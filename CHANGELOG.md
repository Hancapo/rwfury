# Changelog

This changelog was reconstructed from the Git history because some GitHub release notes were incomplete.

## Unreleased

- No unreleased changes yet.

## [0.4.0] - 2026-04-14

### Added

- DFF UV animation support, including parsing and writing of RenderWare UV animation dictionaries and material references
- High-level UV animation inspection and export APIs
- IFP support for GTA San Andreas `ANP3` packages
- High-level IFP inspection and export APIs

### Changed

- DFF reader hardened against malformed or truncated chunk sizes seen in some real-world assets.
- Added support for `ANP3` frame types `2`, `3`, and `4`.
- IFP parsing now trims IMG sector padding automatically using the internal IFP size header

Full Changelog: https://github.com/Hancapo/rwfury/compare/v0.3.2...v0.4.0

## [0.3.2] - 2026-04-14

### Changed

- Better compatibility with modded GTA SA `nodes*.dat` path files (`3791e1a`)
- README `Features` section was reorganized and cleaned up as part of the release (`27ac230`)

### Fixed

- Path parser now tolerates missing or incomplete `intersection_flags` tables and fills missing values with `0`
- Improved compatibility with modded path archives that omit or truncate intersection metadata

## [0.3.1] - 2026-04-13

### Added

- RenderWare DFF light support (`45a85a3`)
- GTA San Andreas path file support for `nodes*.dat` (`d65adcb`)

### Changed

- Major internal DFF refactor into the `rwfury/dff_parts` module set (`4604090`)
- `dff.py` was reduced to a small facade over dedicated reader, writer, API, model, and mesh-export modules
- Public exports were updated to re-export the refactored DFF internals cleanly
- Package version was bumped for the `0.3.1` release (`9e66007`)

### Removed

- Deleted the temporary `FIX_BINMESH_GENERIC_MESHES.md` note file as part of the refactor

## [0.3.0] - 2026-04-09

### Added

- COL support, including collision models, materials, surfaces, bounds, and face groups (`87e9cfb`)

### Fixed

- GenericMesh export so BinMesh-based DFFs preserve material splits correctly

### Changed

- README and package metadata were updated for the `0.3.0` release (`c2e7851`)

## [0.2.0] - 2026-04-08

### Added

- IMG archive support for:
  - GTA III / Vice City v1 archives (`.dir` + `.img`)
  - GTA San Andreas v2 `VER2` archives (`a87956a`)
- `Img.from_file()`, `find()`, `read()`, `extract()`, `extract_all()`, and `create_v2()`
- `Dff.from_bytes()` and `Txd.from_bytes()` for in-memory parsing

### Improved

- README expanded with IMG archive documentation and `from_bytes()` examples (`512e235`)
- README installation instructions updated for PyPI usage during the 0.2.0 release cycle (`0ba7c41`)

## [0.1.0] - 2026-04-08

### Added

- Initial DFF parser with plugin support (`d416558`)
- Initial TXD parser with DDS export and RGBA decoding (`d416558`)
- Generic mesh export utilities (`d416558`)
- README with API docs and usage examples (`a615260`)
- `pyproject.toml` and `LICENSE` for packaging and PyPI publishing (`4123b8a`)

### Changed

- Package renamed from `dffapi` to `rwfury` before the first tagged release (`9d03878`)
