"""Unit tests for onemancompany.core.plugin_registry — 100% coverage."""

from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

import pytest
import yaml

from onemancompany.core.plugin_registry import (
    PluginManifest,
    PluginRegistry,
    PLUGIN_YAML_FILENAME,
)


# ── PluginManifest ──────────────────────────────────────────

class TestPluginManifest:
    def test_from_yaml_minimal(self):
        data = {"id": "test-plugin"}
        m = PluginManifest.from_yaml(data)
        assert m.id == "test-plugin"
        assert m.name == "test-plugin"
        assert m.version == "1.0.0"
        assert m.backend_module == "transform"
        assert m.backend_function == "transform"
        assert m.frontend_script == "render.js"

    def test_from_yaml_full(self):
        data = {
            "id": "fancy",
            "name": "Fancy Plugin",
            "version": "2.0.0",
            "description": "A fancy plugin",
            "author": "tester",
            "builtin": True,
            "view_type": "dashboard",
            "order": 50,
            "icon": "star",
            "backend": {"module": "my_mod", "function": "my_func"},
            "frontend": {"script": "app.js", "style": "app.css", "render_function": "renderFancy"},
            "data_requires": ["tasks", "employees"],
        }
        m = PluginManifest.from_yaml(data)
        assert m.name == "Fancy Plugin"
        assert m.version == "2.0.0"
        assert m.description == "A fancy plugin"
        assert m.author == "tester"
        assert m.builtin is True
        assert m.view_type == "dashboard"
        assert m.order == 50
        assert m.icon == "star"
        assert m.backend_module == "my_mod"
        assert m.backend_function == "my_func"
        assert m.frontend_script == "app.js"
        assert m.frontend_style == "app.css"
        assert m.render_function == "renderFancy"
        assert m.data_requires == ["tasks", "employees"]


# ── PluginRegistry ──────────────────────────────────────────

