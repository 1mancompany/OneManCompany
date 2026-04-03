"""Pet system data models."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, model_validator


class PetState(str, Enum):
    """Possible states for a pet."""
    IDLE = "idle"
    WALKING = "walking"
    SLEEPING = "sleeping"
    EATING = "eating"
    PLAYING = "playing"


class AnimationDef(BaseModel):
    """Sprite-sheet animation definition."""
    row: int
    frames: int
    speed: float


class NeedConfig(BaseModel):
    """Configuration for a single pet need (hunger, happiness, etc.)."""
    decay_rate: float
    critical: float


class BehaviorConfig(BaseModel):
    """Behavioral parameters for a pet species."""
    wander_radius: int = 8
    favorite_spots: list[str] = []
    social: float = 0.5
    speed: float = 0.5


class SpeciesDefinition(BaseModel):
    """Defines a pet species (loaded from YAML)."""
    id: str
    name: str
    size: list[int] = [1, 1]
    sprite_sheet: str
    animations: dict[str, AnimationDef]
    needs: dict[str, NeedConfig]
    behaviors: BehaviorConfig

    @model_validator(mode="after")
    def _validate_species(self) -> SpeciesDefinition:
        if "idle" not in self.animations:
            raise ValueError("animations must include 'idle'")
        if not self.needs:
            raise ValueError("needs must not be empty")
        return self


class PetInstance(BaseModel):
    """Runtime instance of a pet in the office."""
    id: str
    species: str
    name: Optional[str] = None
    owner: Optional[str] = None
    position: list[float]
    state: PetState = PetState.IDLE
    target_position: Optional[list[float]] = None
    needs: dict[str, float] = {"hunger": 1.0, "happiness": 1.0, "energy": 1.0}
    adopted_at: Optional[str] = None
    spawned_at: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize to dict with state as string."""
        d = self.model_dump()
        d["state"] = self.state.value
        return d


class ConsumableType(BaseModel):
    """Definition of a consumable item (use-once) for pets."""
    id: str
    name: str
    icon: str = "\U0001f381"
    cost: int = 1
    effect: dict[str, float]
    target_species: list[str] | str = "all"  # "all" or list of species ids


class FacilityType(BaseModel):
    """Definition of a pet facility type."""
    id: str
    name: str
    sprite: str
    size: list[int] = [1, 1]
    effect: dict[str, float]
    cooldown: int = 60
    cost: int = 1


class FacilityInstance(BaseModel):
    """Placed facility instance in the office."""
    id: str
    type: str
    position: list[int]
    placed_by: str
