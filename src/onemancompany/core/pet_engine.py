"""PetEngine — singleton managing all pet state, behavior, and stray lifecycle."""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone

from loguru import logger

from onemancompany.core.pet_models import (
    ConsumableType,
    FacilityInstance,
    FacilityType,
    PetInstance,
    PetState,
    SpeciesDefinition,
)
from onemancompany.core.store import load_pet_wallet, save_pet_wallet

MAX_PETS = 3
STRAY_SPAWN_CHANCE_FIRST = 0.20   # 20% per tick — first pet arrives quickly (~50s)
STRAY_SPAWN_CHANCE_LATER = 0.001  # 0.1% per tick — subsequent pets: ~2.8 hours apart
STRAY_TIMEOUT_SECONDS = 3600  # 1 hour
TICK_INTERVAL_SECONDS = 10.0
SPEECH_EXPIRE_TICKS = 6  # speech bubbles last ~60 seconds
SPEECH_CHANCE = 0.05  # flat 5% chance per tick (~1 speech per 200s)

# Departure mechanic — pets leave if all needs stay at zero too long
DEPARTURE_THRESHOLD = 0.01  # needs below this count as "zero"
DEPARTURE_TICKS = 2160      # 6 hours at 10s/tick = 6*60*6 = 2160 ticks

# Stray overflow — allow strays to spawn beyond MAX_PETS (up to +2)
STRAY_OVERFLOW = 2

# Pool of auto-generated names for stray pets
_STRAY_NAMES = [
    "Biscuit", "Shadow", "Pepper", "Sunny", "Maple", "Dusty",
    "Pebble", "Cocoa", "Rusty", "Misty", "Ginger", "Smoky",
    "Patches", "Lucky", "Scout", "Bandit", "Whiskers", "Nibbles",
    "Mittens", "Noodle", "Muffin", "Pickle", "Waffles", "Toffee",
]

# Recovery rate per tick for sleeping/eating/playing
_RECOVERY_RATE = 0.05
_RECOVERY_THRESHOLD = 0.8

# ---------------------------------------------------------------------------
# Simlish speech data
# ---------------------------------------------------------------------------

SIMLISH_WORDS = {
    "hungry": ["mew", "naka", "fud", "nom", "grr", "tum"],
    "happy": ["purr", "yip", "woo", "nyan", "hehe", "boop"],
    "tired": ["zzz", "muu", "yawn", "nuu", "muuu", "bleh"],
    "playful": ["zoom", "paw", "bap", "wee", "nya", "hop"],
    "lonely": ["mew", "snif", "waa", "hmm", "huu", "oof"],
    "content": ["purr", "mmm", "ahh", "zen", "chu", "bliss"],
}

SIMLISH_CONNECTORS = ["~", "!", "...", " ", "-", "\u266a"]

MOOD_TRANSLATIONS = {
    "hungry": [
        "I'm getting hungry...",
        "Is it snack time yet?",
        "My tummy is rumbling!",
        "Could really use a treat right now.",
    ],
    "happy": [
        "Life is great!",
        "I love this office!",
        "Best day ever!",
        "Everything is wonderful~",
    ],
    "tired": [
        "So sleepy...",
        "I need a nap.",
        "Can barely keep my eyes open...",
        "Where's my bed?",
    ],
    "playful": [
        "Let's play!",
        "Chase me!",
        "I want to run around!",
        "Where's my toy?",
    ],
    "lonely": [
        "Nobody wants to play with me...",
        "I miss my friends.",
        "Come pet me please?",
        "It's quiet here...",
    ],
    "content": [
        "Everything is perfect.",
        "I'm so cozy right now.",
        "This is the life.",
        "Purr... I mean... I'm happy.",
    ],
}

# Mapping from need name to mood when that need is critically low
_NEED_TO_MOOD = {
    "hunger": "hungry",
    "energy": "tired",
    "happiness": "lonely",
}


