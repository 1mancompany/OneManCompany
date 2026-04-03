"""Tests for pet API endpoints."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def mock_pet_engine():
    engine = MagicMock()
    engine.get_all_state.return_value = {
        "pets": [{"id": "pet_001", "species": "cat", "name": "小橘", "position": [5, 7], "state": "idle", "owner": None, "needs": {"hunger": 0.8}}],
        "facilities": [],
        "species": {"cat": {"id": "cat", "name": "猫"}},
    }
    engine.adopt_pet.return_value = True
    engine.interact_pet.return_value = True
    engine.rename_pet.return_value = True
    engine.remove_facility.return_value = True
    # For adopt_pet — need .pets dict
    from onemancompany.core.pet_models import PetInstance
    mock_pet = PetInstance(id="pet_001", species="cat", position=[5, 7], name="小橘", owner="00001")
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
        assert data["pets"][0]["name"] == "小橘"


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
            resp = client.post("/api/pets/pet_001/name", json={"name": "大橘"})
        assert resp.status_code == 200
        mock_pet_engine.rename_pet.assert_called_once_with("pet_001", "大橘")
