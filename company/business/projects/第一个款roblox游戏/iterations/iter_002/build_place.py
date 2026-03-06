#!/usr/bin/env python3
"""Build TechStartupTycoon.rbxlx from Lua source files.

Uses CDATA sections for script sources — required for Roblox Open Cloud API acceptance.
"""
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SRC_DIR = SCRIPT_DIR / "src"
OUTPUT_FILE = SCRIPT_DIR / "TechStartupTycoon.rbxlx"

_ref_counter = 0


def next_ref():
    global _ref_counter
    _ref_counter += 1
    return f"RBX{_ref_counter:08X}"


def read_lua(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build_place():
    """Build the complete .rbxlx XML."""
    parts = []
    parts.append('<roblox version="4">')

    def item_open(cls, name):
        ref = next_ref()
        parts.append(f'<Item class="{cls}" referent="{ref}">')
        parts.append(f'<Properties><string name="Name">{name}</string></Properties>')

    def item_close():
        parts.append('</Item>')

    def script(cls, name, source):
        ref = next_ref()
        parts.append(f'<Item class="{cls}" referent="{ref}">')
        parts.append('<Properties>')
        parts.append(f'<string name="Name">{name}</string>')
        parts.append(f'<ProtectedString name="Source"><![CDATA[{source}]]></ProtectedString>')
        parts.append('<bool name="Disabled">false</bool>')
        parts.append('</Properties>')
        parts.append('</Item>')

    # Workspace
    item_open("Workspace", "Workspace")
    item_close()

    # Lighting
    item_open("Lighting", "Lighting")
    item_close()

    # ReplicatedStorage — shared modules
    item_open("ReplicatedStorage", "ReplicatedStorage")
    script("ModuleScript", "GameConfig",
           read_lua(SRC_DIR / "ReplicatedStorage" / "GameConfig.lua"))
    item_close()

    # ServerScriptService — server scripts
    item_open("ServerScriptService", "ServerScriptService")
    script("ModuleScript", "DataManager",
           read_lua(SRC_DIR / "ServerScriptService" / "DataManager.lua"))
    script("Script", "GameManager",
           read_lua(SRC_DIR / "ServerScriptService" / "GameManager.server.lua"))
    item_close()

    # StarterGui — UI scripts
    item_open("StarterGui", "StarterGui")
    script("LocalScript", "MainGui",
           read_lua(SRC_DIR / "StarterGui" / "MainGui.lua"))
    item_close()

    # StarterPlayer > StarterPlayerScripts — client scripts
    item_open("StarterPlayer", "StarterPlayer")
    item_open("StarterPlayerScripts", "StarterPlayerScripts")
    script("LocalScript", "ClickHandler",
           read_lua(SRC_DIR / "StarterPlayerScripts" / "ClickHandler.client.lua"))
    item_close()  # StarterPlayerScripts
    item_close()  # StarterPlayer

    parts.append('</roblox>')

    content = "\n".join(parts)
    OUTPUT_FILE.write_text(content, encoding="utf-8")
    print(f"Built place file: {OUTPUT_FILE}")
    print(f"Size: {OUTPUT_FILE.stat().st_size} bytes")
    return OUTPUT_FILE


if __name__ == "__main__":
    build_place()
