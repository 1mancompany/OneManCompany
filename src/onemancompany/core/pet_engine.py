"""PetEngine — singleton managing all pet state, behavior, and stray lifecycle."""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone

from loguru import logger

from onemancompany.core.pet_models import (
    FacilityInstance,
    FacilityType,
    PetInstance,
    PetState,
    SpeciesDefinition,
)

MAX_PETS = 3
STRAY_SPAWN_CHANCE = 0.05  # 5% per tick
STRAY_TIMEOUT_SECONDS = 3600  # 1 hour
TICK_INTERVAL_SECONDS = 10.0

# Recovery rate per tick for sleeping/eating/playing
_RECOVERY_RATE = 0.05
_RECOVERY_THRESHOLD = 0.8


class PetEngine:
    """Core pet simulation engine.

    Manages needs decay, behavior decisions, movement, stray spawning/expiry,
    and public API for adoption/interaction.
    """

    def __init__(
        self,
        species: dict[str, SpeciesDefinition],
        pets: dict[str, PetInstance],
        facility_types: dict[str, FacilityType],
        facilities: dict[str, FacilityInstance],
        office_cols: int = 20,
        office_rows: int = 18,
    ):
        self._species = species
        self._pets = pets
        self._facility_types = facility_types
        self._facilities = facilities
        self._office_cols = office_cols
        self._office_rows = office_rows

        self._dirty_pets: set[str] = set()
        self._deleted_pets: set[str] = set()

        # Auto-increment pet ID from existing max
        max_id = 0
        for pid in self._pets:
            try:
                num = int(pid.split("_")[-1])
                if num > max_id:
                    max_id = num
            except (ValueError, IndexError):
                logger.debug("Non-numeric pet ID suffix, skipping: {}", pid)
        self._next_pet_id = max_id + 1

    @property
    def pets(self) -> dict[str, PetInstance]:
        return self._pets

    @property
    def dirty_pets(self) -> set[str]:
        return self._dirty_pets

    @property
    def deleted_pets(self) -> set[str]:
        return self._deleted_pets

    # ------------------------------------------------------------------
    # Main tick
    # ------------------------------------------------------------------

    def tick(self) -> bool:
        """Advance one simulation tick. Returns True if any state changed."""
        changed = False

        for pet_id, pet in list(self._pets.items()):
            sp = self._species.get(pet.species)
            if not sp:
                logger.warning("Pet {} has unknown species '{}'", pet_id, pet.species)
                continue

            old_state = pet.state
            old_pos = list(pet.position)

            # 1. Decay needs
            self._decay_needs(pet, sp)

            # 2. Handle current state or decide new behavior
            if pet.state == PetState.SLEEPING:
                self._handle_sleeping(pet)
            elif pet.state == PetState.EATING:
                self._handle_eating(pet)
            elif pet.state == PetState.PLAYING:
                self._handle_playing(pet)
            elif pet.state == PetState.WALKING:
                self._move_pet(pet, sp)
                # If arrived at target and was walking to a facility, start activity
                if pet.target_position is None:
                    self._decide_behavior(pet, sp)
            else:
                # IDLE — decide what to do
                self._decide_behavior(pet, sp)

            if pet.state != old_state or pet.position != old_pos:
                self._dirty_pets.add(pet_id)
                changed = True

        # 3. Stray lifecycle
        if self._try_spawn_stray():
            changed = True
        if self._expire_strays():
            changed = True

        return changed

    # ------------------------------------------------------------------
    # Needs decay
    # ------------------------------------------------------------------

    def _decay_needs(self, pet: PetInstance, species: SpeciesDefinition) -> None:
        """Subtract decay_rate from each need, clamp at 0."""
        for name, cfg in species.needs.items():
            current = pet.needs.get(name, 1.0)
            pet.needs[name] = max(0.0, current - cfg.decay_rate)

    # ------------------------------------------------------------------
    # Behavior decision tree
    # ------------------------------------------------------------------

    def _decide_behavior(self, pet: PetInstance, species: SpeciesDefinition) -> None:
        """Priority-based behavior selection."""
        needs = pet.needs
        beh = species.behaviors

        # 1. Energy critical -> sleep
        energy_cfg = species.needs.get("energy")
        if energy_cfg and needs.get("energy", 1.0) < energy_cfg.critical:
            bed = self._find_nearest_facility(pet.position, "pet_bed")
            if bed:
                if self._is_close(pet.position, [float(bed.position[0]), float(bed.position[1])], beh.speed):
                    pet.state = PetState.SLEEPING
                else:
                    pet.state = PetState.WALKING
                    pet.target_position = [float(bed.position[0]), float(bed.position[1])]
            else:
                # Sleep in place
                pet.state = PetState.SLEEPING
            logger.debug("Pet {} -> SLEEPING (energy={:.2f})", pet.id, needs.get("energy", 0))
            return

        # 2. Hunger critical -> find food bowl
        hunger_cfg = species.needs.get("hunger")
        if hunger_cfg and needs.get("hunger", 1.0) < hunger_cfg.critical:
            bowl = self._find_nearest_facility(pet.position, "food_bowl")
            if bowl:
                target = [float(bowl.position[0]), float(bowl.position[1])]
                if self._is_close(pet.position, target, beh.speed):
                    pet.state = PetState.EATING
                else:
                    pet.state = PetState.WALKING
                    pet.target_position = target
                logger.debug("Pet {} -> EATING (hunger={:.2f})", pet.id, needs.get("hunger", 0))
                return

        # 3. Happiness critical -> find toy
        happiness_cfg = species.needs.get("happiness")
        if happiness_cfg and needs.get("happiness", 1.0) < happiness_cfg.critical:
            toy = self._find_nearest_facility(pet.position, "toy_ball")
            if toy:
                target = [float(toy.position[0]), float(toy.position[1])]
                if self._is_close(pet.position, target, beh.speed):
                    pet.state = PetState.PLAYING
                else:
                    pet.state = PetState.WALKING
                    pet.target_position = target
                logger.debug("Pet {} -> PLAYING (happiness={:.2f})", pet.id, needs.get("happiness", 0))
                return

        # 4. Social -> move toward nearest other pet
        if random.random() < beh.social:
            nearest = self._find_nearest_pet(pet)
            if nearest:
                pet.state = PetState.WALKING
                pet.target_position = list(nearest.position)
                logger.debug("Pet {} -> social walk toward {}", pet.id, nearest.id)
                return

        # 5. Wander
        dx = random.uniform(-beh.wander_radius, beh.wander_radius)
        dy = random.uniform(-beh.wander_radius, beh.wander_radius)
        tx = max(0.0, min(float(self._office_cols - 1), pet.position[0] + dx))
        ty = max(0.0, min(float(self._office_rows - 1), pet.position[1] + dy))
        pet.state = PetState.WALKING
        pet.target_position = [tx, ty]
        logger.debug("Pet {} -> wander to ({:.1f}, {:.1f})", pet.id, tx, ty)

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------

    def _move_pet(self, pet: PetInstance, species: SpeciesDefinition) -> None:
        """Move pet toward target_position by speed tiles."""
        if pet.target_position is None:
            return

        dx = pet.target_position[0] - pet.position[0]
        dy = pet.target_position[1] - pet.position[1]
        dist = math.sqrt(dx * dx + dy * dy)

        if dist <= species.behaviors.speed:
            # Arrived
            pet.position = list(pet.target_position)
            pet.target_position = None
        else:
            # Move toward target
            ratio = species.behaviors.speed / dist
            pet.position[0] += dx * ratio
            pet.position[1] += dy * ratio

    # ------------------------------------------------------------------
    # Recovery state handlers
    # ------------------------------------------------------------------

    def _handle_sleeping(self, pet: PetInstance) -> None:
        """Recover energy while sleeping."""
        pet.needs["energy"] = min(1.0, pet.needs.get("energy", 0) + _RECOVERY_RATE)
        if pet.needs["energy"] >= _RECOVERY_THRESHOLD:
            pet.state = PetState.IDLE
            logger.debug("Pet {} woke up (energy={:.2f})", pet.id, pet.needs["energy"])

    def _handle_eating(self, pet: PetInstance) -> None:
        """Recover hunger while eating."""
        pet.needs["hunger"] = min(1.0, pet.needs.get("hunger", 0) + _RECOVERY_RATE)
        if pet.needs["hunger"] >= _RECOVERY_THRESHOLD:
            pet.state = PetState.IDLE
            logger.debug("Pet {} done eating (hunger={:.2f})", pet.id, pet.needs["hunger"])

    def _handle_playing(self, pet: PetInstance) -> None:
        """Recover happiness while playing."""
        pet.needs["happiness"] = min(1.0, pet.needs.get("happiness", 0) + _RECOVERY_RATE)
        if pet.needs["happiness"] >= _RECOVERY_THRESHOLD:
            pet.state = PetState.IDLE
            logger.debug("Pet {} done playing (happiness={:.2f})", pet.id, pet.needs["happiness"])

    # ------------------------------------------------------------------
    # Stray lifecycle
    # ------------------------------------------------------------------

    def _try_spawn_stray(self) -> bool:
        """Attempt to spawn a stray pet at the office edge."""
        if len(self._pets) >= MAX_PETS:
            return False
        if random.random() >= STRAY_SPAWN_CHANCE:
            return False
        if not self._species:
            return False

        species_id = random.choice(list(self._species.keys()))
        pet_id = f"pet_{self._next_pet_id:03d}"
        self._next_pet_id += 1

        x = float(random.randint(0, self._office_cols - 1))
        y = 0.0 if random.random() < 0.5 else float(self._office_rows - 1)
        now = datetime.now(timezone.utc).isoformat()

        pet = PetInstance(
            id=pet_id,
            species=species_id,
            owner=None,
            position=[x, y],
            state=PetState.IDLE,
            needs={name: 1.0 for name in self._species[species_id].needs},
            spawned_at=now,
        )
        self._pets[pet_id] = pet
        self._dirty_pets.add(pet_id)
        logger.info("Stray {} ({}) spawned at ({}, {})", pet_id, species_id, x, y)
        return True

    def _expire_strays(self) -> bool:
        """Remove unowned strays older than STRAY_TIMEOUT_SECONDS."""
        now = datetime.now(timezone.utc)
        expired = []
        for pid, pet in self._pets.items():
            if pet.owner is not None:
                continue
            if not pet.spawned_at:
                continue
            spawned = datetime.fromisoformat(pet.spawned_at)
            age = (now - spawned).total_seconds()
            if age > STRAY_TIMEOUT_SECONDS:
                expired.append(pid)

        for pid in expired:
            del self._pets[pid]
            self._dirty_pets.discard(pid)
            self._deleted_pets.add(pid)
            logger.info("Stray {} expired after timeout", pid)

        return len(expired) > 0

    # ------------------------------------------------------------------
    # Facility / pet lookup helpers
    # ------------------------------------------------------------------

    def _find_nearest_facility(
        self, position: list[float], facility_type: str
    ) -> FacilityInstance | None:
        """Find the nearest facility of the given type."""
        best: FacilityInstance | None = None
        best_dist = float("inf")
        for fac in self._facilities.values():
            if fac.type != facility_type:
                continue
            dx = position[0] - float(fac.position[0])
            dy = position[1] - float(fac.position[1])
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < best_dist:
                best_dist = dist
                best = fac
        return best

    def _find_nearest_pet(self, pet: PetInstance) -> PetInstance | None:
        """Find nearest other pet."""
        best: PetInstance | None = None
        best_dist = float("inf")
        for other in self._pets.values():
            if other.id == pet.id:
                continue
            dx = pet.position[0] - other.position[0]
            dy = pet.position[1] - other.position[1]
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < best_dist:
                best_dist = dist
                best = other
        return best

    @staticmethod
    def _is_close(pos_a: list[float], pos_b: list[float], threshold: float) -> bool:
        dx = pos_a[0] - pos_b[0]
        dy = pos_a[1] - pos_b[1]
        return math.sqrt(dx * dx + dy * dy) <= threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def adopt_pet(self, pet_id: str, owner_id: str) -> PetInstance | None:
        """Adopt a stray pet (assign owner)."""
        pet = self._pets.get(pet_id)
        if not pet:
            logger.warning("adopt_pet: pet {} not found", pet_id)
            return None
        if pet.owner is not None:
            logger.warning("adopt_pet: pet {} already owned by {}", pet_id, pet.owner)
            return None
        pet.owner = owner_id
        pet.adopted_at = datetime.now(timezone.utc).isoformat()
        self._dirty_pets.add(pet_id)
        logger.info("Pet {} adopted by {}", pet_id, owner_id)
        return pet

    def interact_pet(self, pet_id: str, action: str) -> bool:
        """Interact with a pet (pet, feed, play)."""
        pet = self._pets.get(pet_id)
        if not pet:
            return False
        if action == "pet":
            pet.needs["happiness"] = min(1.0, pet.needs.get("happiness", 0) + 0.1)
        elif action == "feed":
            pet.needs["hunger"] = min(1.0, pet.needs.get("hunger", 0) + 0.2)
        elif action == "play":
            pet.needs["happiness"] = min(1.0, pet.needs.get("happiness", 0) + 0.15)
        else:
            logger.warning("interact_pet: unknown action '{}'", action)
            return False
        self._dirty_pets.add(pet_id)
        return True

    def rename_pet(self, pet_id: str, name: str) -> bool:
        """Rename a pet."""
        pet = self._pets.get(pet_id)
        if not pet:
            return False
        pet.name = name
        self._dirty_pets.add(pet_id)
        return True

    def get_all_state(self) -> dict:
        """Return full pet state for frontend sync."""
        return {
            "pets": [pet.to_dict() for pet in self._pets.values()],
            "facilities": [fac.model_dump() for fac in self._facilities.values()],
            "species": {
                sid: sp.model_dump() for sid, sp in self._species.items()
            },
        }

    def add_facility(self, facility: FacilityInstance) -> None:
        """Add a facility to the office."""
        self._facilities[facility.id] = facility
        logger.info("Facility {} ({}) placed at {}", facility.id, facility.type, facility.position)

    def remove_facility(self, facility_id: str) -> bool:
        """Remove a facility from the office."""
        if facility_id not in self._facilities:
            return False
        del self._facilities[facility_id]
        logger.info("Facility {} removed", facility_id)
        return True