class TestPluginRegistry:
    def test_discover_no_plugins_dir(self, tmp_path):
        reg = PluginRegistry()
        with patch("onemancompany.core.plugin_registry.PLUGINS_DIR", tmp_path / "nonexistent"):
            reg.discover_and_load()
        assert reg.list_plugins() == []

    def test_discover_skips_files(self, tmp_path):
        """Non-directory entries in PLUGINS_DIR are skipped."""
        (tmp_path / "not_a_dir.txt").write_text("hello")
        reg = PluginRegistry()
        with patch("onemancompany.core.plugin_registry.PLUGINS_DIR", tmp_path):
            reg.discover_and_load()
        assert reg.list_plugins() == []

    def test_discover_skips_dir_without_manifest(self, tmp_path):
        """Directories without plugin.yaml are skipped."""
        (tmp_path / "my_plugin").mkdir()
        reg = PluginRegistry()
        with patch("onemancompany.core.plugin_registry.PLUGINS_DIR", tmp_path):
            reg.discover_and_load()
        assert reg.list_plugins() == []

    def test_discover_loads_valid_plugin(self, tmp_path):
        """Successfully loads a plugin with valid manifest and transform module."""
        plugin_dir = tmp_path / "test_plug"
        plugin_dir.mkdir()

        manifest = {
            "id": "test_plug",
            "name": "Test Plugin",
            "backend": {"module": "transform", "function": "transform"},
        }
        (plugin_dir / PLUGIN_YAML_FILENAME).write_text(yaml.dump(manifest))
        (plugin_dir / "transform.py").write_text(
            "def transform(dispatches, context):\n    return {'ok': True}\n"
        )

        reg = PluginRegistry()
        with patch("onemancompany.core.plugin_registry.PLUGINS_DIR", tmp_path), \
             patch("onemancompany.core.plugin_registry.open_utf", side_effect=lambda p, **kw: open(p)):
            reg.discover_and_load()

        assert reg.get("test_plug") is not None
        assert reg.get("test_plug").manifest.name == "Test Plugin"

    def test_discover_handles_load_error(self, tmp_path):
        """Plugin load failure is caught and logged."""
        plugin_dir = tmp_path / "bad_plug"
        plugin_dir.mkdir()

        manifest = {
            "id": "bad_plug",
            "backend": {"module": "nonexistent", "function": "func"},
        }
        (plugin_dir / PLUGIN_YAML_FILENAME).write_text(yaml.dump(manifest))

        reg = PluginRegistry()
        with patch("onemancompany.core.plugin_registry.PLUGINS_DIR", tmp_path), \
             patch("onemancompany.core.plugin_registry.open_utf", side_effect=lambda p, **kw: open(p)):
            reg.discover_and_load()  # Should not raise

        assert reg.get("bad_plug") is None

    def test_load_plugin_missing_backend_module(self, tmp_path):
        """FileNotFoundError when backend module .py doesn't exist."""
        plugin_dir = tmp_path / "no_mod"
        plugin_dir.mkdir()

        manifest = {"id": "no_mod", "backend": {"module": "missing", "function": "fn"}}
        (plugin_dir / PLUGIN_YAML_FILENAME).write_text(yaml.dump(manifest))

        reg = PluginRegistry()
        with patch("onemancompany.core.plugin_registry.open_utf", side_effect=lambda p, **kw: open(p)):
            with pytest.raises(FileNotFoundError, match="Backend module"):
                reg._load_plugin(plugin_dir, plugin_dir / PLUGIN_YAML_FILENAME)

    def test_load_plugin_missing_function(self, tmp_path):
        """AttributeError when backend function doesn't exist in module."""
        plugin_dir = tmp_path / "no_func"
        plugin_dir.mkdir()

        manifest = {"id": "no_func", "backend": {"module": "transform", "function": "nonexistent"}}
        (plugin_dir / PLUGIN_YAML_FILENAME).write_text(yaml.dump(manifest))
        (plugin_dir / "transform.py").write_text("def other_func(): pass\n")

        reg = PluginRegistry()
        with patch("onemancompany.core.plugin_registry.open_utf", side_effect=lambda p, **kw: open(p)):
            with pytest.raises(AttributeError, match="nonexistent"):
                reg._load_plugin(plugin_dir, plugin_dir / PLUGIN_YAML_FILENAME)

    def test_get_returns_none_for_missing(self):
        reg = PluginRegistry()
        assert reg.get("nonexistent") is None

    def test_list_plugins_filters_by_view_type(self, tmp_path):
        """list_plugins with view_type filters correctly."""
        plugin_dir_a = tmp_path / "plug_a"
        plugin_dir_a.mkdir()
        manifest_a = {"id": "plug_a", "view_type": "dashboard", "order": 10,
                       "backend": {"module": "transform", "function": "transform"}}
        (plugin_dir_a / PLUGIN_YAML_FILENAME).write_text(yaml.dump(manifest_a))
        (plugin_dir_a / "transform.py").write_text("def transform(d, c): return {}\n")

        plugin_dir_b = tmp_path / "plug_b"
        plugin_dir_b.mkdir()
        manifest_b = {"id": "plug_b", "view_type": "project_tab", "order": 20,
                       "backend": {"module": "transform", "function": "transform"}}
        (plugin_dir_b / PLUGIN_YAML_FILENAME).write_text(yaml.dump(manifest_b))
        (plugin_dir_b / "transform.py").write_text("def transform(d, c): return {}\n")

        reg = PluginRegistry()
        with patch("onemancompany.core.plugin_registry.PLUGINS_DIR", tmp_path), \
             patch("onemancompany.core.plugin_registry.open_utf", side_effect=lambda p, **kw: open(p)):
            reg.discover_and_load()

        all_plugins = reg.list_plugins()
        assert len(all_plugins) == 2

        dash_plugins = reg.list_plugins(view_type="dashboard")
        assert len(dash_plugins) == 1
        assert dash_plugins[0].id == "plug_a"

    def test_list_plugins_sorted_by_order(self, tmp_path):
        """list_plugins returns plugins sorted by order."""
        for name, order in [("z_last", 200), ("a_first", 10)]:
            d = tmp_path / name
            d.mkdir()
            m = {"id": name, "order": order, "backend": {"module": "transform", "function": "transform"}}
            (d / PLUGIN_YAML_FILENAME).write_text(yaml.dump(m))
            (d / "transform.py").write_text("def transform(d, c): return {}\n")

        reg = PluginRegistry()
        with patch("onemancompany.core.plugin_registry.PLUGINS_DIR", tmp_path), \
             patch("onemancompany.core.plugin_registry.open_utf", side_effect=lambda p, **kw: open(p)):
            reg.discover_and_load()

        plugins = reg.list_plugins()
        assert plugins[0].id == "a_first"
        assert plugins[1].id == "z_last"

    def test_transform_success(self, tmp_path):
        plugin_dir = tmp_path / "t_plug"
        plugin_dir.mkdir()

        manifest = {"id": "t_plug", "backend": {"module": "transform", "function": "transform"}}
        (plugin_dir / PLUGIN_YAML_FILENAME).write_text(yaml.dump(manifest))
        (plugin_dir / "transform.py").write_text(
            "def transform(dispatches, context):\n    return {'count': len(dispatches)}\n"
        )

        reg = PluginRegistry()
        with patch("onemancompany.core.plugin_registry.PLUGINS_DIR", tmp_path), \
             patch("onemancompany.core.plugin_registry.open_utf", side_effect=lambda p, **kw: open(p)):
            reg.discover_and_load()

        result = reg.transform("t_plug", [{"a": 1}, {"b": 2}], {})
        assert result == {"count": 2}

    def test_transform_unknown_plugin(self):
        reg = PluginRegistry()
        with pytest.raises(KeyError, match="not found"):
            reg.transform("nonexistent", [], {})

    def test_discover_clears_previous(self, tmp_path):
        """discover_and_load clears previously loaded plugins."""
        plugin_dir = tmp_path / "plug"
        plugin_dir.mkdir()
        manifest = {"id": "plug", "backend": {"module": "transform", "function": "transform"}}
        (plugin_dir / PLUGIN_YAML_FILENAME).write_text(yaml.dump(manifest))
        (plugin_dir / "transform.py").write_text("def transform(d, c): return {}\n")

        reg = PluginRegistry()
        with patch("onemancompany.core.plugin_registry.PLUGINS_DIR", tmp_path), \
             patch("onemancompany.core.plugin_registry.open_utf", side_effect=lambda p, **kw: open(p)):
            reg.discover_and_load()
            assert reg.get("plug") is not None

        # Now discover from empty dir
        empty = tmp_path / "empty"
        empty.mkdir()
        with patch("onemancompany.core.plugin_registry.PLUGINS_DIR", empty):
            reg.discover_and_load()

        assert reg.get("plug") is None
