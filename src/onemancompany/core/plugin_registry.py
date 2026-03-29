"""Plugin registry — discover, load and invoke view plugins."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml
from loguru import logger

from onemancompany.core.config import PLUGINS_DIR

# Single-file constants
PLUGIN_YAML_FILENAME = "plugin.yaml"


@dataclass
class PluginManifest:
    id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    builtin: bool = False
    view_type: str = "project_tab"
    order: int = 100
    icon: str = ""
    backend_module: str = "transform"
    backend_function: str = "transform"
    frontend_script: str = "render.js"
    frontend_style: str = ""
    render_function: str = ""
    data_requires: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, data: dict) -> PluginManifest:
        backend = data.get("backend", {})
        frontend = data.get("frontend", {})
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            builtin=data.get("builtin", False),
            view_type=data.get("view_type", "project_tab"),
            order=data.get("order", 100),
            icon=data.get("icon", ""),
            backend_module=backend.get("module", "transform"),
            backend_function=backend.get("function", "transform"),
            frontend_script=frontend.get("script", "render.js"),
            frontend_style=frontend.get("style", ""),
            render_function=frontend.get("render_function", ""),
            data_requires=data.get("data_requires", []),
        )


@dataclass
class LoadedPlugin:
    manifest: PluginManifest
    transformer: Callable[..., dict]
    plugin_dir: Path


class PluginRegistry:
    """Singleton registry that discovers and manages view plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, LoadedPlugin] = {}

    def discover_and_load(self) -> None:
        """Scan PLUGINS_DIR for plugin.yaml files and load each plugin."""
        self._plugins.clear()
        if not PLUGINS_DIR.exists():
            logger.info("No plugins directory at {}", PLUGINS_DIR)
            return

        for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
            if not plugin_dir.is_dir():
                continue
            manifest_path = plugin_dir / PLUGIN_YAML_FILENAME
            if not manifest_path.exists():
                continue
            try:
                self._load_plugin(plugin_dir, manifest_path)
            except Exception as e:
                logger.warning("Failed to load plugin from {}: {}", plugin_dir.name, e)

        logger.info("Loaded {} plugin(s): {}", len(self._plugins), list(self._plugins.keys()))

    def _load_plugin(self, plugin_dir: Path, manifest_path: Path) -> None:
        with open(manifest_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        manifest = PluginManifest.from_yaml(data)

        # Dynamically load the transformer module
        module_file = plugin_dir / f"{manifest.backend_module}.py"
        if not module_file.exists():
            raise FileNotFoundError(f"Backend module {module_file} not found")

        spec = importlib.util.spec_from_file_location(
            f"plugin_{manifest.id}_{manifest.backend_module}", str(module_file)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        transformer = getattr(mod, manifest.backend_function, None)
        if transformer is None:
            raise AttributeError(
                f"Function '{manifest.backend_function}' not found in {module_file}"
            )

        self._plugins[manifest.id] = LoadedPlugin(
            manifest=manifest,
            transformer=transformer,
            plugin_dir=plugin_dir,
        )

    def get(self, plugin_id: str) -> LoadedPlugin | None:
        return self._plugins.get(plugin_id)

    def list_plugins(self, view_type: str | None = None) -> list[PluginManifest]:
        plugins = list(self._plugins.values())
        if view_type:
            plugins = [p for p in plugins if p.manifest.view_type == view_type]
        return sorted([p.manifest for p in plugins], key=lambda m: m.order)

    def transform(self, plugin_id: str, dispatches: list[dict], context: dict) -> dict:
        plugin = self._plugins.get(plugin_id)
        if not plugin:
            raise KeyError(f"Plugin '{plugin_id}' not found")
        return plugin.transformer(dispatches, context)


# Module-level singleton
plugin_registry = PluginRegistry()
