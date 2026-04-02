"""Tests for pet system data models and config constants."""
from __future__ import annotations

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Task 1: PetState enum
# ---------------------------------------------------------------------------

class TestPetState:
    def test_enum_values(self):
        from onemancompany.core.pet_models import PetState
        assert PetState.IDLE == "idle"
        assert PetState.WALKING == "walking"
        assert PetState.SLEEPING == "sleeping"
        assert PetState.EATING == "eating"
        assert PetState.PLAYING == "playing"

    def test_is_str_enum(self):
        from onemancompany.core.pet_models import PetState
        assert isinstance(PetState.IDLE, str)


# ---------------------------------------------------------------------------
# Task 1: AnimationDef
# ---------------------------------------------------------------------------

class TestAnimationDef:
    def test_basic_creation(self):
        from onemancompany.core.pet_models import AnimationDef
        anim = AnimationDef(row=0, frames=4, speed=0.2)
        assert anim.row == 0
        assert anim.frames == 4
        assert anim.speed == 0.2

    def test_missing_field_raises(self):
        from onemancompany.core.pet_models import AnimationDef
        with pytest.raises(ValidationError):
            AnimationDef(row=0, frames=4)  # missing speed


# ---------------------------------------------------------------------------
# Task 1: NeedConfig
# ---------------------------------------------------------------------------

class TestNeedConfig:
    def test_basic_creation(self):
        from onemancompany.core.pet_models import NeedConfig
        nc = NeedConfig(decay_rate=0.01, critical=0.2)
        assert nc.decay_rate == 0.01
        assert nc.critical == 0.2

    def test_missing_field_raises(self):
        from onemancompany.core.pet_models import NeedConfig
        with pytest.raises(ValidationError):
            NeedConfig(decay_rate=0.01)


# ---------------------------------------------------------------------------
# Task 1: BehaviorConfig
# ---------------------------------------------------------------------------

class TestBehaviorConfig:
    def test_defaults(self):
        from onemancompany.core.pet_models import BehaviorConfig
        bc = BehaviorConfig()
        assert bc.wander_radius == 8
        assert bc.favorite_spots == []
        assert bc.social == 0.5
        assert bc.speed == 0.5

    def test_custom_values(self):
        from onemancompany.core.pet_models import BehaviorConfig
        bc = BehaviorConfig(wander_radius=12, favorite_spots=["desk_1"], social=0.8, speed=0.3)
        assert bc.wander_radius == 12
        assert bc.favorite_spots == ["desk_1"]


# ---------------------------------------------------------------------------
# Task 1: SpeciesDefinition
# ---------------------------------------------------------------------------

class TestSpeciesDefinition:
    def _make_valid_kwargs(self):
        from onemancompany.core.pet_models import AnimationDef, NeedConfig, BehaviorConfig
        return dict(
            id="cat",
            name="Office Cat",
            sprite_sheet="cat.png",
            animations={
                "idle": AnimationDef(row=0, frames=4, speed=0.2),
                "walking": AnimationDef(row=1, frames=6, speed=0.15),
            },
            needs={"hunger": NeedConfig(decay_rate=0.01, critical=0.2)},
            behaviors=BehaviorConfig(),
        )

    def test_valid_creation(self):
        from onemancompany.core.pet_models import SpeciesDefinition
        sd = SpeciesDefinition(**self._make_valid_kwargs())
        assert sd.id == "cat"
        assert sd.size == [1, 1]

    def test_missing_idle_animation_raises(self):
        from onemancompany.core.pet_models import SpeciesDefinition, AnimationDef
        kwargs = self._make_valid_kwargs()
        kwargs["animations"] = {"walking": AnimationDef(row=1, frames=6, speed=0.15)}
        with pytest.raises(ValidationError, match="idle"):
            SpeciesDefinition(**kwargs)

    def test_empty_needs_raises(self):
        from onemancompany.core.pet_models import SpeciesDefinition
        kwargs = self._make_valid_kwargs()
        kwargs["needs"] = {}
        with pytest.raises(ValidationError, match="needs"):
            SpeciesDefinition(**kwargs)


