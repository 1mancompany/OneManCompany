"""Tests for pet appearance generation."""
import pytest
from onemancompany.core.pet_engine import _generate_appearance

PARTS = {
    "cat": {
        "body_colors": ["orange", "black", "white", "gray", "siamese", "cream", "ginger", "chocolate"],
        "patterns": ["solid", "tabby", "spotted", "bicolor", "calico", "pointed"],
        "ears": ["pointy", "round", "fold"],
        "tails": ["long", "curled", "fluffy"],
        "eye_colors": ["green", "blue", "gold", "copper", "aqua", "yellow"],
    },
    "dog": {
        "body_colors": ["golden", "brown", "black", "white", "husky", "corgi", "chocolate", "red"],
        "patterns": ["solid", "spotted", "masked", "saddle", "merle"],
        "ears": ["floppy", "pointy", "half"],
        "tails": ["up", "down", "curled"],
        "eye_colors": ["brown", "amber", "blue", "hazel", "green", "dark"],
        "collar_colors": ["red", "blue", "green", "gold", "purple"],
    },
    "hamster": {
        "body_colors": ["golden", "white", "gray", "cinnamon", "cream", "panda"],
        "patterns": ["solid", "striped", "panda", "patched"],
        "cheeks": ["normal", "stuffed"],
        "ears": ["round", "pointed"],
        "eye_colors": ["black", "ruby", "brown", "blue", "pink", "dark"],
    },
}


class TestAppearanceGeneration:
    def test_deterministic(self):
        a1 = _generate_appearance("pet_001", "cat", PARTS)
        a2 = _generate_appearance("pet_001", "cat", PARTS)
        assert a1 == a2

    def test_different_seeds_differ(self):
        results = set()
        for i in range(20):
            a = _generate_appearance(f"pet_{i:03d}", "cat", PARTS)
            results.add(tuple(sorted(a.items())))
        assert len(results) > 1

    def test_cat_has_required_fields(self):
        a = _generate_appearance("pet_001", "cat", PARTS)
        assert "body_color" in a
        assert "pattern" in a
        assert "ears" in a
        assert "tail" in a
        assert "eye_color" in a
        assert a["body_color"] in PARTS["cat"]["body_colors"]

    def test_dog_has_collar(self):
        a = _generate_appearance("pet_001", "dog", PARTS)
        assert "collar_color" in a
        assert a["collar_color"] in PARTS["dog"]["collar_colors"]

    def test_hamster_has_cheeks(self):
        a = _generate_appearance("pet_001", "hamster", PARTS)
        assert "cheeks" in a
        assert a["cheeks"] in PARTS["hamster"]["cheeks"]

    def test_cat_has_no_collar(self):
        a = _generate_appearance("pet_001", "cat", PARTS)
        assert "collar_color" not in a

    def test_hamster_has_no_tail(self):
        a = _generate_appearance("pet_001", "hamster", PARTS)
        assert "tail" not in a

    def test_unknown_species_returns_empty(self):
        a = _generate_appearance("pet_001", "unknown", PARTS)
        assert a == {}
