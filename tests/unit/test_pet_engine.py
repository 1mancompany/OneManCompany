"""Tests for PetEngine — behavior tree, needs decay, stray lifecycle, token economy."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from onemancompany.core.pet_models import (
    AnimationDef,
    BehaviorConfig,
    ConsumableType,
    FacilityInstance,
    FacilityType,
    NeedConfig,
    PetInstance,
    PetState,
    SpeciesDefinition,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_species(
    sid: str = "cat",
    speed: float = 1.0,
    wander_radius: int = 5,
    social: float = 0.3,
    needs: dict | None = None,
) -> SpeciesDefinition:
    default_needs = {
        "energy": NeedConfig(decay_rate=0.1, critical=0.2),
        "hunger": NeedConfig(decay_rate=0.08, critical=0.2),
        "happiness": NeedConfig(decay_rate=0.05, critical=0.2),
    }
    return SpeciesDefinition(
        id=sid,
        name=sid.title(),
        sprite_sheet=f"{sid}.png",
        animations={"idle": AnimationDef(row=0, frames=4, speed=0.2)},
        needs=needs or default_needs,
        behaviors=BehaviorConfig(speed=speed, wander_radius=wander_radius, social=social),
    )


def _make_pet(
    pid: str = "pet_001",
    species: str = "cat",
    owner: str | None = "emp_001",
    position: list[float] | None = None,
    state: PetState = PetState.IDLE,
    needs: dict[str, float] | None = None,
    spawned_at: str | None = None,
) -> PetInstance:
    return PetInstance(
        id=pid,
        species=species,
        owner=owner,
        position=position or [5.0, 5.0],
        state=state,
        needs=needs or {"energy": 1.0, "hunger": 1.0, "happiness": 1.0},
        spawned_at=spawned_at,
    )


def _make_engine(pets=None, species=None, facilities=None, facility_types=None, consumable_types=None):
    from onemancompany.core.pet_engine import PetEngine

    sp = species or {"cat": _make_species()}
    p = pets or {}
    ft = facility_types or {}
    f = facilities or {}
    ct = consumable_types or {}
    return PetEngine(
        species=sp,
        pets=p,
        facility_types=ft,
        facilities=f,
        consumable_types=ct,
        office_cols=20,
        office_rows=18,
    )


# ---------------------------------------------------------------------------
# Needs decay
# ---------------------------------------------------------------------------

class TestNeedsDecay:
    def test_decay_reduces_values(self):
        pet = _make_pet(needs={"energy": 0.5, "hunger": 0.6, "happiness": 0.8})
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        engine._decay_needs(pet, species)

        assert pet.needs["energy"] == pytest.approx(0.4)  # 0.5 - 0.1
        assert pet.needs["hunger"] == pytest.approx(0.52)  # 0.6 - 0.08
        assert pet.needs["happiness"] == pytest.approx(0.75)  # 0.8 - 0.05

    def test_decay_clamps_at_zero(self):
        pet = _make_pet(needs={"energy": 0.05, "hunger": 0.0, "happiness": 0.02})
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        engine._decay_needs(pet, species)

        assert pet.needs["energy"] == 0.0
        assert pet.needs["hunger"] == 0.0
        assert pet.needs["happiness"] == 0.0


# ---------------------------------------------------------------------------
# Behavior decision tree
# ---------------------------------------------------------------------------

class TestBehaviorTree:
    def test_low_energy_triggers_sleep(self):
        """Energy below critical -> SLEEPING."""
        pet = _make_pet(needs={"energy": 0.1, "hunger": 1.0, "happiness": 1.0})
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        engine._decide_behavior(pet, species)

        assert pet.state == PetState.SLEEPING

    def test_low_hunger_with_food_bowl_triggers_eat(self):
        """Hunger below critical + food_bowl facility -> walk to bowl -> eat."""
        pet = _make_pet(needs={"energy": 1.0, "hunger": 0.1, "happiness": 1.0})
        species = _make_species()
        bowl_type = FacilityType(
            id="food_bowl", name="Food Bowl", sprite="bowl.png", effect={"hunger": 0.1}
        )
        bowl = FacilityInstance(
            id="fac_001", type="food_bowl", position=[10, 10], placed_by="ceo"
        )
        engine = _make_engine(
            pets={"pet_001": pet},
            species={"cat": species},
            facility_types={"food_bowl": bowl_type},
            facilities={"fac_001": bowl},
        )

        engine._decide_behavior(pet, species)

        # Should walk toward bowl or start eating (if already close)
        assert pet.state in (PetState.WALKING, PetState.EATING)
        if pet.state == PetState.WALKING:
            assert pet.target_position is not None

    def test_low_happiness_with_toy_triggers_play(self):
        """Happiness below critical + toy_ball -> walk to toy -> play."""
        pet = _make_pet(needs={"energy": 1.0, "hunger": 1.0, "happiness": 0.1})
        species = _make_species()
        toy_type = FacilityType(
            id="toy_ball", name="Toy Ball", sprite="toy.png", effect={"happiness": 0.1}
        )
        toy = FacilityInstance(
            id="fac_002", type="toy_ball", position=[8, 8], placed_by="ceo"
        )
        engine = _make_engine(
            pets={"pet_001": pet},
            species={"cat": species},
            facility_types={"toy_ball": toy_type},
            facilities={"fac_002": toy},
        )

        engine._decide_behavior(pet, species)

        assert pet.state in (PetState.WALKING, PetState.PLAYING)

    @patch("onemancompany.core.pet_engine.random")
    def test_all_needs_ok_triggers_wander(self, mock_random):
        """All needs fine, low social roll -> wander."""
        mock_random.random.return_value = 0.99  # above social threshold
        mock_random.uniform.side_effect = [3.0, 2.0]  # dx, dy offsets
        pet = _make_pet(needs={"energy": 1.0, "hunger": 1.0, "happiness": 1.0})
        species = _make_species(social=0.3)
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        engine._decide_behavior(pet, species)

        assert pet.state == PetState.WALKING
        assert pet.target_position is not None


# ---------------------------------------------------------------------------
# Movement
# ---------------------------------------------------------------------------

class TestMovement:
    def test_moves_toward_target(self):
        pet = _make_pet(position=[0.0, 0.0])
        pet.state = PetState.WALKING
        pet.target_position = [10.0, 0.0]
        species = _make_species(speed=2.0)
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        engine._move_pet(pet, species)

        # Should have moved 2 tiles toward target
        assert pet.position[0] == pytest.approx(2.0)
        assert pet.position[1] == pytest.approx(0.0)
        assert pet.target_position is not None  # not arrived yet

    def test_arrives_and_clears_target(self):
        pet = _make_pet(position=[9.0, 0.0])
        pet.state = PetState.WALKING
        pet.target_position = [10.0, 0.0]
        species = _make_species(speed=2.0)
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        engine._move_pet(pet, species)

        # dist=1 <= speed=2, so snap to target
        assert pet.position[0] == pytest.approx(10.0)
        assert pet.target_position is None


# ---------------------------------------------------------------------------
# Stray spawning
# ---------------------------------------------------------------------------

class TestStraySpawn:
    @patch("onemancompany.core.pet_engine.random")
    def test_spawns_stray_under_cap(self, mock_random):
        """Random < STRAY_SPAWN_CHANCE and under MAX_PETS -> spawn."""
        mock_random.random.return_value = 0.01  # below 0.05
        mock_random.choice.return_value = "cat"
        mock_random.randint.return_value = 5  # x position on edge
        species = _make_species()
        engine = _make_engine(species={"cat": species})

        engine._try_spawn_stray()

        assert len(engine._pets) == 1
        pet = list(engine._pets.values())[0]
        assert pet.owner is None
        assert pet.position[1] == 0.0  # spawns at top edge

    @patch("onemancompany.core.pet_engine.random")
    def test_no_spawn_at_cap(self, mock_random):
        """At MAX_PETS -> no spawn regardless of random."""
        mock_random.random.return_value = 0.01
        species = _make_species()
        pets = {
            f"pet_{i:03d}": _make_pet(pid=f"pet_{i:03d}")
            for i in range(3)
        }
        engine = _make_engine(pets=pets, species={"cat": species})

        engine._try_spawn_stray()

        assert len(engine._pets) == 3  # unchanged


# ---------------------------------------------------------------------------
# Stray expiry
# ---------------------------------------------------------------------------

class TestStrayExpiry:
    def test_old_stray_removed(self):
        """Stray older than STRAY_TIMEOUT_SECONDS -> removed."""
        old_time = "2020-01-01T00:00:00+00:00"
        pet = _make_pet(pid="stray_001", owner=None, spawned_at=old_time)
        engine = _make_engine(pets={"stray_001": pet})

        engine._expire_strays()

        assert "stray_001" not in engine._pets
        assert "stray_001" in engine._deleted_pets

    def test_owned_pet_not_expired(self):
        """Owned pets are never expired regardless of age."""
        old_time = "2020-01-01T00:00:00+00:00"
        pet = _make_pet(pid="pet_001", owner="emp_001", spawned_at=old_time)
        engine = _make_engine(pets={"pet_001": pet})

        engine._expire_strays()

        assert "pet_001" in engine._pets


# ---------------------------------------------------------------------------
# Recovery states (sleeping / eating / playing)
# ---------------------------------------------------------------------------

class TestRecoveryStates:
    def test_sleeping_recovers_energy(self):
        pet = _make_pet(needs={"energy": 0.5, "hunger": 1.0, "happiness": 1.0})
        pet.state = PetState.SLEEPING
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        engine._handle_sleeping(pet)

        assert pet.needs["energy"] == pytest.approx(0.55)
        assert pet.state == PetState.SLEEPING  # not yet at 0.8

    def test_sleeping_switches_to_idle_at_threshold(self):
        pet = _make_pet(needs={"energy": 0.79, "hunger": 1.0, "happiness": 1.0})
        pet.state = PetState.SLEEPING
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        engine._handle_sleeping(pet)

        assert pet.needs["energy"] >= 0.8
        assert pet.state == PetState.IDLE

    def test_eating_recovers_hunger(self):
        pet = _make_pet(needs={"energy": 1.0, "hunger": 0.5, "happiness": 1.0})
        pet.state = PetState.EATING
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        engine._handle_eating(pet)

        assert pet.needs["hunger"] == pytest.approx(0.55)

    def test_playing_recovers_happiness(self):
        pet = _make_pet(needs={"energy": 1.0, "hunger": 1.0, "happiness": 0.5})
        pet.state = PetState.PLAYING
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        engine._handle_playing(pet)

        assert pet.needs["happiness"] == pytest.approx(0.55)


# ---------------------------------------------------------------------------
# Token economy
# ---------------------------------------------------------------------------

class TestTokenEconomy:
    """Token economy: earn tokens from completed projects, spend on facilities."""

    def _make_wallet(self, tokens=0, projects_counted=0, tokens_spent=0):
        return {"tokens": tokens, "projects_counted": projects_counted, "tokens_spent": tokens_spent}

    @patch("onemancompany.core.pet_engine.load_pet_wallet")
    @patch("onemancompany.core.pet_engine.save_pet_wallet")
    def test_sync_tokens_grants_correct_amount(self, mock_save, mock_load):
        """15 completed projects = 5 tokens."""
        mock_load.return_value = self._make_wallet(tokens=0, projects_counted=0, tokens_spent=0)
        engine = _make_engine()

        result = engine.sync_tokens(15)

        saved = mock_save.call_args[0][0]
        assert saved["tokens"] == 5
        assert saved["projects_counted"] == 15
        assert result == 5  # 5 tokens - 0 spent

    @patch("onemancompany.core.pet_engine.load_pet_wallet")
    @patch("onemancompany.core.pet_engine.save_pet_wallet")
    def test_sync_tokens_incremental(self, mock_save, mock_load):
        """Going from 3 -> 6 projects grants 1 more token."""
        mock_load.return_value = self._make_wallet(tokens=1, projects_counted=3, tokens_spent=0)
        engine = _make_engine()

        result = engine.sync_tokens(6)

        saved = mock_save.call_args[0][0]
        assert saved["tokens"] == 2  # was 1, earned 1 more
        assert saved["projects_counted"] == 6
        assert result == 2  # 2 tokens - 0 spent

    @patch("onemancompany.core.pet_engine.load_pet_wallet")
    @patch("onemancompany.core.pet_engine.save_pet_wallet")
    def test_sync_tokens_no_grant_if_not_enough(self, mock_save, mock_load):
        """Going from 3 -> 5 projects (still less than 6) grants 0."""
        mock_load.return_value = self._make_wallet(tokens=1, projects_counted=3, tokens_spent=0)
        engine = _make_engine()

        result = engine.sync_tokens(5)

        saved = mock_save.call_args[0][0]
        assert saved["tokens"] == 1  # unchanged
        assert saved["projects_counted"] == 5
        assert result == 1

    @patch("onemancompany.core.pet_engine.load_pet_wallet")
    @patch("onemancompany.core.pet_engine.save_pet_wallet")
    def test_spend_tokens_success(self, mock_save, mock_load):
        """Spend when balance sufficient returns True."""
        mock_load.return_value = self._make_wallet(tokens=5, projects_counted=15, tokens_spent=2)
        engine = _make_engine()

        result = engine.spend_tokens(2)

        assert result is True
        saved = mock_save.call_args[0][0]
        assert saved["tokens_spent"] == 4

    @patch("onemancompany.core.pet_engine.load_pet_wallet")
    @patch("onemancompany.core.pet_engine.save_pet_wallet")
    def test_spend_tokens_insufficient(self, mock_save, mock_load):
        """Spend when balance insufficient returns False, no save."""
        mock_load.return_value = self._make_wallet(tokens=5, projects_counted=15, tokens_spent=5)
        engine = _make_engine()

        result = engine.spend_tokens(1)

        assert result is False
        mock_save.assert_not_called()

    @patch("onemancompany.core.pet_engine.load_pet_wallet")
    def test_get_token_balance(self, mock_load):
        """Balance = tokens - tokens_spent."""
        mock_load.return_value = self._make_wallet(tokens=5, projects_counted=15, tokens_spent=2)
        engine = _make_engine()

        assert engine.get_token_balance() == 3


# ---------------------------------------------------------------------------
# Consumable items
# ---------------------------------------------------------------------------

class TestUseConsumable:
    def _make_treat(self):
        return ConsumableType(
            id="premium_treat", name="Premium Treat", icon="\U0001f356",
            cost=1, effect={"hunger": 0.4}, target_species="all",
        )

    def _make_catnip(self):
        return ConsumableType(
            id="catnip_toy", name="Catnip Toy", icon="\U0001f9f8",
            cost=1, effect={"happiness": 0.5}, target_species=["cat"],
        )

    def test_use_consumable_success(self):
        """Using a treat on a pet should increase the target need."""
        pet = _make_pet(needs={"energy": 1.0, "hunger": 0.5, "happiness": 1.0})
        treat = self._make_treat()
        engine = _make_engine(
            pets={"pet_001": pet},
            consumable_types={"premium_treat": treat},
        )

        result = engine.use_consumable("pet_001", "premium_treat")

        assert result is True
        assert pet.needs["hunger"] == pytest.approx(0.9)
        assert "pet_001" in engine.dirty_pets

    def test_use_consumable_clamps_at_1(self):
        """Need should not exceed 1.0."""
        pet = _make_pet(needs={"energy": 1.0, "hunger": 0.8, "happiness": 1.0})
        treat = self._make_treat()
        engine = _make_engine(
            pets={"pet_001": pet},
            consumable_types={"premium_treat": treat},
        )

        engine.use_consumable("pet_001", "premium_treat")

        assert pet.needs["hunger"] == 1.0

    def test_use_consumable_species_match(self):
        """Cat-only item should work on a cat."""
        pet = _make_pet(species="cat", needs={"energy": 1.0, "hunger": 1.0, "happiness": 0.3})
        catnip = self._make_catnip()
        engine = _make_engine(
            pets={"pet_001": pet},
            consumable_types={"catnip_toy": catnip},
        )

        result = engine.use_consumable("pet_001", "catnip_toy")

        assert result is True
        assert pet.needs["happiness"] == pytest.approx(0.8)

    def test_use_consumable_species_mismatch(self):
        """Cat-only item should fail on a dog."""
        pet = _make_pet(species="dog", needs={"energy": 1.0, "hunger": 1.0, "happiness": 0.3})
        catnip = self._make_catnip()
        engine = _make_engine(
            pets={"pet_001": pet},
            species={"dog": _make_species(sid="dog")},
            consumable_types={"catnip_toy": catnip},
        )

        result = engine.use_consumable("pet_001", "catnip_toy")

        assert result is False
        assert pet.needs["happiness"] == pytest.approx(0.3)  # unchanged

    def test_use_consumable_unknown_item(self):
        """Unknown consumable ID returns False."""
        pet = _make_pet()
        engine = _make_engine(pets={"pet_001": pet})

        result = engine.use_consumable("pet_001", "nonexistent")

        assert result is False

    def test_use_consumable_unknown_pet(self):
        """Unknown pet ID returns False."""
        treat = self._make_treat()
        engine = _make_engine(consumable_types={"premium_treat": treat})

        result = engine.use_consumable("nonexistent", "premium_treat")

        assert result is False

    def test_use_consumable_negative_effect(self):
        """An item with negative effect (e.g. fetch ball energy -0.1)."""
        pet = _make_pet(needs={"energy": 0.5, "hunger": 1.0, "happiness": 0.5})
        ball = ConsumableType(
            id="fetch_ball", name="Fetch Ball", cost=1,
            effect={"happiness": 0.3, "energy": -0.1}, target_species="all",
        )
        engine = _make_engine(
            pets={"pet_001": pet},
            consumable_types={"fetch_ball": ball},
        )

        engine.use_consumable("pet_001", "fetch_ball")

        assert pet.needs["happiness"] == pytest.approx(0.8)
        assert pet.needs["energy"] == pytest.approx(0.4)

    def test_consumables_in_get_all_state(self):
        """get_all_state should include consumables."""
        treat = self._make_treat()
        engine = _make_engine(consumable_types={"premium_treat": treat})

        state = engine.get_all_state()

        assert "consumables" in state
        assert "premium_treat" in state["consumables"]
        assert state["consumables"]["premium_treat"]["cost"] == 1


# ---------------------------------------------------------------------------
# can_use_consumable (pre-check without side effects)
# ---------------------------------------------------------------------------

class TestCanUseConsumable:
    def _make_treat(self):
        return ConsumableType(
            id="premium_treat", name="Premium Treat", icon="\U0001f356",
            cost=1, effect={"hunger": 0.4}, target_species="all",
        )

    def _make_catnip(self):
        return ConsumableType(
            id="catnip_toy", name="Catnip Toy", icon="\U0001f9f8",
            cost=1, effect={"happiness": 0.5}, target_species=["cat"],
        )

    def test_can_use_universal_item(self):
        """Universal item (target_species='all') passes for any pet."""
        pet = _make_pet(species="dog")
        treat = self._make_treat()
        engine = _make_engine(
            pets={"pet_001": pet},
            species={"dog": _make_species(sid="dog")},
            consumable_types={"premium_treat": treat},
        )
        assert engine.can_use_consumable("pet_001", "premium_treat") is True

    def test_can_use_species_specific_match(self):
        """Cat-only item passes for cat."""
        pet = _make_pet(species="cat")
        catnip = self._make_catnip()
        engine = _make_engine(
            pets={"pet_001": pet},
            consumable_types={"catnip_toy": catnip},
        )
        assert engine.can_use_consumable("pet_001", "catnip_toy") is True

    def test_cannot_use_species_mismatch(self):
        """Cat-only item fails for dog."""
        pet = _make_pet(species="dog")
        catnip = self._make_catnip()
        engine = _make_engine(
            pets={"pet_001": pet},
            species={"dog": _make_species(sid="dog")},
            consumable_types={"catnip_toy": catnip},
        )
        assert engine.can_use_consumable("pet_001", "catnip_toy") is False

    def test_cannot_use_unknown_pet(self):
        treat = self._make_treat()
        engine = _make_engine(consumable_types={"premium_treat": treat})
        assert engine.can_use_consumable("nonexistent", "premium_treat") is False

    def test_cannot_use_unknown_item(self):
        pet = _make_pet()
        engine = _make_engine(pets={"pet_001": pet})
        assert engine.can_use_consumable("pet_001", "nonexistent") is False

    def test_does_not_apply_effects(self):
        """can_use_consumable must not mutate pet needs."""
        pet = _make_pet(needs={"energy": 1.0, "hunger": 0.5, "happiness": 1.0})
        treat = self._make_treat()
        engine = _make_engine(
            pets={"pet_001": pet},
            consumable_types={"premium_treat": treat},
        )
        engine.can_use_consumable("pet_001", "premium_treat")
        assert pet.needs["hunger"] == pytest.approx(0.5)  # unchanged


# ---------------------------------------------------------------------------
# refund_tokens
# ---------------------------------------------------------------------------

class TestRefundTokens:
    @patch("onemancompany.core.pet_engine.load_pet_wallet")
    @patch("onemancompany.core.pet_engine.save_pet_wallet")
    def test_refund_decrements_tokens_spent(self, mock_save, mock_load):
        mock_load.return_value = {"tokens": 5, "projects_counted": 15, "tokens_spent": 3}
        engine = _make_engine()

        engine.refund_tokens(2)

        saved = mock_save.call_args[0][0]
        assert saved["tokens_spent"] == 1

    @patch("onemancompany.core.pet_engine.load_pet_wallet")
    @patch("onemancompany.core.pet_engine.save_pet_wallet")
    def test_refund_clamps_at_zero(self, mock_save, mock_load):
        """Refunding more than spent clamps tokens_spent at 0."""
        mock_load.return_value = {"tokens": 5, "projects_counted": 15, "tokens_spent": 1}
        engine = _make_engine()

        engine.refund_tokens(5)

        saved = mock_save.call_args[0][0]
        assert saved["tokens_spent"] == 0


# ---------------------------------------------------------------------------
# Speech cleared on engine init
# ---------------------------------------------------------------------------

class TestSpeechClearedOnInit:
    def test_speech_fields_cleared_on_init(self):
        """Engine constructor clears stale speech from loaded pets."""
        pet = _make_pet()
        pet.current_speech = "Stale speech"
        pet.current_mood = "happy"
        pet.speech_translation = "Old translation"
        pet.speech_tick = 42

        engine = _make_engine(pets={"pet_001": pet})

        assert pet.current_speech is None
        assert pet.current_mood is None
        assert pet.speech_translation is None
        assert pet.speech_tick == 0


# ---------------------------------------------------------------------------
# Pet speech generation
# ---------------------------------------------------------------------------

class TestSpeechGeneration:
    def test_determine_mood_hungry(self):
        """Lowest need < 0.3 and it's hunger -> mood is hungry."""
        from onemancompany.core.pet_engine import PetEngine
        pet = _make_pet(needs={"energy": 0.8, "hunger": 0.1, "happiness": 0.8})
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        speech = engine._generate_speech(pet, species)

        assert speech is not None
        assert speech["mood"] == "hungry"
        assert isinstance(speech["text"], str)
        assert len(speech["text"]) > 0
        assert isinstance(speech["translation"], str)

    def test_determine_mood_tired(self):
        """Lowest need < 0.3 and it's energy -> mood is tired."""
        pet = _make_pet(needs={"energy": 0.1, "hunger": 0.8, "happiness": 0.8})
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        speech = engine._generate_speech(pet, species)

        assert speech["mood"] == "tired"

    def test_determine_mood_lonely(self):
        """Lowest need < 0.3 and it's happiness -> mood is lonely."""
        pet = _make_pet(needs={"energy": 0.8, "hunger": 0.8, "happiness": 0.1})
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        speech = engine._generate_speech(pet, species)

        assert speech["mood"] == "lonely"

    def test_determine_mood_content(self):
        """All needs > 0.7 -> content."""
        pet = _make_pet(needs={"energy": 0.9, "hunger": 0.9, "happiness": 0.9})
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        speech = engine._generate_speech(pet, species)

        assert speech["mood"] == "content"

    def test_determine_mood_playful_or_happy(self):
        """Needs between 0.3-0.7 -> playful or happy."""
        pet = _make_pet(needs={"energy": 0.5, "hunger": 0.5, "happiness": 0.5})
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        speech = engine._generate_speech(pet, species)

        assert speech["mood"] in ("playful", "happy")

    def test_speech_text_contains_simlish_words(self):
        """Speech text should contain words from SIMLISH_WORDS pools."""
        from onemancompany.core.pet_engine import SIMLISH_WORDS
        pet = _make_pet(needs={"energy": 0.1, "hunger": 0.8, "happiness": 0.8})
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        speech = engine._generate_speech(pet, species)
        mood = speech["mood"]

        # At least one word from the mood pool should appear
        text_lower = speech["text"].lower()
        pool = SIMLISH_WORDS[mood]
        assert any(w in text_lower for w in pool), f"No simlish word from {pool} found in '{speech['text']}'"

    def test_speech_translation_is_from_mood_pool(self):
        """Translation should be from MOOD_TRANSLATIONS for the determined mood."""
        from onemancompany.core.pet_engine import MOOD_TRANSLATIONS
        pet = _make_pet(needs={"energy": 0.1, "hunger": 0.8, "happiness": 0.8})
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        speech = engine._generate_speech(pet, species)

        assert speech["translation"] in MOOD_TRANSLATIONS[speech["mood"]]


