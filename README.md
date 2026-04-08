# rwfury

Python library for reading and writing GTA RenderWare **DFF** (3D model) and **TXD** (texture dictionary) files. Supports GTA III, Vice City, and San Andreas.

## Features

- **DFF parsing** with full plugin support: BinMesh, Skin, HAnim, 2dfx, Material Effects, Night Colors, Collision, Specular/Reflection materials
- **DFF writing** back to binary (round-trip)
- **TXD parsing** with DDS export and raw RGBA decoding
- **Version-aware**: handles RW 3.1 (GTA III) through 3.6 (San Andreas) struct differences automatically
- **GenericMesh**: format-agnostic mesh representation with flat arrays and byte-packing helpers for easy porting to glTF, FBX, or any custom format
- Zero external dependencies (pure Python, stdlib only)

## Installation

```bash
pip install rwfury
```

Requires Python 3.10+. No external dependencies.

## Quick start

### Parse a DFF and extract meshes

```python
from rwfury import Dff

dff = Dff.from_file("model.dff")

# High-level mesh extraction (grouped by material)
for entry in dff.get_meshes():
    print(entry["name"], entry["texture"])
    for mesh in entry["meshes"]:
        print(f"  {len(mesh.positions)} vertices, {len(mesh.indices)//3} triangles")
        print(f"  UV sets: {len(mesh.texcoords)}")
```

### Format-agnostic export with GenericMesh

```python
from rwfury import Dff

dff = Dff.from_file("model.dff")

for mesh in dff.to_generic_meshes():
    # Flat arrays ready for GPU upload or any format conversion
    pos_bytes = mesh.positions_as_bytes()      # float32 LE, 3 per vertex
    idx_bytes = mesh.indices_as_bytes("u16")   # uint16 LE (or "u32")
    uv_bytes  = mesh.texcoords_as_bytes(0)     # float32 LE, 2 per vertex
    norm_bytes = mesh.normals_as_bytes()       # float32 LE, 3 per vertex

    # Material info
    print(mesh.texture_name, mesh.diffuse_color)

    # 4x4 transform matrix (row-major, 16 floats)
    print(mesh.transform)

    # Skinning data (if present)
    if mesh.has_skinning:
        bi = mesh.bone_indices_as_bytes()   # uint8, 4 per vertex
        bw = mesh.bone_weights_as_bytes()   # float32, 4 per vertex
```

### Extract textures from a TXD

```python
from rwfury import Txd

txd = Txd.from_file("textures.txd")

# Export all textures as .dds files
txd.export_to_dds("output_folder/")

# Or get raw RGBA pixel data (for compression, processing, etc.)
for tex in txd.textures:
    rgba_mipmaps, has_alpha = tex.to_rgba()
    # rgba_mipmaps[0] = bytes, width * height * 4, R,G,B,A order
    print(f"{tex.name}: {tex.width}x{tex.height}, alpha={has_alpha}")
```

### Write a modified DFF

```python
from rwfury import Dff

dff = Dff.from_file("original.dff")

# Modify frames, geometry, materials, etc.
dff.frames[0].name = "renamed_frame"

# Write back to binary
dff.to_file("modified.dff")
```

### Access low-level data

```python
from rwfury import Dff

dff = Dff.from_file("model.dff")

# Frames (skeleton/hierarchy)
for frame in dff.frames:
    print(frame.name, frame.position, frame.parent)
    if frame.hanim:
        print(f"  HAnim: {len(frame.hanim.bones)} bones")

# Geometry details
for geom in dff.geometries:
    print(f"{len(geom.vertices)} verts, {len(geom.triangles)} tris")
    print(f"UV sets: {geom.num_uv_sets}, flags: 0x{geom.flags:04X}")

    # Plugins
    if geom.bin_mesh:
        print(f"BinMesh: {len(geom.bin_mesh.splits)} splits")
    if geom.skin:
        print(f"Skin: {geom.skin.num_bones} bones")
    if geom.night_colors:
        print(f"Night colors: {len(geom.night_colors)} vertices")
    if geom.effects_2dfx:
        print(f"2dfx: {len(geom.effects_2dfx)} effects")

    # Materials
    for mat in geom.materials:
        print(f"  {mat.texture_name} color={mat.color}")
        if mat.specular_texture:
            print(f"    specular: {mat.specular_texture} level={mat.specular_level}")
        if mat.reflection:
            print(f"    reflection: intensity={mat.reflection['intensity']}")

# Collision data
if dff.collision:
    print(f"Embedded collision: {len(dff.collision.raw)} bytes")
```

## API reference

### Core classes

| Class | Description |
|-------|-------------|
| `Dff` | DFF file parser/writer. `from_file(path)`, `to_file(path)`, `get_meshes()`, `to_generic_meshes()` |
| `Txd` | TXD file parser. `from_file(path)`, `export_to_dds(folder)` |
| `TxdTexture` | Single texture entry. `to_rgba()` decodes any format to raw RGBA bytes |
| `GenericMesh` | Flat-array mesh with `*_as_bytes()` helpers for format-agnostic export |

### DFF data classes

| Class | Description |
|-------|-------------|
| `DffGeometry` | Vertices, normals, triangles, texcoords, vertex colors, materials, and plugins |
| `DffMaterial` | Color, texture name, ambient/diffuse/specular, material effects, specular/reflection extensions |
| `DffFrame` | Name, position, rotation matrix, parent index, HAnim skeleton data |
| `DffAtomic` | Links a frame to a geometry |
| `MorphTarget` | Per-morph-target vertices and normals with bounding sphere |
| `BinMeshPLG` | Triangle strip/list splits by material |
| `SkinPLG` | Bone indices, weights, and inverse bind matrices per vertex |
| `HAnimPLG` | Skeleton hierarchy with bone IDs and flags |
| `Effect2dfxEntry` | 2dfx effect (lights, particles, etc.) with position and typed data |
| `CollisionData` | Raw embedded COL data blob |

### GenericMesh properties and methods

| Member | Description |
|--------|-------------|
| `vertex_count`, `triangle_count` | Derived from flat array lengths |
| `has_normals`, `has_colors`, `has_skinning` | Quick checks for optional data |
| `uv_set_count` | Number of UV coordinate sets |
| `positions_as_bytes()` | Packed `float32` LE (12 bytes/vertex) |
| `normals_as_bytes()` | Packed `float32` LE (12 bytes/vertex) |
| `texcoords_as_bytes(uv_set)` | Packed `float32` LE (8 bytes/vertex) |
| `colors_as_bytes()` | Packed `uint8` RGBA (4 bytes/vertex) |
| `indices_as_bytes(fmt)` | `"u16"` or `"u32"` packed indices |
| `bone_indices_as_bytes()` | Packed `uint8` (4 bytes/vertex) |
| `bone_weights_as_bytes()` | Packed `float32` LE (16 bytes/vertex) |

## Supported formats

| Game | RW Version | DFF | TXD |
|------|-----------|-----|-----|
| GTA III | 3.1 - 3.3 | Read/Write | Read + DDS export |
| GTA Vice City | 3.4 - 3.5 | Read/Write | Read + DDS export |
| GTA San Andreas | 3.6 | Read/Write | Read + DDS export |

TXD supports D3D8/D3D9 platform textures: PAL4, PAL8, 16-bit (R5G6B5, A1R5G5B5, A4R4G4B4), 32-bit (A8R8G8B8, X8R8G8B8), and DXT1/DXT3/DXT5 compressed.

## License

MIT