_CAT_PARTS = {
    "body_colors": ["orange", "black", "white", "gray", "siamese", "cream", "ginger", "chocolate"],
    "patterns": ["solid", "tabby", "spotted", "bicolor", "calico", "pointed"],
    "ears": ["pointy", "round", "fold"],
    "tails": ["long", "curled", "fluffy"],
    "eye_colors": ["green", "blue", "gold", "copper", "aqua", "yellow"],
}
_DOG_PARTS = {
    "body_colors": ["golden", "brown", "black", "white", "husky", "corgi", "chocolate", "red"],
    "patterns": ["solid", "spotted", "masked", "saddle", "merle"],
    "ears": ["floppy", "pointy", "half"],
    "tails": ["up", "down", "curled"],
    "eye_colors": ["brown", "amber", "blue", "hazel", "green", "dark"],
    "collar_colors": ["red", "blue", "green", "gold", "purple"],
}
_RAT_PARTS = {
    "body_colors": ["golden", "white", "gray", "cinnamon", "cream", "panda"],
    "patterns": ["solid", "striped", "panda", "patched"],
    "ears": ["round", "pointed"],
    "eye_colors": ["black", "ruby", "brown", "blue", "pink", "dark"],
}

_PARTS = {
    "cat": _CAT_PARTS,
    "cat2": _CAT_PARTS,
    "dog": _DOG_PARTS,
    "dog2": _DOG_PARTS,
    "rat": _RAT_PARTS,
    "rat2": _RAT_PARTS,
}