class TestSpeechTiming:
    """Test speech appears and expires during tick()."""

    @patch("onemancompany.core.pet_engine.load_pet_wallet")
    @patch("onemancompany.core.pet_engine.save_pet_wallet")
    @patch("onemancompany.core.pet_engine.random")
    def test_speech_generated_on_tick(self, mock_random, mock_save, mock_load):
        """After enough ticks with lucky roll, pet should have speech."""
        mock_load.return_value = {"tokens": 0, "projects_counted": 0, "tokens_spent": 0}
        mock_random.random.return_value = 0.1
        mock_random.uniform.side_effect = [1.0, 1.0] * 10
        mock_random.choice.side_effect = lambda x: x[0]
        mock_random.randint.return_value = 3
        mock_random.sample = lambda x, k: x[:k]

        pet = _make_pet(needs={"energy": 0.9, "hunger": 0.9, "happiness": 0.9})
        species = _make_species(social=0.0)
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        engine._tick_count = 4
        speech = engine._generate_speech(pet, species)
        assert speech is not None
        assert "text" in speech

    def test_speech_expires_after_ticks(self):
        """Speech should be cleared after 6 ticks (60 seconds)."""
        pet = _make_pet(needs={"energy": 0.9, "hunger": 0.9, "happiness": 0.9})
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        # Set speech AFTER engine init (which clears speech fields)
        pet.current_speech = "Purr mmm~"
        pet.current_mood = "content"
        pet.speech_translation = "I'm so cozy right now."
        pet.speech_tick = 1
        engine._tick_count = 8  # 8 - 1 = 7 ticks since speech, > 6

        engine._expire_speech(pet)

        assert pet.current_speech is None
        assert pet.current_mood is None
        assert pet.speech_translation is None

    def test_speech_not_expired_if_recent(self):
        """Speech should NOT be cleared if fewer than 6 ticks have passed."""
        pet = _make_pet(needs={"energy": 0.9, "hunger": 0.9, "happiness": 0.9})
        species = _make_species()
        engine = _make_engine(pets={"pet_001": pet}, species={"cat": species})

        # Set speech AFTER engine init (which clears speech fields)
        pet.current_speech = "Purr mmm~"
        pet.current_mood = "content"
        pet.speech_translation = "I'm so cozy right now."
        pet.speech_tick = 5
        engine._tick_count = 8  # 8 - 5 = 3 ticks, < 6

        engine._expire_speech(pet)

        assert pet.current_speech == "Purr mmm~"
