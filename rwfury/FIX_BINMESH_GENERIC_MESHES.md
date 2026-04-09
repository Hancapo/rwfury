# Fix `Dff.to_generic_meshes()` Material Loss For BinMesh-Based DFFs

## Problem

Some DFFs contain the correct material splits in `geometry.bin_mesh.splits`, but `geometry.triangles` does not preserve the right `material_index` values.

`rwfury.dff.Dff.to_generic_meshes()` currently builds generic meshes from `geom.triangles`, so those files collapse to a single mesh/material even though the DFF really has multiple materials.

This breaks downstream conversion to YDR because the converter only receives one generic mesh.

## Real Repro

File:

- `city1_lesn.dff`

Observed with current parser:

- `len(dff.geometries) == 1`
- `len(dff.geometries[0].materials) == 6`
- `len(dff.geometries[0].bin_mesh.splits) == 6`
- `len(dff.geometries[0].triangles) == 2644`
- all `geom.triangles[*][3] == 0`
- `len(dff.to_generic_meshes()) == 1`

So the source DFF clearly has 6 material splits, but `to_generic_meshes()` exports only 1.

## Root Cause

Current implementation in `dff.py`:

- iterates `for mat_idx, material in enumerate(geom.materials)`
- collects triangles with `mat_tris = [t for t in geom.triangles if t[3] == mat_idx]`

That only works if `geom.triangles` already has valid material indices.

For files like `city1_lesn.dff`, the authoritative split data is in:

- `geom.bin_mesh.splits`

not in:

- `geom.triangles[*][3]`

## Correct Fix

`Dff.to_generic_meshes()` should prefer `geom.bin_mesh.splits` when available.

Recommended behavior:

1. If `geom.bin_mesh` exists and has splits:
- build one `GenericMesh` per `BinMeshSplit`
- use `split.material_index` to select the material
- use `split.indices` as the source index buffer

2. If `geom.bin_mesh` is missing:
- keep current triangle-based fallback

## Important Detail

`BinMeshPLG.flags` indicates whether the split indices are triangle list or triangle strip:

- `0` = triangle list
- `1` = triangle strip

So if `flags == 1`, the split indices must be expanded from strip to triangle list before building the generic mesh.

The output `GenericMesh.indices` should still be normal triangle indices in local vertex space.

## Suggested Implementation Shape

Inside `Dff.to_generic_meshes()`:

- branch on `geom.bin_mesh and geom.bin_mesh.splits`
- for each split:
  - resolve `material = geom.materials[split.material_index]`
  - expand strip if needed
  - collect used vertex indices from that split
  - remap to local indices
  - flatten positions/normals/uvs/colors/skinning only for used vertices
  - emit one `GenericMesh`

This should mirror the existing triangle-based code path, just changing the source of the per-material index buffer.

## Acceptance Criteria

For `city1_lesn.dff`:

- `len(dff.to_generic_meshes()) == 6`
- generic mesh texture/material data matches the 6 DFF materials
- downstream YDR conversion can preserve 6 materials instead of 1

More generally:

- existing DFFs that already work through triangle material indices must keep working
- bin-mesh-only material splits must no longer collapse to one mesh

## Why Fix It Here

This is a parser/export issue in `rwfury`, not really a converter issue.

The converter can only preserve materials that `rwfury.to_generic_meshes()` exposes.

If `rwfury` collapses multiple material splits into one generic mesh, every downstream consumer will inherit the bug.
