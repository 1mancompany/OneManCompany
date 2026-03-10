#!/usr/bin/env python3
"""Generate a valid .rbxlx (Roblox XML place) file from Luau source files.
Uses the simplified format proven to work with Roblox Open Cloud API."""
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
                # Detect script type from suffix, then strip it from name
                is_server_script = '.server' in module_name
                is_client_script = '.client' in module_name
                module_name = module_name.replace('.server', '').replace('.client', '')
                scripts.setdefault(service_dir, []).append((module_name, content, is_server_script, is_client_script))
                print(f"  Loaded: {service_dir}/{fname} -> {module_name} ({len(content)} bytes)")

print(f"\nTotal scripts: {sum(len(v) for v in scripts.values())}")

# Simple integer referent counter (matching the working format)
ref_counter = [0]
def ref():
    r = ref_counter[0]
    ref_counter[0] += 1
    return str(r)

def make_script(class_name, name, source):
    r = ref()
    return f'''  <Item class="{class_name}" referent="{r}">
    <Properties>
      <string name="Name">{name}</string>
      <string name="Source"><![CDATA[{source}]]></string>
    </Properties>
  </Item>
'''

def make_folder(name, children=""):
    r = ref()
    return f'''  <Item class="Folder" referent="{r}">
    <Properties>
      <string name="Name">{name}</string>
    </Properties>
{children}  </Item>
'''

# Build script sections
server_scripts = ""
if 'ServerScriptService' in scripts:
    for name, source, is_server, is_client in scripts['ServerScriptService']:
        cls = "Script" if is_server else "ModuleScript"
        server_scripts += make_script(cls, name, source)

shared_scripts = ""
if 'ReplicatedStorage/Shared' in scripts:
    for name, source, is_server, is_client in scripts['ReplicatedStorage/Shared']:
        shared_scripts += make_script("ModuleScript", name, source)

client_scripts = ""
if 'StarterPlayerScripts' in scripts:
    for name, source, is_server, is_client in scripts['StarterPlayerScripts']:
        cls = "LocalScript" if is_client else "ModuleScript"
        client_scripts += make_script(cls, name, source)

# Build the .rbxlx (matching the proven working format from iter_001)
rbxlx = f'''<roblox version="4">
  <Item class="Chat" referent="{ref()}">
    <Properties>
      <string name="Name">Chat</string>
    </Properties>
  </Item>
  <Item class="Lighting" referent="{ref()}">
    <Properties>
      <string name="Name">Lighting</string>
      <float name="Brightness">2</float>
      <token name="Technology">3</token>
    </Properties>
  </Item>
  <Item class="Players" referent="{ref()}">
    <Properties>
      <string name="Name">Players</string>
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
{make_folder("Shared", shared_scripts)}  </Item>
  <Item class="ServerScriptService" referent="{ref()}">
    <Properties>
      <string name="Name">ServerScriptService</string>
    </Properties>
{server_scripts}  </Item>
  <Item class="ServerStorage" referent="{ref()}">
    <Properties>
      <string name="Name">ServerStorage</string>
    </Properties>
  </Item>
  <Item class="SoundService" referent="{ref()}">
    <Properties>
      <string name="Name">SoundService</string>
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
{client_scripts}    </Item>
  </Item>
  <Item class="Workspace" referent="{ref()}">
    <Properties>
      <string name="Name">Workspace</string>
    </Properties>
    <Item class="SpawnLocation" referent="{ref()}">
      <Properties>
        <bool name="Anchored">true</bool>
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
      </Properties>
    </Item>
  </Item>
</roblox>'''

output_path = os.path.join(SCRIPT_DIR, "SoulRift.rbxlx")
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(rbxlx)

file_size = os.path.getsize(output_path)
print(f"\nGenerated: SoulRift.rbxlx ({file_size:,} bytes)")
