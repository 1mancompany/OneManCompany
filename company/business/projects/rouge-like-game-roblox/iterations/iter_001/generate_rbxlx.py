#!/usr/bin/env python3
"""Generate a valid .rbxlx (Roblox XML place) file from Luau source files."""
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(SCRIPT_DIR, "src")

def read_file(path):
    with open(path, 'r') as f:
        return f.read()

# Collect scripts
scripts = {}
for service_dir in ['ServerScriptService', 'ReplicatedStorage/Shared', 'StarterPlayerScripts']:
    full_path = os.path.join(BASE, service_dir)
    if os.path.isdir(full_path):
        for fname in sorted(os.listdir(full_path)):
            if fname.endswith('.luau'):
                content = read_file(os.path.join(full_path, fname))
                module_name = fname.replace('.luau', '')
                scripts.setdefault(service_dir, []).append((module_name, content))
                print(f"  Loaded: {service_dir}/{fname} ({len(content)} bytes)")

print(f"\nTotal scripts: {sum(len(v) for v in scripts.values())}")

# Generate referent IDs
ref_counter = [0]
def ref():
    ref_counter[0] += 1
    return f"RBXB56D4E8E{ref_counter[0]:08X}"

def make_script(class_name, name, source, disabled="false"):
    r = ref()
    return f'''<Item class="{class_name}" referent="{r}">
<Properties>
<BinaryString name="AttributesSerialize"></BinaryString>
<bool name="Disabled">{disabled}</bool>
<Content name="LinkedSource"><null></null></Content>
<string name="Name">{name}</string>
<string name="ScriptGuid"></string>
<ProtectedString name="Source"><![CDATA[{source}]]></ProtectedString>
<BinaryString name="Tags"></BinaryString>
</Properties>
</Item>'''

def make_folder(name, children=""):
    r = ref()
    return f'''<Item class="Folder" referent="{r}">
<Properties>
<BinaryString name="AttributesSerialize"></BinaryString>
<string name="Name">{name}</string>
<BinaryString name="Tags"></BinaryString>
</Properties>
{children}
</Item>'''

# Build script sections
server_scripts = ""
if 'ServerScriptService' in scripts:
    for name, source in scripts['ServerScriptService']:
        cls = "Script" if name == "GameManager" else "ModuleScript"
        server_scripts += make_script(cls, name, source)

shared_scripts = ""
if 'ReplicatedStorage/Shared' in scripts:
    for name, source in scripts['ReplicatedStorage/Shared']:
        shared_scripts += make_script("ModuleScript", name, source)

client_scripts = ""
if 'StarterPlayerScripts' in scripts:
    for name, source in scripts['StarterPlayerScripts']:
        client_scripts += make_script("LocalScript", name, source)

# Build the .rbxlx
rbxlx = f'''<?xml version="1.0" encoding="utf-8"?>
<roblox xmlns:xmime="http://www.w3.org/2005/05/xmlmime" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://www.roblox.com/roblox.xsd" version="4">
<External>null</External>
<External>nil</External>
<Item class="Workspace" referent="{ref()}">
<Properties>
<string name="Name">Workspace</string>
</Properties>
<Item class="Terrain" referent="{ref()}">
<Properties>
<string name="Name">Terrain</string>
</Properties>
</Item>
<Item class="Camera" referent="{ref()}">
<Properties>
<string name="Name">Camera</string>
</Properties>
</Item>
<Item class="SpawnLocation" referent="{ref()}">
<Properties>
<bool name="Anchored">true</bool>
<bool name="CanCollide">true</bool>
<string name="Name">SpawnLocation</string>
<Vector3 name="size">
<X>8</X>
<Y>1</Y>
<Z>8</Z>
</Vector3>
</Properties>
</Item>
<Item class="Part" referent="{ref()}">
<Properties>
<bool name="Anchored">true</bool>
<string name="Name">Baseplate</string>
<bool name="Locked">true</bool>
<Vector3 name="size">
<X>512</X>
<Y>20</Y>
<Z>512</Z>
</Vector3>
<CoordinateFrame name="CFrame">
<X>0</X><Y>-10</Y><Z>0</Z>
<R00>1</R00><R01>0</R01><R02>0</R02>
<R10>0</R10><R11>1</R11><R12>0</R12>
<R20>0</R20><R21>0</R21><R22>1</R22>
</CoordinateFrame>
<Color3uint8 name="Color3uint8">4285098345</Color3uint8>
<token name="Material">816</token>
</Properties>
</Item>
</Item>
<Item class="Players" referent="{ref()}">
<Properties>
<string name="Name">Players</string>
</Properties>
</Item>
<Item class="Lighting" referent="{ref()}">
<Properties>
<string name="Name">Lighting</string>
</Properties>
</Item>
<Item class="ReplicatedFirst" referent="{ref()}">
<Properties>
<string name="Name">ReplicatedFirst</string>
</Properties>
</Item>
<Item class="ReplicatedStorage" referent="{ref()}">
<Properties>
<string name="Name">ReplicatedStorage</string>
</Properties>
{make_folder("Shared", shared_scripts)}
</Item>
<Item class="ServerScriptService" referent="{ref()}">
<Properties>
<string name="Name">ServerScriptService</string>
</Properties>
{server_scripts}
</Item>
<Item class="ServerStorage" referent="{ref()}">
<Properties>
<string name="Name">ServerStorage</string>
</Properties>
</Item>
<Item class="StarterGui" referent="{ref()}">
<Properties>
<string name="Name">StarterGui</string>
</Properties>
</Item>
<Item class="StarterPack" referent="{ref()}">
<Properties>
<string name="Name">StarterPack</string>
</Properties>
</Item>
<Item class="StarterPlayer" referent="{ref()}">
<Properties>
<string name="Name">StarterPlayer</string>
</Properties>
<Item class="StarterPlayerScripts" referent="{ref()}">
<Properties>
<string name="Name">StarterPlayerScripts</string>
</Properties>
{client_scripts}
</Item>
</Item>
<Item class="SoundService" referent="{ref()}">
<Properties>
<string name="Name">SoundService</string>
</Properties>
</Item>
<Item class="Chat" referent="{ref()}">
<Properties>
<string name="Name">Chat</string>
</Properties>
</Item>
</roblox>'''

output_path = os.path.join(SCRIPT_DIR, "SoulRift.rbxlx")
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(rbxlx)

file_size = os.path.getsize(output_path)
print(f"\nGenerated: SoulRift.rbxlx ({file_size:,} bytes)")
