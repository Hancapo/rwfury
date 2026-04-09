"""DFF (RenderWare Clump) parser and mesh extractor for GTA RenderWare."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .generic_mesh import GenericMesh

from .rwbinary import (
    RwBinaryReader,
    RwBinaryWriter,
    ChunkHeader,
    RW_STRUCT,
    RW_STRING,
    RW_EXTENSION,
    RW_TEXTURE,
    RW_MATERIAL,
    RW_MATERIAL_LIST,
    RW_FRAME_LIST,
    RW_GEOMETRY,
    RW_CLUMP,
    RW_ATOMIC,
    RW_GEOMETRY_LIST,
    RW_FRAME_NAME,
    RW_BIN_MESH,
    RW_SKIN,
    RW_HANIM,
    RW_MAT_EFFECTS,
    RW_2DFX,
    RW_NIGHT_COLORS,
    RW_COLLISION,
    RW_SPECULAR_MAT,
    RW_REFLECTION_MAT,
    GEO_TRISTRIP,
    GEO_POSITIONS,
    GEO_TEXTURED,
    GEO_PRELIT,
    GEO_NORMALS,
    GEO_TEXTURED2,
    GEO_NATIVE,
)


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

def _rw_version_unpack(version: int) -> tuple[int, int, int, int]:
    """Decode a RW chunk version field into (major, minor, revision, build).

    For library-stamped versions (post-3.1):
      version = 0x1803FFFF -> RW 3.6.0.3, build 0xFFFF
    For old pre-3.1 versions the raw value is the version directly.
    """
    if version & 0xFFFF0000:
        # Library-stamped: upper 16 bits encode version, lower 16 = build
        packed = (version >> 16) & 0xFFFF
        # packed = ((major - 3) << 14) | (minor << 10) | (revision << 6) | (build_upper)
        # Actually the encoding is:
        #   bits 14-15: major - 3
        #   bits 10-13: minor
        #   bits  2-9:  revision (upper 8)
        #   bits  0-1:  sub-revision
        major = ((packed >> 14) & 0x03) + 3
        minor = (packed >> 10) & 0x0F
        revision = (packed >> 2) & 0xFF
        sub = packed & 0x03
        build = version & 0xFFFF
        return (major, minor, revision, sub)
    else:
        # Old format (RW 3.1 and below)
        return (3, version >> 8, version & 0xFF, 0)


def _rw_version_raw(version: int) -> int:
    """Convert a chunk version to a comparable raw version number (e.g. 0x36003)."""
    major, minor, rev, sub = _rw_version_unpack(version)
    return (major << 16) | (minor << 12) | (rev << 4) | sub


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Mesh:
    """High-level mesh data for a single material split."""
    positions: list[tuple[float, float, float]]
    indices: list[int]
    texcoords: list[list[tuple[float, float]]]
    normals: list[tuple[float, float, float]] | None = None
    vertex_colors: list[tuple[int, int, int, int]] | None = None


@dataclass
class DffMaterial:
    color: tuple[int, int, int, int] = (255, 255, 255, 255)
    texture_name: str = ""
    mask_name: str = ""
    ambient: float = 1.0
    diffuse: float = 1.0
    specular: float = 0.0
    # Material Effects PLG
    mat_fx_type: int = 0
    mat_fx_data: dict | None = None
    # Specular Material PLG
    specular_level: float = 0.0
    specular_texture: str = ""
    # Reflection Material PLG
    reflection: dict | None = None


@dataclass
class BinMeshSplit:
    material_index: int = 0
    indices: list[int] = field(default_factory=list)


@dataclass
class BinMeshPLG:
    flags: int = 0  # 0 = tri list, 1 = tri strip
    splits: list[BinMeshSplit] = field(default_factory=list)


@dataclass
class SkinPLG:
    num_bones: int = 0
    used_bone_indices: list[int] = field(default_factory=list)
    bone_indices: list[tuple[int, int, int, int]] = field(default_factory=list)
    weights: list[tuple[float, float, float, float]] = field(default_factory=list)
    inverse_matrices: list[tuple] = field(default_factory=list)  # list of 16-float tuples


@dataclass
class HAnimBone:
    node_id: int = 0
    node_index: int = 0
    flags: int = 0


@dataclass
class HAnimPLG:
    version: int = 0x100
    node_id: int = 0
    hierarchy_flags: int = 0
    key_frame_size: int = 36
    bones: list[HAnimBone] = field(default_factory=list)


@dataclass
class Effect2dfxLight:
    color: tuple[int, int, int, int] = (255, 255, 255, 255)
    corona_far_clip: float = 0.0
    pointlight_range: float = 0.0
    corona_size: float = 0.0
    shadow_size: float = 0.0
    corona_show_mode: int = 0
    corona_enable_reflection: int = 0
    corona_flare_type: int = 0
    shadow_color_multiplier: int = 0
    flags1: int = 0
    corona_tex_name: str = ""
    shadow_tex_name: str = ""
    shadow_z_distance: int = 0
    flags2: int = 0
    look_direction: tuple[int, int, int] | None = None


@dataclass
class Effect2dfxParticle:
    effect_name: str = ""


@dataclass
class Effect2dfxEntry:
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    entry_type: int = 0
    data: object = None  # Light, Particle, or raw bytes


@dataclass
class MorphTarget:
    bounding_sphere: tuple[float, float, float, float] = (0, 0, 0, 0)
    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)


@dataclass
class CollisionData:
    raw: bytes = b""  # Raw COL data blob

    def parse(self):
        """Parse the raw collision bytes into a ColModel.

        Returns a ColModel if the data is valid, None otherwise.
        """
        if not self.raw:
            return None
        from .col import Col
        col = Col.from_bytes(self.raw)
        return col.models[0] if col.models else None


@dataclass
class DffGeometry:
    flags: int = 0
    num_uv_sets: int = 0
    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)
    triangles: list[tuple[int, int, int, int]] = field(default_factory=list)
    texcoord_sets: list[list[tuple[float, float]]] = field(default_factory=list)
    vertex_colors: list[tuple[int, int, int, int]] = field(default_factory=list)
    materials: list[DffMaterial] = field(default_factory=list)
    bounding_sphere: tuple[float, float, float, float] = (0, 0, 0, 0)
    morph_targets: list[MorphTarget] = field(default_factory=list)
    # Plugins
    bin_mesh: BinMeshPLG | None = None
    skin: SkinPLG | None = None
    night_colors: list[tuple[int, int, int, int]] | None = None
    effects_2dfx: list[Effect2dfxEntry] | None = None


@dataclass
class DffFrame:
    name: str = ""
    rotation_matrix: tuple = (1, 0, 0, 0, 1, 0, 0, 0, 1)
    position: tuple[float, float, float] = (0, 0, 0)
    parent: int = -1
    hanim: HAnimPLG | None = None


@dataclass
class DffAtomic:
    frame_index: int = 0
    geometry_index: int = 0
    flags: int = 0


# ---------------------------------------------------------------------------
# Main DFF class
# ---------------------------------------------------------------------------

class Dff:
    """DFF (RenderWare Clump) parser with full plugin support."""

    def __init__(self):
        self.frames: list[DffFrame] = []
        self.geometries: list[DffGeometry] = []
        self.atomics: list[DffAtomic] = []
        self.collision: CollisionData | None = None
        self.rw_version: int = 0

    @classmethod
    def from_file(cls, path: str) -> Dff:
        dff = cls()
        with RwBinaryReader.from_file(path) as reader:
            dff._parse(reader)
        return dff

    @classmethod
    def from_bytes(cls, data: bytes) -> Dff:
        """Parse a DFF from raw bytes (e.g. read from an IMG archive)."""
        import io
        dff = cls()
        reader = RwBinaryReader(io.BytesIO(data))
        dff._parse(reader)
        return dff

    def get_meshes(self) -> list[dict]:
        """Extract mesh data grouped by atomic/material.

        Returns a list of dicts, each with:
            meshes: list[Mesh]  -- one Mesh per material in the geometry
            texture: str        -- diffuse texture name of the first material
            name: str           -- frame name
        """
        results = []

        for atomic in self.atomics:
            geom = self.geometries[atomic.geometry_index]
            frame = self.frames[atomic.frame_index] if atomic.frame_index < len(self.frames) else DffFrame()

            meshes = []
            for mat_idx, material in enumerate(geom.materials):
                mat_tris = [t for t in geom.triangles if t[3] == mat_idx]
                if not mat_tris:
                    continue

                used_indices = sorted({i for a, b, c, _ in mat_tris for i in (a, b, c)})
                remap = {old: new for new, old in enumerate(used_indices)}

                positions = [geom.vertices[i] for i in used_indices]
                indices = [remap[i] for a, b, c, _ in mat_tris for i in (a, b, c)]

                texcoords = []
                for uv_layer in geom.texcoord_sets:
                    texcoords.append([uv_layer[i] for i in used_indices])

                normals = [geom.normals[i] for i in used_indices] if geom.normals else None
                colors = [geom.vertex_colors[i] for i in used_indices] if geom.vertex_colors else None

                meshes.append(Mesh(
                    positions=positions,
                    indices=indices,
                    texcoords=texcoords,
                    normals=normals,
                    vertex_colors=colors,
                ))

            results.append({
                "meshes": meshes,
                "texture": geom.materials[0].texture_name if geom.materials else "",
                "name": frame.name,
            })

        return results

    def to_generic_meshes(self) -> list[GenericMesh]:
        """Export all meshes as flat, format-agnostic GenericMesh objects.

        Each material split in each atomic produces one GenericMesh with:
        - Flat position/normal/texcoord/color arrays
        - Triangle indices (remapped to local vertex space)
        - 4x4 transform from the associated frame
        - Skinning data (if the geometry has a SkinPLG)
        """
        results: list[GenericMesh] = []

        for atomic in self.atomics:
            geom = self.geometries[atomic.geometry_index]
            frame = (self.frames[atomic.frame_index]
                     if atomic.frame_index < len(self.frames) else DffFrame())

            # Build 4x4 row-major transform from frame
            r = frame.rotation_matrix  # 9 floats (3x3 row-major)
            p = frame.position
            xform = [
                r[0], r[1], r[2], 0.0,
                r[3], r[4], r[5], 0.0,
                r[6], r[7], r[8], 0.0,
                p[0], p[1], p[2], 1.0,
            ]

            if geom.bin_mesh and geom.bin_mesh.splits:
                for split in geom.bin_mesh.splits:
                    mesh = self._build_generic_mesh_from_indices(
                        geom=geom,
                        frame=frame,
                        transform=xform,
                        material_index=split.material_index,
                        source_indices=self._expand_bin_mesh_indices(
                            split.indices, geom.bin_mesh.flags
                        ),
                    )
                    if mesh:
                        results.append(mesh)
                continue

            for mat_idx, _material in enumerate(geom.materials):
                source_indices = [
                    i for a, b, c, tri_mat in geom.triangles
                    if tri_mat == mat_idx
                    for i in (a, b, c)
                ]
                mesh = self._build_generic_mesh_from_indices(
                    geom=geom,
                    frame=frame,
                    transform=xform,
                    material_index=mat_idx,
                    source_indices=source_indices,
                )
                if mesh:
                    results.append(mesh)

        return results

    @staticmethod
    def _expand_bin_mesh_indices(indices: list[int], flags: int) -> list[int]:
        if flags != 1:
            return list(indices)

        triangles: list[int] = []
        for i in range(len(indices) - 2):
            a, b, c = indices[i], indices[i + 1], indices[i + 2]
            if a == b or b == c or a == c:
                continue
            if i % 2:
                triangles.extend((b, a, c))
            else:
                triangles.extend((a, b, c))
        return triangles

    @staticmethod
    def _build_generic_mesh_from_indices(
        geom: DffGeometry,
        frame: DffFrame,
        transform: list[float],
        material_index: int,
        source_indices: list[int],
    ) -> GenericMesh | None:
        if material_index < 0 or material_index >= len(geom.materials):
            return None
        if not source_indices:
            return None
        if any(i < 0 or i >= len(geom.vertices) for i in source_indices):
            return None

        material = geom.materials[material_index]
        used = sorted(set(source_indices))
        remap = {old: new for new, old in enumerate(used)}

        positions: list[float] = []
        for i in used:
            positions.extend(geom.vertices[i])

        normals: list[float] = []
        if geom.normals:
            for i in used:
                normals.extend(geom.normals[i])

        texcoords: list[list[float]] = []
        for uv_layer in geom.texcoord_sets:
            flat_uv: list[float] = []
            for i in used:
                flat_uv.extend(uv_layer[i])
            texcoords.append(flat_uv)

        colors: list[int] = []
        if geom.vertex_colors:
            for i in used:
                colors.extend(geom.vertex_colors[i])

        bone_idx_flat: list[int] = []
        bone_wt_flat: list[float] = []
        if geom.skin:
            for i in used:
                bone_idx_flat.extend(geom.skin.bone_indices[i])
                bone_wt_flat.extend(geom.skin.weights[i])

        return GenericMesh(
            name=frame.name,
            material_index=material_index,
            positions=positions,
            normals=normals,
            texcoords=texcoords,
            colors=colors,
            indices=[remap[i] for i in source_indices],
            texture_name=material.texture_name,
            mask_name=material.mask_name,
            diffuse_color=material.color,
            ambient=material.ambient,
            diffuse=material.diffuse,
            specular=material.specular,
            transform=transform,
            bone_indices=bone_idx_flat,
            bone_weights=bone_wt_flat,
        )

    def to_file(self, path: str):
        """Write this DFF back to a binary file."""
        with RwBinaryWriter.to_file(path) as writer:
            self._write(writer)

    # -----------------------------------------------------------------------
    # Reading
    # -----------------------------------------------------------------------

    def _parse(self, reader: RwBinaryReader):
        header = reader.read_chunk_header()
        if header.id != RW_CLUMP:
            raise ValueError(f"Not a DFF: expected 0x{RW_CLUMP:04X}, got 0x{header.id:04X}")
        clump_end = reader.tell() + header.size
        self.rw_version = header.version

        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT
        _num_atomics = reader.read_u32()
        if struct_h.size >= 12:
            reader.read_u32()  # num_lights
            reader.read_u32()  # num_cameras
        elif struct_h.size > 4:
            reader.skip(struct_h.size - 4)

        while reader.tell() < clump_end:
            child = reader.read_chunk_header()
            child_end = reader.tell() + child.size

            if child.id == RW_FRAME_LIST:
                self._parse_frame_list(reader, child_end)
            elif child.id == RW_GEOMETRY_LIST:
                self._parse_geometry_list(reader, child_end)
            elif child.id == RW_ATOMIC:
                self._parse_atomic(reader, child_end)
            elif child.id == RW_EXTENSION:
                self._parse_clump_extension(reader, child_end)
            else:
                reader.seek(child_end)

    def _parse_clump_extension(self, reader: RwBinaryReader, chunk_end: int):
        while reader.tell() < chunk_end:
            plugin = reader.read_chunk_header()
            plugin_end = reader.tell() + plugin.size

            if plugin.id == RW_COLLISION:
                self.collision = CollisionData(raw=reader.read_bytes(plugin.size))
            else:
                reader.seek(plugin_end)

    # --- Frame List ---

    def _parse_frame_list(self, reader: RwBinaryReader, chunk_end: int):
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT

        num_frames = reader.read_u32()

        for _ in range(num_frames):
            rot = struct.unpack("<9f", reader.read_bytes(36))
            pos = struct.unpack("<3f", reader.read_bytes(12))
            parent_idx = reader.read_i32()
            _flags = reader.read_u32()
            self.frames.append(DffFrame(rotation_matrix=rot, position=pos, parent=parent_idx))

        for i in range(num_frames):
            if reader.tell() >= chunk_end:
                break
            ext = reader.read_chunk_header()
            if ext.id != RW_EXTENSION:
                reader.seek(reader.tell() - 12 + ext.size)
                continue
            ext_end = reader.tell() + ext.size

            while reader.tell() < ext_end:
                plugin = reader.read_chunk_header()
                plugin_end = reader.tell() + plugin.size

                if plugin.id == RW_FRAME_NAME and plugin.size > 0:
                    self.frames[i].name = reader.read_string(plugin.size)
                elif plugin.id == RW_HANIM:
                    self.frames[i].hanim = self._parse_hanim(reader, plugin_end)
                else:
                    reader.seek(plugin_end)

    def _parse_hanim(self, reader: RwBinaryReader, chunk_end: int) -> HAnimPLG:
        hanim = HAnimPLG()
        hanim.version = reader.read_u32()
        hanim.node_id = reader.read_u32()
        num_nodes = reader.read_u32()

        if num_nodes > 0:
            hanim.hierarchy_flags = reader.read_u32()
            hanim.key_frame_size = reader.read_u32()
            for _ in range(num_nodes):
                bone = HAnimBone(
                    node_id=reader.read_u32(),
                    node_index=reader.read_u32(),
                    flags=reader.read_u32(),
                )
                hanim.bones.append(bone)

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
        return hanim

    # --- Geometry List ---

    def _parse_geometry_list(self, reader: RwBinaryReader, chunk_end: int):
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT
        num_geometries = reader.read_u32()

        for _ in range(num_geometries):
            if reader.tell() >= chunk_end:
                break
            geom_header = reader.read_chunk_header()
            if geom_header.id == RW_GEOMETRY:
                geom_end = reader.tell() + geom_header.size
                self.geometries.append(self._parse_geometry(reader, geom_end, geom_header.version))
            else:
                reader.skip(geom_header.size)

    def _parse_geometry(self, reader: RwBinaryReader, chunk_end: int, version: int) -> DffGeometry:
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT

        geom = DffGeometry()
        geom.flags = reader.read_u16()
        geom.num_uv_sets = reader.read_u8()
        _native_flags = reader.read_u8()
        num_triangles = reader.read_u32()
        num_vertices = reader.read_u32()
        num_morph_targets = reader.read_u32()

        rw_ver = _rw_version_raw(version)

        # Pre-RW 3.4: surface properties (ambient, specular, diffuse) are in geometry struct
        if rw_ver < 0x34000:
            _ambient = reader.read_f32()
            _specular = reader.read_f32()
            _diffuse = reader.read_f32()

        if geom.num_uv_sets == 0:
            if geom.flags & GEO_TEXTURED2:
                geom.num_uv_sets = 2
            elif geom.flags & GEO_TEXTURED:
                geom.num_uv_sets = 1

        is_native = bool(geom.flags & GEO_NATIVE)

        if not is_native:
            # Vertex colors
            if geom.flags & GEO_PRELIT:
                for _ in range(num_vertices):
                    geom.vertex_colors.append((
                        reader.read_u8(), reader.read_u8(),
                        reader.read_u8(), reader.read_u8(),
                    ))

            # Texcoords
            for _ in range(geom.num_uv_sets):
                uv_set = []
                for _ in range(num_vertices):
                    uv_set.append((reader.read_f32(), reader.read_f32()))
                geom.texcoord_sets.append(uv_set)

            # Triangles (RW stores: v2, v1, mat, v3)
            for _ in range(num_triangles):
                v2 = reader.read_u16()
                v1 = reader.read_u16()
                mat_id = reader.read_u16()
                v3 = reader.read_u16()
                geom.triangles.append((v1, v2, v3, mat_id))

        # Morph targets
        for mt_i in range(num_morph_targets):
            mt = MorphTarget()
            mt.bounding_sphere = (
                reader.read_f32(), reader.read_f32(),
                reader.read_f32(), reader.read_f32(),
            )
            has_positions = reader.read_u32()
            has_normals = reader.read_u32()

            if not is_native:
                if has_positions:
                    for _ in range(num_vertices):
                        mt.vertices.append((reader.read_f32(), reader.read_f32(), reader.read_f32()))
                if has_normals:
                    for _ in range(num_vertices):
                        mt.normals.append((reader.read_f32(), reader.read_f32(), reader.read_f32()))

            geom.morph_targets.append(mt)

            # First morph target populates the main geometry fields
            if mt_i == 0:
                geom.bounding_sphere = mt.bounding_sphere
                geom.vertices = mt.vertices
                geom.normals = mt.normals

        # Child chunks (material list + extension)
        while reader.tell() < chunk_end:
            child = reader.read_chunk_header()
            child_end = reader.tell() + child.size

            if child.id == RW_MATERIAL_LIST:
                self._parse_material_list(reader, child_end, geom)
            elif child.id == RW_EXTENSION:
                self._parse_geometry_extension(reader, child_end, geom, num_vertices)
            else:
                reader.seek(child_end)

        return geom

    def _parse_geometry_extension(self, reader: RwBinaryReader, chunk_end: int,
                                  geom: DffGeometry, num_vertices: int):
        while reader.tell() < chunk_end:
            plugin = reader.read_chunk_header()
            plugin_end = reader.tell() + plugin.size

            if plugin.id == RW_BIN_MESH:
                geom.bin_mesh = self._parse_bin_mesh(reader, plugin_end)
            elif plugin.id == RW_SKIN:
                geom.skin = self._parse_skin(reader, plugin_end, num_vertices)
            elif plugin.id == RW_NIGHT_COLORS:
                geom.night_colors = self._parse_night_colors(reader, plugin_end, num_vertices)
            elif plugin.id == RW_2DFX:
                geom.effects_2dfx = self._parse_2dfx(reader, plugin_end)
            elif plugin.id == RW_MAT_EFFECTS:
                reader.read_u32()  # enabled flag at geometry level, skip
                if reader.tell() < plugin_end:
                    reader.seek(plugin_end)
            else:
                reader.seek(plugin_end)

    # --- Bin Mesh PLG ---

    def _parse_bin_mesh(self, reader: RwBinaryReader, chunk_end: int) -> BinMeshPLG:
        bm = BinMeshPLG()
        bm.flags = reader.read_u32()
        num_meshes = reader.read_u32()
        _total_indices = reader.read_u32()

        for _ in range(num_meshes):
            num_indices = reader.read_u32()
            mat_index = reader.read_u32()
            indices = [reader.read_u32() for _ in range(num_indices)]
            bm.splits.append(BinMeshSplit(material_index=mat_index, indices=indices))

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
        return bm

    # --- Skin PLG ---

    def _parse_skin(self, reader: RwBinaryReader, chunk_end: int, num_vertices: int) -> SkinPLG:
        skin = SkinPLG()
        skin.num_bones = reader.read_u8()
        num_used = reader.read_u8()
        max_weights = reader.read_u8()
        _pad = reader.read_u8()

        skin.used_bone_indices = [reader.read_u8() for _ in range(num_used)]

        # Bone indices per vertex
        for _ in range(num_vertices):
            skin.bone_indices.append((
                reader.read_u8(), reader.read_u8(),
                reader.read_u8(), reader.read_u8(),
            ))

        # Weights per vertex
        for _ in range(num_vertices):
            skin.weights.append((
                reader.read_f32(), reader.read_f32(),
                reader.read_f32(), reader.read_f32(),
            ))

        # Inverse bone matrices (4x4, 16 floats each)
        for _ in range(skin.num_bones):
            mat = struct.unpack("<16f", reader.read_bytes(64))
            skin.inverse_matrices.append(mat)

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
        return skin

    # --- Night Vertex Colors ---

    def _parse_night_colors(self, reader: RwBinaryReader, chunk_end: int,
                            num_vertices: int) -> list[tuple[int, int, int, int]] | None:
        has_colors = reader.read_u32()
        if not has_colors:
            return None
        colors = []
        for _ in range(num_vertices):
            colors.append((reader.read_u8(), reader.read_u8(), reader.read_u8(), reader.read_u8()))
        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
        return colors

    # --- 2dfx PLG ---

    def _parse_2dfx(self, reader: RwBinaryReader, chunk_end: int) -> list[Effect2dfxEntry]:
        entries: list[Effect2dfxEntry] = []
        num_entries = reader.read_u32()

        for _ in range(num_entries):
            if reader.tell() >= chunk_end:
                break
            px = reader.read_f32()
            py = reader.read_f32()
            pz = reader.read_f32()
            entry_type = reader.read_u32()
            data_size = reader.read_u32()
            data_start = reader.tell()

            entry = Effect2dfxEntry(position=(px, py, pz), entry_type=entry_type)

            if entry_type == 0 and data_size >= 76:  # Light
                light = Effect2dfxLight()
                light.color = (reader.read_u8(), reader.read_u8(), reader.read_u8(), reader.read_u8())
                light.corona_far_clip = reader.read_f32()
                light.pointlight_range = reader.read_f32()
                light.corona_size = reader.read_f32()
                light.shadow_size = reader.read_f32()
                light.corona_show_mode = reader.read_u8()
                light.corona_enable_reflection = reader.read_u8()
                light.corona_flare_type = reader.read_u8()
                light.shadow_color_multiplier = reader.read_u8()
                light.flags1 = reader.read_u8()
                light.corona_tex_name = reader.read_string(24)
                light.shadow_tex_name = reader.read_string(24)
                light.shadow_z_distance = reader.read_u8()
                light.flags2 = reader.read_u8()
                _pad = reader.read_u8()
                if data_size >= 80:
                    lx = reader.read_u8()
                    ly = reader.read_u8()
                    lz = reader.read_u8()
                    light.look_direction = (lx, ly, lz)
                    reader.skip(data_size - 79)  # remaining padding
                entry.data = light
            elif entry_type == 1:  # Particle
                entry.data = Effect2dfxParticle(effect_name=reader.read_string(min(data_size, 24)))
            else:
                entry.data = reader.read_bytes(data_size)

            reader.seek(data_start + data_size)
            entries.append(entry)

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
        return entries

    # --- Material List ---

    def _parse_material_list(self, reader: RwBinaryReader, chunk_end: int, geom: DffGeometry):
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT

        num_materials = reader.read_u32()
        for _ in range(num_materials):
            reader.read_i32()

        for _ in range(num_materials):
            if reader.tell() >= chunk_end:
                break
            mat_header = reader.read_chunk_header()
            if mat_header.id == RW_MATERIAL:
                mat_end = reader.tell() + mat_header.size
                geom.materials.append(self._parse_material(reader, mat_end))
            else:
                reader.skip(mat_header.size)

    def _parse_material(self, reader: RwBinaryReader, chunk_end: int) -> DffMaterial:
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT

        mat = DffMaterial()
        _flags = reader.read_u32()
        mat.color = (reader.read_u8(), reader.read_u8(), reader.read_u8(), reader.read_u8())
        _unused = reader.read_u32()
        is_textured = reader.read_u32()
        mat.ambient = reader.read_f32()
        mat.specular = reader.read_f32()
        mat.diffuse = reader.read_f32()

        # Read remaining child chunks
        while reader.tell() < chunk_end:
            child = reader.read_chunk_header()
            child_end = reader.tell() + child.size

            if child.id == RW_TEXTURE:
                self._parse_texture_ref(reader, child_end, mat)
            elif child.id == RW_EXTENSION:
                self._parse_material_extension(reader, child_end, mat)
            else:
                reader.seek(child_end)

        return mat

    def _parse_material_extension(self, reader: RwBinaryReader, chunk_end: int, mat: DffMaterial):
        while reader.tell() < chunk_end:
            plugin = reader.read_chunk_header()
            plugin_end = reader.tell() + plugin.size

            if plugin.id == RW_MAT_EFFECTS:
                mat.mat_fx_type = reader.read_u32()
                mat.mat_fx_data = self._parse_mat_fx_data(reader, plugin_end, mat.mat_fx_type)
            elif plugin.id == RW_SPECULAR_MAT:
                mat.specular_level = reader.read_f32()
                mat.specular_texture = reader.read_string(24)
            elif plugin.id == RW_REFLECTION_MAT:
                mat.reflection = {
                    "scale_x": reader.read_f32(),
                    "scale_y": reader.read_f32(),
                    "offset_x": reader.read_f32(),
                    "offset_y": reader.read_f32(),
                    "intensity": reader.read_f32(),
                }
                reader.read_u32()  # env_tex_ptr (always 0)
            else:
                reader.seek(plugin_end)

    def _parse_mat_fx_data(self, reader: RwBinaryReader, chunk_end: int, fx_type: int) -> dict:
        data: dict = {}

        def read_effect_slot():
            slot_type = reader.read_u32()
            slot: dict = {"type": slot_type}

            if slot_type == 1:  # Bump map
                slot["intensity"] = reader.read_f32()
                slot["has_bump_map"] = reader.read_u32()
                if slot["has_bump_map"]:
                    slot["bump_map"] = self._parse_inline_texture(reader)
                slot["has_height_map"] = reader.read_u32()
                if slot["has_height_map"]:
                    slot["height_map"] = self._parse_inline_texture(reader)
            elif slot_type == 2:  # Env map
                slot["reflection_coeff"] = reader.read_f32()
                slot["use_fb_alpha"] = reader.read_u32()
                slot["has_env_map"] = reader.read_u32()
                if slot["has_env_map"]:
                    slot["env_map"] = self._parse_inline_texture(reader)
            elif slot_type == 4:  # Dual texture
                slot["src_blend"] = reader.read_i32()
                slot["dst_blend"] = reader.read_i32()
                slot["has_texture"] = reader.read_u32()
                if slot["has_texture"]:
                    slot["texture"] = self._parse_inline_texture(reader)
            elif slot_type == 5:  # UV transform
                pass  # No additional data
            elif slot_type == 0:  # NULL
                pass

            return slot

        data["effect1"] = read_effect_slot()

        if reader.tell() < chunk_end:
            data["effect2"] = read_effect_slot()

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
        return data

    def _parse_inline_texture(self, reader: RwBinaryReader) -> dict:
        """Parse an inline RwTexture chunk embedded in material effects."""
        tex_h = reader.read_chunk_header()
        tex_end = reader.tell() + tex_h.size
        tex: dict = {"name": "", "mask": ""}

        while reader.tell() < tex_end:
            child = reader.read_chunk_header()
            child_end = reader.tell() + child.size

            if child.id == RW_STRUCT:
                tex["filter"] = reader.read_u32()
            elif child.id == RW_STRING:
                if not tex["name"]:
                    tex["name"] = reader.read_string(child.size)
                else:
                    tex["mask"] = reader.read_string(child.size)
            else:
                reader.seek(child_end)

        reader.seek(tex_end)
        return tex

    def _parse_texture_ref(self, reader: RwBinaryReader, chunk_end: int, mat: DffMaterial):
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT
        _filter_flags = reader.read_u16()
        _unknown = reader.read_u16()

        name_h = reader.read_chunk_header()
        if name_h.id == RW_STRING:
            mat.texture_name = reader.read_string(name_h.size)
        else:
            reader.skip(name_h.size)

        if reader.tell() < chunk_end:
            mask_h = reader.read_chunk_header()
            if mask_h.id == RW_STRING:
                mat.mask_name = reader.read_string(mask_h.size)
            else:
                reader.skip(mask_h.size)

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)

    # --- Atomic ---

    def _parse_atomic(self, reader: RwBinaryReader, chunk_end: int):
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT

        self.atomics.append(DffAtomic(
            frame_index=reader.read_u32(),
            geometry_index=reader.read_u32(),
            flags=reader.read_u32(),
        ))
        reader.read_u32()  # unused

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)

    # -----------------------------------------------------------------------
    # Writing
    # -----------------------------------------------------------------------

    def _write(self, w: RwBinaryWriter):
        """Write the full DFF clump."""
        ver = self.rw_version or 0x1803FFFF

        # We build the clump body in memory to know the size
        import io
        body_stream = io.BytesIO()
        body_w = RwBinaryWriter(body_stream)

        # Clump struct
        struct_data = struct.pack("<III", len(self.atomics), 0, 0)
        body_w.write_chunk_header(RW_STRUCT, len(struct_data), ver)
        body_w.write_bytes(struct_data)

        # Frame list
        self._write_frame_list(body_w, ver)

        # Geometry list
        self._write_geometry_list(body_w, ver)

        # Atomics
        for atomic in self.atomics:
            self._write_atomic(body_w, ver, atomic)

        # Clump extension
        ext_stream = io.BytesIO()
        ext_w = RwBinaryWriter(ext_stream)
        if self.collision and self.collision.raw:
            ext_w.write_chunk_header(RW_COLLISION, len(self.collision.raw), ver)
            ext_w.write_bytes(self.collision.raw)
        ext_data = ext_stream.getvalue()
        body_w.write_chunk_header(RW_EXTENSION, len(ext_data), ver)
        body_w.write_bytes(ext_data)

        # Write clump header + body
        body_data = body_stream.getvalue()
        w.write_chunk_header(RW_CLUMP, len(body_data), ver)
        w.write_bytes(body_data)

    def _write_frame_list(self, w: RwBinaryWriter, ver: int):
        import io

        num_frames = len(self.frames)
        # Struct: num_frames + 56 bytes per frame
        struct_data = struct.pack("<I", num_frames)
        for frame in self.frames:
            struct_data += struct.pack("<9f", *frame.rotation_matrix)
            struct_data += struct.pack("<3f", *frame.position)
            struct_data += struct.pack("<iI", frame.parent, 0)

        # Extensions per frame
        ext_chunks = b""
        for frame in self.frames:
            ext_body = b""
            # Frame name
            if frame.name:
                name_bytes = frame.name.encode("ascii", errors="replace")
                ext_body += struct.pack("<III", RW_FRAME_NAME, len(name_bytes), ver)
                ext_body += name_bytes
            # HAnim
            if frame.hanim:
                hanim_data = self._build_hanim_data(frame.hanim)
                ext_body += struct.pack("<III", RW_HANIM, len(hanim_data), ver)
                ext_body += hanim_data
            ext_chunks += struct.pack("<III", RW_EXTENSION, len(ext_body), ver)
            ext_chunks += ext_body

        body = struct.pack("<III", RW_STRUCT, len(struct_data), ver) + struct_data + ext_chunks
        w.write_chunk_header(RW_FRAME_LIST, len(body), ver)
        w.write_bytes(body)

    def _build_hanim_data(self, hanim: HAnimPLG) -> bytes:
        data = struct.pack("<III", hanim.version, hanim.node_id, len(hanim.bones))
        if hanim.bones:
            data += struct.pack("<II", hanim.hierarchy_flags, hanim.key_frame_size)
            for bone in hanim.bones:
                data += struct.pack("<III", bone.node_id, bone.node_index, bone.flags)
        return data

    def _write_geometry_list(self, w: RwBinaryWriter, ver: int):
        import io

        body_stream = io.BytesIO()
        body_w = RwBinaryWriter(body_stream)

        struct_data = struct.pack("<I", len(self.geometries))
        body_w.write_chunk_header(RW_STRUCT, len(struct_data), ver)
        body_w.write_bytes(struct_data)

        for geom in self.geometries:
            self._write_geometry(body_w, ver, geom)

        body_data = body_stream.getvalue()
        w.write_chunk_header(RW_GEOMETRY_LIST, len(body_data), ver)
        w.write_bytes(body_data)

    def _write_geometry(self, w: RwBinaryWriter, ver: int, geom: DffGeometry):
        import io

        geom_stream = io.BytesIO()
        gw = RwBinaryWriter(geom_stream)

        # Struct
        num_verts = len(geom.vertices)
        num_tris = len(geom.triangles)
        num_morph = max(1, len(geom.morph_targets))

        struct_data = struct.pack("<HBBiii",
            geom.flags, geom.num_uv_sets, 0,
            num_tris, num_verts, num_morph)

        # Pre-RW 3.4: surface properties stored in geometry struct
        rw_ver = _rw_version_raw(ver)
        if rw_ver < 0x34000:
            struct_data += struct.pack("<fff", 1.0, 1.0, 1.0)  # ambient, specular, diffuse

        is_native = bool(geom.flags & GEO_NATIVE)

        if not is_native:
            # Vertex colors
            if geom.flags & GEO_PRELIT:
                for r, g, b, a in geom.vertex_colors:
                    struct_data += struct.pack("<4B", r, g, b, a)
            # Texcoords
            for uv_set in geom.texcoord_sets:
                for u, v in uv_set:
                    struct_data += struct.pack("<ff", u, v)
            # Triangles (RW format: v2, v1, mat, v3)
            for v1, v2, v3, mat in geom.triangles:
                struct_data += struct.pack("<HHHH", v2, v1, mat, v3)

        # Morph targets
        targets = geom.morph_targets if geom.morph_targets else [
            MorphTarget(
                bounding_sphere=geom.bounding_sphere,
                vertices=geom.vertices,
                normals=geom.normals,
            )
        ]
        for mt in targets:
            has_pos = 1 if mt.vertices else 0
            has_norm = 1 if mt.normals else 0
            struct_data += struct.pack("<4fII",
                *mt.bounding_sphere, has_pos, has_norm)
            if not is_native:
                if mt.vertices:
                    for x, y, z in mt.vertices:
                        struct_data += struct.pack("<fff", x, y, z)
                if mt.normals:
                    for nx, ny, nz in mt.normals:
                        struct_data += struct.pack("<fff", nx, ny, nz)

        gw.write_chunk_header(RW_STRUCT, len(struct_data), ver)
        gw.write_bytes(struct_data)

        # Material list
        self._write_material_list(gw, ver, geom)

        # Extension
        ext_data = b""
        if geom.bin_mesh:
            ext_data += self._build_bin_mesh_data(geom.bin_mesh, ver)
        if geom.skin:
            ext_data += self._build_skin_data(geom.skin, num_verts, ver)
        if geom.night_colors is not None:
            ext_data += self._build_night_colors_data(geom.night_colors, ver)
        if geom.effects_2dfx:
            ext_data += self._build_2dfx_data(geom.effects_2dfx, ver)

        gw.write_chunk_header(RW_EXTENSION, len(ext_data), ver)
        gw.write_bytes(ext_data)

        geom_data = geom_stream.getvalue()
        w.write_chunk_header(RW_GEOMETRY, len(geom_data), ver)
        w.write_bytes(geom_data)

    def _build_bin_mesh_data(self, bm: BinMeshPLG, ver: int) -> bytes:
        total_indices = sum(len(s.indices) for s in bm.splits)
        body = struct.pack("<III", bm.flags, len(bm.splits), total_indices)
        for split in bm.splits:
            body += struct.pack("<II", len(split.indices), split.material_index)
            for idx in split.indices:
                body += struct.pack("<I", idx)
        return struct.pack("<III", RW_BIN_MESH, len(body), ver) + body

    def _build_skin_data(self, skin: SkinPLG, num_verts: int, ver: int) -> bytes:
        body = struct.pack("<4B", skin.num_bones, len(skin.used_bone_indices), 4, 0)
        for idx in skin.used_bone_indices:
            body += struct.pack("<B", idx)
        for bi in skin.bone_indices:
            body += struct.pack("<4B", *bi)
        for wt in skin.weights:
            body += struct.pack("<4f", *wt)
        for mat in skin.inverse_matrices:
            body += struct.pack("<16f", *mat)
        # Bone limit/groups (unused in GTA SA)
        body += struct.pack("<III", 0, 0, 0)
        return struct.pack("<III", RW_SKIN, len(body), ver) + body

    def _build_night_colors_data(self, colors: list, ver: int) -> bytes:
        body = struct.pack("<I", 1)  # has_colors
        for r, g, b, a in colors:
            body += struct.pack("<4B", r, g, b, a)
        return struct.pack("<III", RW_NIGHT_COLORS, len(body), ver) + body

    def _build_2dfx_data(self, entries: list[Effect2dfxEntry], ver: int) -> bytes:
        body = struct.pack("<I", len(entries))
        for entry in entries:
            if isinstance(entry.data, Effect2dfxLight):
                light = entry.data
                edata = struct.pack("<4B", *light.color)
                edata += struct.pack("<4f",
                    light.corona_far_clip, light.pointlight_range,
                    light.corona_size, light.shadow_size)
                edata += struct.pack("<5B", light.corona_show_mode,
                    light.corona_enable_reflection, light.corona_flare_type,
                    light.shadow_color_multiplier, light.flags1)
                edata += light.corona_tex_name.encode("ascii").ljust(24, b"\x00")[:24]
                edata += light.shadow_tex_name.encode("ascii").ljust(24, b"\x00")[:24]
                edata += struct.pack("<3B", light.shadow_z_distance, light.flags2, 0)
                if light.look_direction:
                    edata += struct.pack("<3B", *light.look_direction)
                    edata += b"\x00" * 2
            elif isinstance(entry.data, Effect2dfxParticle):
                edata = entry.data.effect_name.encode("ascii").ljust(24, b"\x00")[:24]
            elif isinstance(entry.data, bytes):
                edata = entry.data
            else:
                edata = b""

            body += struct.pack("<3fII", *entry.position, entry.entry_type, len(edata))
            body += edata

        return struct.pack("<III", RW_2DFX, len(body), ver) + body

    def _write_material_list(self, w: RwBinaryWriter, ver: int, geom: DffGeometry):
        import io
        body_stream = io.BytesIO()
        bw = RwBinaryWriter(body_stream)

        # Struct: count + indices (all -1)
        struct_data = struct.pack("<I", len(geom.materials))
        for _ in geom.materials:
            struct_data += struct.pack("<i", -1)
        bw.write_chunk_header(RW_STRUCT, len(struct_data), ver)
        bw.write_bytes(struct_data)

        for mat in geom.materials:
            self._write_material(bw, ver, mat)

        body_data = body_stream.getvalue()
        w.write_chunk_header(RW_MATERIAL_LIST, len(body_data), ver)
        w.write_bytes(body_data)

    def _write_material(self, w: RwBinaryWriter, ver: int, mat: DffMaterial):
        import io
        mat_stream = io.BytesIO()
        mw = RwBinaryWriter(mat_stream)

        is_textured = 1 if mat.texture_name else 0
        struct_data = struct.pack("<I4BIIfff",
            0, *mat.color, 0, is_textured,
            mat.ambient, mat.specular, mat.diffuse)
        mw.write_chunk_header(RW_STRUCT, len(struct_data), ver)
        mw.write_bytes(struct_data)

        if mat.texture_name:
            self._write_texture_ref(mw, ver, mat.texture_name, mat.mask_name)

        # Extension
        ext_data = b""
        if mat.mat_fx_type and mat.mat_fx_data:
            fx_body = struct.pack("<I", mat.mat_fx_type)
            # Simplified: write raw fx data type markers
            ext_data += struct.pack("<III", RW_MAT_EFFECTS, len(fx_body), ver) + fx_body
        if mat.specular_level > 0 or mat.specular_texture:
            spec_body = struct.pack("<f", mat.specular_level)
            spec_body += mat.specular_texture.encode("ascii").ljust(24, b"\x00")[:24]
            ext_data += struct.pack("<III", RW_SPECULAR_MAT, len(spec_body), ver) + spec_body
        if mat.reflection:
            ref = mat.reflection
            ref_body = struct.pack("<5fI",
                ref.get("scale_x", 0), ref.get("scale_y", 0),
                ref.get("offset_x", 0), ref.get("offset_y", 0),
                ref.get("intensity", 0), 0)
            ext_data += struct.pack("<III", RW_REFLECTION_MAT, len(ref_body), ver) + ref_body

        mw.write_chunk_header(RW_EXTENSION, len(ext_data), ver)
        mw.write_bytes(ext_data)

        mat_data = mat_stream.getvalue()
        w.write_chunk_header(RW_MATERIAL, len(mat_data), ver)
        w.write_bytes(mat_data)

    def _write_texture_ref(self, w: RwBinaryWriter, ver: int, name: str, mask: str):
        import io
        tex_stream = io.BytesIO()
        tw = RwBinaryWriter(tex_stream)

        tw.write_chunk_header(RW_STRUCT, 4, ver)
        tw.write_u32(0x1106)  # default filter flags

        # Pad strings to 4-byte aligned
        name_bytes = name.encode("ascii", errors="replace") + b"\x00"
        name_bytes = name_bytes.ljust((len(name_bytes) + 3) & ~3, b"\x00")
        tw.write_chunk_header(RW_STRING, len(name_bytes), ver)
        tw.write_bytes(name_bytes)

        mask_bytes = mask.encode("ascii", errors="replace") + b"\x00"
        mask_bytes = mask_bytes.ljust((len(mask_bytes) + 3) & ~3, b"\x00")
        tw.write_chunk_header(RW_STRING, len(mask_bytes), ver)
        tw.write_bytes(mask_bytes)

        tw.write_chunk_header(RW_EXTENSION, 0, ver)

        tex_data = tex_stream.getvalue()
        w.write_chunk_header(RW_TEXTURE, len(tex_data), ver)
        w.write_bytes(tex_data)

    def _write_atomic(self, w: RwBinaryWriter, ver: int, atomic: DffAtomic):
        import io
        body_stream = io.BytesIO()
        bw = RwBinaryWriter(body_stream)

        struct_data = struct.pack("<IIII",
            atomic.frame_index, atomic.geometry_index, atomic.flags, 0)
        bw.write_chunk_header(RW_STRUCT, len(struct_data), ver)
        bw.write_bytes(struct_data)
        bw.write_chunk_header(RW_EXTENSION, 0, ver)

        body_data = body_stream.getvalue()
        w.write_chunk_header(RW_ATOMIC, len(body_data), ver)
        w.write_bytes(body_data)
