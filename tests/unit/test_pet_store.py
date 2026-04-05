"""Tests for pet store CRUD functions."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def pet_dirs(tmp_path: Path):
    """Create pet directory structure and patch DIR constants."""
    species_dir = tmp_path / "species"
    instances_dir = tmp_path / "instances"
    facilities_dir = tmp_path / "facilities"
    facility_types_dir = tmp_path / "facility_types"
    consumables_dir = tmp_path / "consumables"

    for d in (species_dir, instances_dir, facilities_dir, facility_types_dir, consumables_dir):
        d.mkdir()

    patches = {
        "onemancompany.core.store.PET_SPECIES_DIR": species_dir,
        "onemancompany.core.store.PET_INSTANCES_DIR": instances_dir,
        "onemancompany.core.store.PET_FACILITIES_DIR": facilities_dir,
        "onemancompany.core.store.PET_FACILITY_TYPES_DIR": facility_types_dir,
        "onemancompany.core.store.PET_CONSUMABLES_DIR": consumables_dir,
    }
    with patch.dict("os.environ", {}, clear=False):
        stack = []
        for target, value in patches.items():
            p = patch(target, value)
            p.start()
            stack.append(p)
        yield {
            "species_dir": species_dir,
            "instances_dir": instances_dir,
            "facilities_dir": facilities_dir,
            "facility_types_dir": facility_types_dir,
            "consumables_dir": consumables_dir,
        }
        for p in stack:
            p.stop()


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# Species
# ---------------------------------------------------------------------------

VALID_SPECIES = {
    "id": "cat",
    "name": "Office Cat",
    "sprite_dir": "3 Cat",
    "sprite_size": 48,
    "animations": {
        "idle": {"file": "Idle.png", "frames": 4, "speed": 0.2},
        "walking": {"file": "Walk.png", "frames": 6, "speed": 0.15},
    },
    "needs": {
        "hunger": {"decay_rate": 0.1, "critical": 0.2},
    },
    "behaviors": {"wander_radius": 10, "speed": 0.4},
}

INVALID_SPECIES_NO_IDLE = {
    "id": "broken",
    "name": "Broken Pet",
    "sprite_dir": "99 Broken",
    "animations": {
        "walking": {"file": "Walk.png", "frames": 6, "speed": 0.15},
    },
    "needs": {
        "hunger": {"decay_rate": 0.1, "critical": 0.2},
    },
    "behaviors": {},
}


class TestLoadPetSpecies:
    def test_valid_species_loads(self, pet_dirs):
        from onemancompany.core.store import load_pet_species

        _write_yaml(pet_dirs["species_dir"] / "cat.yaml", VALID_SPECIES)
        result = load_pet_species()
        assert "cat" in result
        assert result["cat"].name == "Office Cat"
        assert "idle" in result["cat"].animations

    def test_invalid_species_skipped(self, pet_dirs):
        from onemancompany.core.store import load_pet_species

        _write_yaml(pet_dirs["species_dir"] / "cat.yaml", VALID_SPECIES)
        _write_yaml(pet_dirs["species_dir"] / "broken.yaml", INVALID_SPECIES_NO_IDLE)
        result = load_pet_species()
        assert "cat" in result
        assert "broken" not in result

    def test_empty_dir(self, pet_dirs):
        from onemancompany.core.store import load_pet_species

        result = load_pet_species()
        assert result == {}


# ---------------------------------------------------------------------------
# Pet instances
# ---------------------------------------------------------------------------

SAMPLE_PET = {
    "id": "pet-001",
    "species": "cat",
    "name": "Whiskers",
    "position": [5.0, 3.0],
    "state": "idle",
    "needs": {"hunger": 0.8, "happiness": 0.9, "energy": 1.0},
}


class TestPetInstances:
    def test_save_and_load(self, pet_dirs):
        from onemancompany.core.store import save_pet_sync, load_all_pets_sync

        save_pet_sync("pet-001", SAMPLE_PET)
        result = load_all_pets_sync()
        assert "pet-001" in result
        assert result["pet-001"]["name"] == "Whiskers"

    def test_delete(self, pet_dirs):
        from onemancompany.core.store import save_pet_sync, delete_pet_sync, load_all_pets_sync

        save_pet_sync("pet-001", SAMPLE_PET)
        delete_pet_sync("pet-001")
        result = load_all_pets_sync()
        assert "pet-001" not in result

    def test_delete_nonexistent(self, pet_dirs):
        from onemancompany.core.store import delete_pet_sync

        # Should not raise
        delete_pet_sync("nonexistent")

    def test_load_empty(self, pet_dirs):
        from onemancompany.core.store import load_all_pets_sync

        assert load_all_pets_sync() == {}


# ---------------------------------------------------------------------------
# Facility types
# ---------------------------------------------------------------------------

VALID_FACILITY_TYPE = {
    "id": "food_bowl",
    "name": "Food Bowl",
    "sprite": "food_bowl.png",
    "size": [1, 1],
    "effect": {"hunger": 0.3},
    "cooldown": 120,
}


class TestLoadFacilityTypes:
    def test_valid_type_loads(self, pet_dirs):
        from onemancompany.core.store import load_pet_facility_types

        _write_yaml(pet_dirs["facility_types_dir"] / "food_bowl.yaml", VALID_FACILITY_TYPE)
        result = load_pet_facility_types()
        assert "food_bowl" in result
        assert result["food_bowl"].name == "Food Bowl"
        assert result["food_bowl"].effect == {"hunger": 0.3}

    def test_invalid_type_skipped(self, pet_dirs):
        from onemancompany.core.store import load_pet_facility_types

        _write_yaml(pet_dirs["facility_types_dir"] / "food_bowl.yaml", VALID_FACILITY_TYPE)
        # Missing required fields
        _write_yaml(pet_dirs["facility_types_dir"] / "broken.yaml", {"id": "broken"})
        result = load_pet_facility_types()
        assert "food_bowl" in result
        assert "broken" not in result


# ---------------------------------------------------------------------------
# Facility instances
# ---------------------------------------------------------------------------

SAMPLE_FACILITY = {
    "id": "fac-001",
    "type": "food_bowl",
    "position": [10, 5],
    "placed_by": "ceo",
}


class TestFacilityInstances:
    def test_save_and_load(self, pet_dirs):
        from onemancompany.core.store import save_facility_sync, load_facilities_sync

        save_facility_sync("fac-001", SAMPLE_FACILITY)
        result = load_facilities_sync()
        assert "fac-001" in result
        assert result["fac-001"]["type"] == "food_bowl"

    def test_delete(self, pet_dirs):
        from onemancompany.core.store import save_facility_sync, delete_facility_sync, load_facilities_sync

        save_facility_sync("fac-001", SAMPLE_FACILITY)
        delete_facility_sync("fac-001")
        result = load_facilities_sync()
        assert "fac-001" not in result

    def test_delete_nonexistent(self, pet_dirs):
        from onemancompany.core.store import delete_facility_sync

        # Should not raise
        delete_facility_sync("nonexistent")

    def test_load_empty(self, pet_dirs):
        from onemancompany.core.store import load_facilities_sync

        assert load_facilities_sync() == {}


# ---------------------------------------------------------------------------
# Consumable types
# ---------------------------------------------------------------------------

VALID_CONSUMABLE = {
    "id": "premium_treat",
    "name": "Premium Treat",
    "icon": "\U0001f356",
    "cost": 1,
    "effect": {"hunger": 0.4},
    "target_species": "all",
}

VALID_CONSUMABLE_SPECIES = {
    "id": "catnip_toy",
    "name": "Catnip Toy",
    "icon": "\U0001f9f8",
    "cost": 1,
    "effect": {"happiness": 0.5},
    "target_species": ["cat"],
}


class TestLoadConsumableTypes:
    def test_valid_consumable_loads(self, pet_dirs):
        from onemancompany.core.store import load_consumable_types

        _write_yaml(pet_dirs["consumables_dir"] / "premium_treat.yaml", VALID_CONSUMABLE)
        result = load_consumable_types()
        assert "premium_treat" in result
        assert result["premium_treat"].name == "Premium Treat"
        assert result["premium_treat"].effect == {"hunger": 0.4}
        assert result["premium_treat"].target_species == "all"

    def test_species_restricted_consumable(self, pet_dirs):
        from onemancompany.core.store import load_consumable_types

        _write_yaml(pet_dirs["consumables_dir"] / "catnip_toy.yaml", VALID_CONSUMABLE_SPECIES)
        result = load_consumable_types()
        assert "catnip_toy" in result
        assert result["catnip_toy"].target_species == ["cat"]

    def test_invalid_consumable_skipped(self, pet_dirs):
        from onemancompany.core.store import load_consumable_types

        _write_yaml(pet_dirs["consumables_dir"] / "premium_treat.yaml", VALID_CONSUMABLE)
        # Missing required 'effect' field
        _write_yaml(pet_dirs["consumables_dir"] / "broken.yaml", {"id": "broken", "name": "Broken"})
        result = load_consumable_types()
        assert "premium_treat" in result
        assert "broken" not in result

    def test_empty_dir(self, pet_dirs):
        from onemancompany.core.store import load_consumable_types

        result = load_consumable_types()
        assert result == {}