# ---------------------------------------------------------------------------
# Task 1: PetInstance
# ---------------------------------------------------------------------------

class TestPetInstance:
    def test_defaults(self):
        from onemancompany.core.pet_models import PetInstance, PetState
        pet = PetInstance(id="pet_001", species="cat", position=[5.0, 3.0])
        assert pet.state == PetState.IDLE
        assert pet.name is None
        assert pet.owner is None
        assert pet.target_position is None
        assert pet.needs == {"hunger": 1.0, "happiness": 1.0, "energy": 1.0}
        assert pet.adopted_at is None
        assert pet.spawned_at is None

    def test_to_dict_converts_state_to_string(self):
        from onemancompany.core.pet_models import PetInstance
        pet = PetInstance(id="pet_001", species="cat", position=[5.0, 3.0])
        d = pet.to_dict()
        assert isinstance(d["state"], str)
        assert d["state"] == "idle"
        assert d["id"] == "pet_001"
        assert d["position"] == [5.0, 3.0]

    def test_custom_values(self):
        from onemancompany.core.pet_models import PetInstance, PetState
        pet = PetInstance(
            id="pet_002",
            species="dog",
            name="Buddy",
            owner="emp_001",
            position=[10.0, 7.0],
            state=PetState.PLAYING,
            target_position=[12.0, 8.0],
            needs={"hunger": 0.5, "happiness": 0.9, "energy": 0.3},
        )
        assert pet.name == "Buddy"
        assert pet.state == PetState.PLAYING


# ---------------------------------------------------------------------------
# Task 1: FacilityType
# ---------------------------------------------------------------------------

class TestFacilityType:
    def test_basic_creation(self):
        from onemancompany.core.pet_models import FacilityType
        ft = FacilityType(id="food_bowl", name="Food Bowl", sprite="bowl.png", effect={"hunger": 0.5})
        assert ft.id == "food_bowl"
        assert ft.size == [1, 1]
        assert ft.cooldown == 60

    def test_custom_cooldown(self):
        from onemancompany.core.pet_models import FacilityType
        ft = FacilityType(id="bed", name="Pet Bed", sprite="bed.png", effect={"energy": 0.8}, cooldown=120)
        assert ft.cooldown == 120


# ---------------------------------------------------------------------------
# Task 1: FacilityInstance
# ---------------------------------------------------------------------------

class TestFacilityInstance:
    def test_basic_creation(self):
        from onemancompany.core.pet_models import FacilityInstance
        fi = FacilityInstance(id="fac_001", type="food_bowl", position=[3, 4], placed_by="ceo")
        assert fi.id == "fac_001"
        assert fi.position == [3, 4]


# ---------------------------------------------------------------------------
# Task 2: Config constants
# ---------------------------------------------------------------------------

class TestPetConfig:
    def test_dirty_category_pets_exists(self):
        from onemancompany.core.config import DirtyCategory
        assert DirtyCategory.PETS == "pets"

    def test_pet_directory_constants_exist(self):
        from onemancompany.core import config
        assert hasattr(config, "PETS_DIR")
        assert hasattr(config, "PET_SPECIES_DIR")
        assert hasattr(config, "PET_INSTANCES_DIR")
        assert hasattr(config, "PET_FACILITIES_DIR")
        assert hasattr(config, "PET_FACILITY_TYPES_DIR")

    def test_pet_dirs_under_company(self):
        from onemancompany.core.config import (
            COMPANY_DIR, PETS_DIR, PET_SPECIES_DIR,
            PET_INSTANCES_DIR, PET_FACILITIES_DIR, PET_FACILITY_TYPES_DIR,
        )
        assert PETS_DIR == COMPANY_DIR / "pets"
        assert PET_SPECIES_DIR == PETS_DIR / "species"
        assert PET_INSTANCES_DIR == PETS_DIR / "instances"
        assert PET_FACILITIES_DIR == PETS_DIR / "facilities"
        assert PET_FACILITY_TYPES_DIR == PETS_DIR / "facility_types"