def _generate_appearance(pet_id: str, species: str, parts: dict | None = None) -> dict:
    """Generate deterministic appearance dict from pet_id seed."""
    if parts is None:
        parts = _PARTS
    sp = parts.get(species)
    if not sp:
        return {}

    h = 0
    for ch in pet_id:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
    rng_state = h

    def _next():
        nonlocal rng_state
        rng_state = (rng_state + 0x6D2B79F5) & 0xFFFFFFFF
        t = ((rng_state ^ (rng_state >> 15)) * (1 | rng_state)) & 0xFFFFFFFF
        t = (t + ((t ^ (t >> 7)) * (61 | t))) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    def _pick(lst):
        return lst[int(_next() * len(lst))]

    result = {}
    if "body_colors" in sp:
        result["body_color"] = _pick(sp["body_colors"])
    if "patterns" in sp:
        result["pattern"] = _pick(sp["patterns"])
    if "ears" in sp:
        result["ears"] = _pick(sp["ears"])
    if "tails" in sp:
        result["tail"] = _pick(sp["tails"])
    if "cheeks" in sp:
        result["cheeks"] = _pick(sp["cheeks"])
    if "eye_colors" in sp:
        result["eye_color"] = _pick(sp["eye_colors"])
    if "collar_colors" in sp:
        result["collar_color"] = _pick(sp["collar_colors"])
    return result


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
        consumable_types: dict[str, ConsumableType] | None = None,
        office_cols: int = 20,
        office_rows: int = 18,
    ):
        self._species = species
        self._pets = pets
        self._facility_types = facility_types
        self._facilities = facilities
        self._consumable_types: dict[str, ConsumableType] = consumable_types or {}
        self._office_cols = office_cols
        self._office_rows = office_rows

        self._dirty_pets: set[str] = set()
        self._deleted_pets: set[str] = set()
        self._tick_count: int = 0

        # Clear transient speech fields so stale bubbles don't persist across restarts
        for pet in self._pets.values():
            pet.current_speech = None
            pet.current_mood = None
            pet.speech_translation = None
            pet.speech_tick = 0

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
    def facilities(self) -> dict[str, FacilityInstance]:
        return self._facilities

    @property
    def facility_types(self) -> dict[str, FacilityType]:
        return self._facility_types

    @property
    def consumable_types(self) -> dict[str, ConsumableType]:
        return self._consumable_types

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
        self._tick_count += 1
        changed = False

        for pet_id, pet in list(self._pets.items()):
            sp = self._species.get(pet.species)
            if not sp:
                logger.warning("Pet {} has unknown species '{}'", pet_id, pet.species)
                continue

            old_state = pet.state
            old_pos = list(pet.position)
            old_speech = pet.current_speech

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

            # 3. Speech bubble lifecycle
            self._expire_speech(pet)
            self._maybe_generate_speech(pet, sp)

            if pet.state != old_state or pet.position != old_pos or pet.current_speech != old_speech:
                self._dirty_pets.add(pet_id)
                changed = True

        # 4. Departure check — pets leave if all needs at zero too long
        to_remove = []
        for pet_id, pet in self._pets.items():
            all_zero = all(v <= DEPARTURE_THRESHOLD for v in pet.needs.values())
            if all_zero:
                pet.sad_ticks += 1
                if pet.sad_ticks >= DEPARTURE_TICKS:
                    logger.info("Pet {} ({}) left the office sadly after being neglected", pet.id, pet.species)
                    to_remove.append(pet_id)
            else:
                pet.sad_ticks = 0  # reset if any need recovers

        for pet_id in to_remove:
            del self._pets[pet_id]
            self._dirty_pets.discard(pet_id)
            self._deleted_pets.add(pet_id)
            changed = True

        # 5. Stray lifecycle
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

        # 0. 70% chance to do nothing (pets are lazy)
        if pet.state == PetState.IDLE and random.random() < 0.7:
            return  # stay idle, do nothing

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
        """Attempt to spawn a stray pet at the office edge.

        Strays can spawn even at MAX_PETS (up to MAX_PETS + STRAY_OVERFLOW)
        so the CEO always has a chance to discover new pets.
        """
        if len(self._pets) >= MAX_PETS + STRAY_OVERFLOW:
            return False
        # First pet arrives quickly, subsequent ones take hours
        chance = STRAY_SPAWN_CHANCE_FIRST if len(self._pets) == 0 else STRAY_SPAWN_CHANCE_LATER
        if random.random() >= chance:
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
            name=random.choice(_STRAY_NAMES),
            owner=None,
            position=[x, y],
            state=PetState.IDLE,
            needs={name: 1.0 for name in self._species[species_id].needs},
            spawned_at=now,
            appearance=_generate_appearance(pet_id, species_id),
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

    def release_pet(self, pet_id: str) -> bool:
        """Release an owned pet (send them away). Returns True on success."""
        pet = self._pets.get(pet_id)
        if not pet:
            logger.warning("release_pet: pet {} not found", pet_id)
            return False
        if pet.owner is None:
            logger.warning("release_pet: pet {} is a stray, cannot release", pet_id)
            return False
        del self._pets[pet_id]
        self._dirty_pets.discard(pet_id)
        self._deleted_pets.add(pet_id)
        logger.info("Pet {} ({}) released by owner {}", pet_id, pet.species, pet.owner)
        return True

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

    # ------------------------------------------------------------------
    # Token economy
    # ------------------------------------------------------------------

    def sync_tokens(self, total_completed_projects: int) -> int:
        """Sync token wallet with completed project count.

        Every 3 completed projects earns 1 pet token.
        Returns current available balance.
        """
        wallet = load_pet_wallet()
        earned_now = total_completed_projects // 3
        earned_before = wallet["projects_counted"] // 3
        new_tokens = max(0, earned_now - earned_before)
        if new_tokens > 0:
            wallet["tokens"] += new_tokens
            logger.info("Pet tokens: granted {} new (total={})", new_tokens, wallet["tokens"])
        wallet["projects_counted"] = total_completed_projects
        save_pet_wallet(wallet)
        return wallet["tokens"] - wallet["tokens_spent"]

    def get_token_balance(self) -> int:
        """Return available token balance (tokens - tokens_spent)."""
        wallet = load_pet_wallet()
        return wallet["tokens"] - wallet["tokens_spent"]

    def spend_tokens(self, amount: int) -> bool:
        """Spend tokens. Returns True if successful, False if insufficient balance."""
        wallet = load_pet_wallet()
        available = wallet["tokens"] - wallet["tokens_spent"]
        if available < amount:
            logger.debug("spend_tokens: need {} but only {} available", amount, available)
            return False
        wallet["tokens_spent"] += amount
        save_pet_wallet(wallet)
        logger.info("Pet tokens: spent {} (remaining={})", amount, wallet["tokens"] - wallet["tokens_spent"])
        return True

    def get_all_state(self) -> dict:
        """Return full pet state for frontend sync."""
        return {
            "pets": [pet.to_dict() for pet in self._pets.values()],
            "facilities": [fac.model_dump() for fac in self._facilities.values()],
            "species": {
                sid: sp.model_dump() for sid, sp in self._species.items()
            },
            "consumables": {
                cid: ct.model_dump() for cid, ct in self._consumable_types.items()
            },
            "tokens": self.get_token_balance(),
        }

    # ------------------------------------------------------------------
    # Consumable items
    # ------------------------------------------------------------------

    def can_use_consumable(self, pet_id: str, consumable_id: str) -> bool:
        """Check if a consumable can be used on a pet WITHOUT applying effects.

        Validates pet exists, consumable exists, and species is compatible.
        """
        pet = self._pets.get(pet_id)
        ctype = self._consumable_types.get(consumable_id)
        if not pet or not ctype:
            return False
        if ctype.target_species != "all" and pet.species not in ctype.target_species:
            return False
        return True

    def refund_tokens(self, amount: int) -> None:
        """Refund tokens by decrementing tokens_spent in the wallet.

        All wallet mutations go through the engine.
        """
        wallet = load_pet_wallet()
        wallet["tokens_spent"] = max(0, wallet["tokens_spent"] - amount)
        save_pet_wallet(wallet)
        logger.info("Pet tokens: refunded {} (remaining={})", amount, wallet["tokens"] - wallet["tokens_spent"])

    def use_consumable(self, pet_id: str, consumable_id: str) -> bool:
        """Use a consumable on a pet. Checks species compatibility.

        Does NOT handle tokens (caller/routes does that).
        """
        pet = self._pets.get(pet_id)
        ctype = self._consumable_types.get(consumable_id)
        if not pet or not ctype:
            return False
        # Species check
        if ctype.target_species != "all" and pet.species not in ctype.target_species:
            return False
        # Apply effects
        for need, delta in ctype.effect.items():
            if need in pet.needs:
                pet.needs[need] = max(0.0, min(1.0, pet.needs[need] + delta))
        self._dirty_pets.add(pet_id)
        logger.debug("Consumable {} used on pet {} (effects: {})", consumable_id, pet_id, ctype.effect)
        return True

    # ------------------------------------------------------------------
    # Speech bubbles (Simlish)
    # ------------------------------------------------------------------

    def _determine_mood(self, pet: PetInstance) -> str:
        """Determine pet's mood based on needs levels."""
        # Find the lowest need (shuffle to break ties randomly)
        items = list(pet.needs.items())
        random.shuffle(items)
        lowest_need = None
        lowest_val = float("inf")
        for need_name, val in items:
            if val < lowest_val:
                lowest_val = val
                lowest_need = need_name

        # If any need is critically low (< 0.3)
        if lowest_val < 0.3 and lowest_need in _NEED_TO_MOOD:
            return _NEED_TO_MOOD[lowest_need]

        # All needs above 0.7 -> content
        if all(v > 0.7 for v in pet.needs.values()):
            return "content"

        # Otherwise playful or happy
        return random.choice(["playful", "happy"])

    def _generate_speech(self, pet: PetInstance, species: SpeciesDefinition) -> dict:
        """Generate a Simlish speech bubble for a pet.

        Returns {text: str, mood: str, translation: str}.
        """
        mood = self._determine_mood(pet)
        pool = SIMLISH_WORDS.get(mood, SIMLISH_WORDS["content"])

        # Generate 2-4 words
        word_count = random.randint(2, 4)
        words = [random.choice(pool) for _ in range(word_count)]

        # Capitalize first word
        words[0] = words[0].capitalize()

        # Join with random connectors
        parts = []
        for i, w in enumerate(words):
            parts.append(w)
            if i < len(words) - 1:
                parts.append(random.choice(SIMLISH_CONNECTORS))

        # Add a trailing connector for flavor
        parts.append(random.choice(["!", "~", "..."]))
        text = "".join(parts)

        translation = random.choice(MOOD_TRANSLATIONS[mood])

        return {"text": text, "mood": mood, "translation": translation}

    def _maybe_generate_speech(self, pet: PetInstance, species: SpeciesDefinition) -> None:
        """Possibly generate new speech for a pet during tick.

        Each tick, each pet has a flat SPEECH_CHANCE (5%) probability to
        generate speech if they don't already have one. This gives roughly
        one speech per 200 seconds on average.
        """
        # Don't generate if pet already has speech
        if pet.current_speech is not None:
            return
        if random.random() >= SPEECH_CHANCE:
            return

        speech = self._generate_speech(pet, species)
        pet.current_speech = speech["text"]
        pet.current_mood = speech["mood"]
        pet.speech_translation = speech["translation"]
        pet.speech_tick = self._tick_count
        logger.debug("Pet {} says: '{}' (mood={}, translation='{}')",
                      pet.id, speech["text"], speech["mood"], speech["translation"])

    def _expire_speech(self, pet: PetInstance) -> None:
        """Clear speech bubble if it has been displayed for enough ticks."""
        if pet.current_speech is None:
            return
        if self._tick_count - pet.speech_tick >= SPEECH_EXPIRE_TICKS:
            pet.current_speech = None
            pet.current_mood = None
            pet.speech_translation = None
            logger.debug("Pet {} speech expired", pet.id)

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
