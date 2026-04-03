"""Tests for pet API endpoints."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def mock_pet_engine():
    engine = MagicMock()
    engine.get_all_state.return_value = {
        "pets": [{"id": "pet_001", "species": "cat", "name": "Mochi", "position": [5, 7], "state": "idle", "owner": None, "needs": {"hunger": 0.8}}],
        "facilities": [],
        "species": {"cat": {"id": "cat", "name": "Cat"}},
    }
    engine.adopt_pet.return_value = True
    engine.interact_pet.return_value = True
    engine.rename_pet.return_value = True
    engine.remove_facility.return_value = True
    # For adopt_pet — need .pets dict
    from onemancompany.core.pet_models import PetInstance
    mock_pet = PetInstance(id="pet_001", species="cat", position=[5, 7], name="Mochi", owner="00001")
    engine.pets = {"pet_001": mock_pet}
    return engine


@pytest.fixture
def client(mock_pet_engine):
    # Must patch BEFORE importing/using the router
    with patch("onemancompany.api.routes._pet_engine", mock_pet_engine), \
         patch("onemancompany.api.routes.OFFICE_VIBES_ENABLED", True):
        from onemancompany.api.routes import router
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app)


class TestGetPets:
    def test_returns_pet_state(self, client, mock_pet_engine):
        resp = client.get("/api/pets")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["pets"]) == 1
        assert data["pets"][0]["name"] == "Mochi"


class TestAdoptPet:
    def test_adopt_success(self, client, mock_pet_engine):
        with patch("onemancompany.core.store.save_pet_sync"), \
             patch("onemancompany.core.store.mark_dirty"):
            resp = client.post("/api/pets/pet_001/adopt")
        assert resp.status_code == 200


class TestInteractPet:
    def test_interact_pet(self, client, mock_pet_engine):
        with patch("onemancompany.core.store.mark_dirty"):
            resp = client.post("/api/pets/pet_001/interact", json={"action": "pet"})
        assert resp.status_code == 200
        mock_pet_engine.interact_pet.assert_called_once_with("pet_001", "pet")


class TestRenamePet:
    def test_rename_pet(self, client, mock_pet_engine):
        with patch("onemancompany.core.store.save_pet_sync"), \
             patch("onemancompany.core.store.mark_dirty"):
            resp = client.post("/api/pets/pet_001/name", json={"name": "Chonk"})
        assert resp.status_code == 200
        mock_pet_engine.rename_pet.assert_called_once_with("pet_001", "Chonk")


class TestUseItem:
    def test_use_item_success(self, client, mock_pet_engine):
        from onemancompany.core.pet_models import ConsumableType
        mock_pet_engine._consumable_types = {
            "premium_treat": ConsumableType(
                id="premium_treat", name="Premium Treat",
                cost=1, effect={"hunger": 0.4},
            )
        }
        mock_pet_engine.can_use_consumable.return_value = True
        mock_pet_engine.spend_tokens.return_value = True
        mock_pet_engine.use_consumable.return_value = True
        with patch("onemancompany.core.store.mark_dirty"):
            resp = client.post("/api/pets/pet_001/use-item", json={"item_id": "premium_treat"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        mock_pet_engine.can_use_consumable.assert_called_once_with("pet_001", "premium_treat")
        mock_pet_engine.spend_tokens.assert_called_once_with(1)
        mock_pet_engine.use_consumable.assert_called_once_with("pet_001", "premium_treat")

    def test_use_item_unknown(self, client, mock_pet_engine):
        mock_pet_engine._consumable_types = {}
        resp = client.post("/api/pets/pet_001/use-item", json={"item_id": "nonexistent"})
        assert resp.status_code == 400

    def test_use_item_not_enough_tokens(self, client, mock_pet_engine):
        from onemancompany.core.pet_models import ConsumableType
        mock_pet_engine._consumable_types = {
            "premium_treat": ConsumableType(
                id="premium_treat", name="Premium Treat",
                cost=1, effect={"hunger": 0.4},
            )
        }
        mock_pet_engine.can_use_consumable.return_value = True
        mock_pet_engine.spend_tokens.return_value = False
        resp = client.post("/api/pets/pet_001/use-item", json={"item_id": "premium_treat"})
        assert resp.status_code == 400
        assert "tokens" in resp.json()["detail"].lower()

    def test_use_item_species_mismatch(self, client, mock_pet_engine):
        """Species mismatch is caught before tokens are spent."""
        from onemancompany.core.pet_models import ConsumableType
        mock_pet_engine._consumable_types = {
            "catnip_toy": ConsumableType(
                id="catnip_toy", name="Catnip Toy",
                cost=1, effect={"happiness": 0.5}, target_species=["cat"],
            )
        }
        mock_pet_engine.can_use_consumable.return_value = False  # species mismatch
        resp = client.post("/api/pets/pet_001/use-item", json={"item_id": "catnip_toy"})
        assert resp.status_code == 400
        assert "compatible" in resp.json()["detail"].lower()
        # Tokens should never have been spent
        mock_pet_engine.spend_tokens.assert_not_called()


class TestTranslate:
    def test_translate_single_pet(self, client, mock_pet_engine):
        mock_pet_engine.pets = {
            "pet_001": MagicMock(
                id="pet_001", name="Mochi", species="cat",
                current_speech="Mew naka~", current_mood="hungry",
                speech_translation="I'm getting hungry...",
            ),
        }
        resp = client.post("/api/pets/translate", json={"pet_id": "pet_001"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["translations"]) == 1
        assert data["translations"][0]["translation"] == "I'm getting hungry..."

    def test_translate_all_pets(self, client, mock_pet_engine):
        mock_pet_engine.pets = {
            "pet_001": MagicMock(
                id="pet_001", name="Mochi", species="cat",
                current_speech="Mew naka~", current_mood="hungry",
                speech_translation="I'm getting hungry...",
            ),
            "pet_002": MagicMock(
                id="pet_002", name="Rex", species="dog",
                current_speech=None, current_mood=None,
                speech_translation=None,
            ),
        }
        resp = client.post("/api/pets/translate", json={})
        assert resp.status_code == 200
        data = resp.json()
        # Only pet_001 has speech
        assert len(data["translations"]) == 1

    def test_translate_no_speech(self, client, mock_pet_engine):
        mock_pet_engine.pets = {
            "pet_001": MagicMock(
                id="pet_001", name="Mochi", species="cat",
                current_speech=None, current_mood=None,
                speech_translation=None,
            ),
        }
        resp = client.post("/api/pets/translate", json={"pet_id": "pet_001"})
        assert resp.status_code == 200
        assert len(resp.json()["translations"]) == 0


class TestGetPetsSpeech:
    def test_speech_endpoint(self, client, mock_pet_engine):
        mock_pet_engine.pets = {
            "pet_001": MagicMock(
                id="pet_001", name="Mochi", species="cat",
                current_speech="Mew naka~", current_mood="hungry",
                speech_translation="I'm getting hungry...",
            ),
        }
        resp = client.get("/api/pets/speech")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["speeches"]) == 1
        assert data["speeches"][0]["speech"] == "Mew naka~"
