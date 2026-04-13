from __future__ import annotations

from ..rwbinary import (
    RwBinaryReader,
    RW_STRUCT,
    RW_EXTENSION,
    RW_FRAME_LIST,
    RW_GEOMETRY_LIST,
    RW_CLUMP,
    RW_LIGHT,
    RW_ATOMIC,
    RW_COLLISION,
)
from .models import CollisionData
from .read_geometry import DffGeometryReaderMixin
from .read_material import DffMaterialReaderMixin
from .read_scene import DffSceneReaderMixin


class DffReaderMixin(DffSceneReaderMixin, DffGeometryReaderMixin, DffMaterialReaderMixin):
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
            _num_lights = reader.read_u32()
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
            elif child.id == RW_LIGHT:
                self.lights.append(self._parse_light(reader, child_end))
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
