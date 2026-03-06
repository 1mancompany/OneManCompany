#!/usr/bin/env python3
"""Build a .rbxlx (Roblox XML place) file from Lua source files.

This script reads the Lua scripts in src/ and generates a valid .rbxlx file
that can be uploaded via the Roblox Open Cloud API.
"""
import xml.etree.ElementTree as ET
from xml.dom import minidom
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SRC_DIR = SCRIPT_DIR / "src"
OUTPUT_FILE = SCRIPT_DIR / "ShadowDungeonDescent.rbxlx"

# Incrementing referent counter
_ref_counter = 0


def next_ref():
    global _ref_counter
    _ref_counter += 1
    return f"RBX{_ref_counter:08X}"


def read_lua(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def make_properties(item_el, props: dict):
    props_el = ET.SubElement(item_el, "Properties")
    for ptype, pname, pvalue in props:
        p = ET.SubElement(props_el, ptype, {"name": pname})
        if ptype == "BinaryString":
            p.text = ""
        elif ptype == "bool":
            p.text = str(pvalue).lower()
        elif ptype == "Vector3":
            ET.SubElement(p, "X").text = str(pvalue[0])
            ET.SubElement(p, "Y").text = str(pvalue[1])
            ET.SubElement(p, "Z").text = str(pvalue[2])
        elif ptype == "Color3":
            ET.SubElement(p, "R").text = str(pvalue[0])
            ET.SubElement(p, "G").text = str(pvalue[1])
            ET.SubElement(p, "B").text = str(pvalue[2])
        elif ptype == "CoordinateFrame":
            for k, v in zip(["X", "Y", "Z", "R00", "R01", "R02",
                              "R10", "R11", "R12", "R20", "R21", "R22"], pvalue):
                ET.SubElement(p, k).text = str(v)
        else:
            p.text = str(pvalue)
    return props_el


def make_item(parent_el, class_name: str, name: str = "", extra_props=None):
    ref = next_ref()
    item = ET.SubElement(parent_el, "Item", {
        "class": class_name,
        "referent": ref,
    })
    props = [("string", "Name", name or class_name)]
    if extra_props:
        props.extend(extra_props)
    make_properties(item, props)
    return item


def add_script(parent_el, class_name: str, name: str, source: str):
    """Add a Script or LocalScript item with source code."""
    item = make_item(parent_el, class_name, name, [
        ("ProtectedString", "Source", source),
        ("bool", "Disabled", False),
    ])
    return item


def build_place():
    """Build the complete .rbxlx XML."""
    root = ET.Element("roblox", {
        "xmlns:xmime": "http://www.w3.org/2005/05/xmlmime",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:noNamespaceSchemaLocation": "http://www.roblox.com/roblox.xsd",
        "version": "4",
    })

    # ── Workspace ────────────────────────────────────────
    workspace = make_item(root, "Workspace", "Workspace", [
        ("bool", "FilteringEnabled", True),
    ])

    # Baseplate (dark dungeon floor)
    make_item(workspace, "Part", "Baseplate", [
        ("bool", "Anchored", True),
        ("Vector3", "size", [512, 1, 512]),
        ("CoordinateFrame", "CFrame", [0, -0.5, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]),
        ("Color3", "Color3uint8", [20, 20, 30]),
        ("float", "Transparency", 0),
    ])

    # Camera
    make_item(workspace, "Camera", "Camera")

    # ── Lighting (dark dungeon ambiance) ─────────────────
    make_item(root, "Lighting", "Lighting", [
        ("float", "Brightness", 1),
        ("float", "ClockTime", 0),
        ("Color3", "Ambient", [0.15, 0.15, 0.2]),
        ("Color3", "OutdoorAmbient", [0.1, 0.1, 0.15]),
        ("float", "FogEnd", 300),
        ("Color3", "FogColor", [0.05, 0.05, 0.1]),
    ])

    # ── ReplicatedStorage — shared modules ───────────────
    rep_storage = make_item(root, "ReplicatedStorage", "ReplicatedStorage")

    game_config_src = read_lua(SRC_DIR / "ReplicatedStorage" / "GameConfig.lua")
    add_script(rep_storage, "ModuleScript", "GameConfig", game_config_src)

    dungeon_gen_src = read_lua(SRC_DIR / "ReplicatedStorage" / "DungeonGenerator.lua")
    add_script(rep_storage, "ModuleScript", "DungeonGenerator", dungeon_gen_src)

    loot_src = read_lua(SRC_DIR / "ReplicatedStorage" / "LootSystem.lua")
    add_script(rep_storage, "ModuleScript", "LootSystem", loot_src)

    # ── ServerScriptService ──────────────────────────────
    server_scripts = make_item(root, "ServerScriptService", "ServerScriptService")

    dm_src = read_lua(SRC_DIR / "ServerScriptService" / "DataManager.lua")
    add_script(server_scripts, "ModuleScript", "DataManager", dm_src)

    cm_src = read_lua(SRC_DIR / "ServerScriptService" / "CombatManager.lua")
    add_script(server_scripts, "ModuleScript", "CombatManager", cm_src)

    gm_src = read_lua(SRC_DIR / "ServerScriptService" / "GameManager.server.lua")
    add_script(server_scripts, "Script", "GameManager", gm_src)

    # ── StarterGui ───────────────────────────────────────
    starter_gui = make_item(root, "StarterGui", "StarterGui")
    gui_src = read_lua(SRC_DIR / "StarterGui" / "MainGui.lua")
    add_script(starter_gui, "LocalScript", "MainGui", gui_src)

    # ── StarterPlayer > StarterPlayerScripts ─────────────
    starter_player = make_item(root, "StarterPlayer", "StarterPlayer")
    starter_player_scripts = make_item(starter_player, "StarterPlayerScripts", "StarterPlayerScripts")

    ctrl_src = read_lua(SRC_DIR / "StarterPlayerScripts" / "PlayerController.client.lua")
    add_script(starter_player_scripts, "LocalScript", "PlayerController", ctrl_src)

    # ── Write XML ────────────────────────────────────────
    rough_string = ET.tostring(root, encoding="unicode")
    dom = minidom.parseString(rough_string)
    pretty = dom.toprettyxml(indent="  ", encoding=None)

    # Fix xml declaration
    lines = pretty.split("\n")
    if lines[0].startswith("<?xml"):
        lines[0] = '<?xml version="1.0" encoding="utf-8"?>'

    OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Built place file: {OUTPUT_FILE}")
    print(f"Size: {OUTPUT_FILE.stat().st_size} bytes")
    return OUTPUT_FILE


if __name__ == "__main__":
    build_place()
