"""GTA San Andreas `nodes*.dat` path file parser/writer."""

from __future__ import annotations

import io
import re
import struct
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from pathlib import Path


PATH_NODE_SIZE = 28
NAVI_NODE_SIZE = 14
LINK_SIZE = 4
SECTION4_FILLER_SIZE = 768
DEFAULT_SECTION4_FILLER = b"\xff\xff\x00\x00" * 192
# Original Steam GTA SA nodes*.dat files carry 384 bytes after section 7.
DEFAULT_TRAILING_DATA = b"\x00" * 384
POSITION_SCALE = 8.0
NAVI_DIRECTION_SCALE = 100.0
NAVI_NODE_ID_MASK = 0x03FF
PATH_NODE_BEHAVIOR_FLAG_MASK = (
    (1 << 6) | (1 << 7) | (1 << 8) | (1 << 10) |
    (1 << 12) | (1 << 13) | (1 << 20) | (1 << 21) | (1 << 23)
)


class PathNodeKind(IntEnum):
    VEHICLE = 0
    PED = 1


class PathTrafficLevel(IntEnum):
    FULL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3


class PathNodeFlag(IntFlag):
    ROAD_BLOCKS = 1 << 6
    BOATS = 1 << 7
    EMERGENCY_VEHICLES_ONLY = 1 << 8
    UNKNOWN_GROVE_HOUSE_ENTRANCE = 1 << 10
    IS_NOT_HIGHWAY = 1 << 12
    IS_HIGHWAY = 1 << 13
    ROAD_BLOCK = 1 << 20
    PARKING = 1 << 21
    ROAD_BLOCK_ALT = 1 << 23


class PathIntersectionFlag(IntFlag):
    ROAD_CROSS = 1 << 0
    PED_TRAFFIC_LIGHT = 1 << 1


@dataclass
class PathNode:
    """Vehicle or pedestrian path graph node from section 1."""

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    link_id: int = 0
    area_id: int = 0
    node_id: int = 0
    path_width: int = 0
    flood_fill: int = 0
    flags: int = 0
    heuristic_cost: int = 0x7FFE
    mem_address: int = 0
    zero: int = 0
    kind: PathNodeKind = PathNodeKind.VEHICLE

    @property
    def link_count(self) -> int:
        return self.flags & 0x0F

    @link_count.setter
    def link_count(self, value: int):
        if not 0 <= value <= 0x0F:
            raise ValueError("link_count must fit in 4 bits")
        self.flags = (self.flags & ~0x0F) | value

    @property
    def traffic_level(self) -> PathTrafficLevel:
        return PathTrafficLevel((self.flags >> 4) & 0x03)

    @traffic_level.setter
    def traffic_level(self, value: int | PathTrafficLevel):
        value = int(value)
        if not 0 <= value <= 0x03:
            raise ValueError("traffic_level must fit in 2 bits")
        self.flags = (self.flags & ~(0x03 << 4)) | (value << 4)

    @property
    def behavior_flags(self) -> PathNodeFlag:
        return PathNodeFlag(self.flags & PATH_NODE_BEHAVIOR_FLAG_MASK)

    @property
    def spawn_probability(self) -> int:
        return (self.flags >> 16) & 0x0F

    @spawn_probability.setter
    def spawn_probability(self, value: int):
        if not 0 <= value <= 0x0F:
            raise ValueError("spawn_probability must fit in 4 bits")
        self.flags = (self.flags & ~(0x0F << 16)) | (value << 16)

    @property
    def width(self) -> float:
        return self.path_width / POSITION_SCALE

    @width.setter
    def width(self, value: float):
        self.path_width = _clamp_u8(round(value * POSITION_SCALE))

    @property
    def is_vehicle(self) -> bool:
        return self.kind == PathNodeKind.VEHICLE

    @property
    def is_ped(self) -> bool:
        return self.kind == PathNodeKind.PED


@dataclass
class NaviNode:
    """Vehicle navigation node from section 2."""

    position: tuple[float, float] = (0.0, 0.0)
    area_id: int = 0
    node_id: int = 0
    direction: tuple[float, float] = (0.0, 0.0)
    flags: int = 0

    @property
    def path_width(self) -> int:
        return self.flags & 0xFF

    @path_width.setter
    def path_width(self, value: int):
        if not 0 <= value <= 0xFF:
            raise ValueError("path_width must fit in 8 bits")
        self.flags = (self.flags & ~0xFF) | value

    @property
    def width(self) -> float:
        return self.path_width / POSITION_SCALE

    @width.setter
    def width(self, value: float):
        self.path_width = _clamp_u8(round(value * POSITION_SCALE))

    @property
    def left_lanes(self) -> int:
        return (self.flags >> 8) & 0x07

    @left_lanes.setter
    def left_lanes(self, value: int):
        if not 0 <= value <= 0x07:
            raise ValueError("left_lanes must fit in 3 bits")
        self.flags = (self.flags & ~(0x07 << 8)) | (value << 8)

    @property
    def right_lanes(self) -> int:
        return (self.flags >> 11) & 0x07

    @right_lanes.setter
    def right_lanes(self, value: int):
        if not 0 <= value <= 0x07:
            raise ValueError("right_lanes must fit in 3 bits")
        self.flags = (self.flags & ~(0x07 << 11)) | (value << 11)

    @property
    def traffic_light_direction(self) -> bool:
        return bool(self.flags & (1 << 14))

    @traffic_light_direction.setter
    def traffic_light_direction(self, value: bool):
        self.flags = _set_bool_bit(self.flags, 14, value)

    @property
    def traffic_light_behavior(self) -> int:
        return (self.flags >> 16) & 0x03

    @traffic_light_behavior.setter
    def traffic_light_behavior(self, value: int):
        if not 0 <= value <= 0x03:
            raise ValueError("traffic_light_behavior must fit in 2 bits")
        self.flags = (self.flags & ~(0x03 << 16)) | (value << 16)

    @property
    def train_crossing(self) -> bool:
        return bool(self.flags & (1 << 18))

    @train_crossing.setter
    def train_crossing(self, value: bool):
        self.flags = _set_bool_bit(self.flags, 18, value)


@dataclass
class PathLink:
    area_id: int = 0
    node_id: int = 0


@dataclass
class NaviLink:
    area_id: int = 0
    node_id: int = 0

    @classmethod
    def from_packed(cls, value: int) -> NaviLink:
        return cls(area_id=(value >> 10) & 0x3F, node_id=value & NAVI_NODE_ID_MASK)

    def to_packed(self) -> int:
        if not 0 <= self.area_id <= 0x3F:
            raise ValueError("navi link area_id must fit in 6 bits")
        if not 0 <= self.node_id <= NAVI_NODE_ID_MASK:
            raise ValueError("navi link node_id must fit in 10 bits")
        return (self.area_id << 10) | self.node_id


@dataclass
class PathLinkRecord:
    """Combined view over sections 3, 5, 6, and 7 for one link index."""

    link: PathLink
    navi_link: NaviLink
    length: int
    intersection_flags: PathIntersectionFlag


@dataclass
class SaPathFile:
    """A single GTA SA `nodes*.dat` path file."""

    vehicle_nodes: list[PathNode] = field(default_factory=list)
    ped_nodes: list[PathNode] = field(default_factory=list)
    navi_nodes: list[NaviNode] = field(default_factory=list)
    links: list[PathLink] = field(default_factory=list)
    navi_links: list[NaviLink] = field(default_factory=list)
    link_lengths: list[int] = field(default_factory=list)
    intersection_flags: list[int | PathIntersectionFlag] = field(default_factory=list)
    section4_filler: bytes = DEFAULT_SECTION4_FILLER
    trailing_data: bytes = DEFAULT_TRAILING_DATA
    area_id: int | None = None

    @classmethod
    def from_file(cls, path: str) -> SaPathFile:
        with open(path, "rb") as f:
            data = f.read()
        return cls.from_bytes(data, area_id=cls.area_id_from_filename(path))

    @classmethod
    def from_bytes(cls, data: bytes, area_id: int | None = None) -> SaPathFile:
        stream = io.BytesIO(data)
        if len(data) < 20:
            raise ValueError("SA path file is too small to contain a header")

        total_nodes, vehicle_count, ped_count, navi_count, link_count = struct.unpack(
            "<5I", _read_exact(stream, 20)
        )
        if total_nodes != vehicle_count + ped_count:
            raise ValueError(
                "SA path header node count does not match vehicle + ped counts"
            )

        path_file = cls(area_id=area_id)
        for _ in range(vehicle_count):
            path_file.vehicle_nodes.append(_read_path_node(stream, PathNodeKind.VEHICLE))
        for _ in range(ped_count):
            path_file.ped_nodes.append(_read_path_node(stream, PathNodeKind.PED))
        for _ in range(navi_count):
            path_file.navi_nodes.append(_read_navi_node(stream))
        for _ in range(link_count):
            path_file.links.append(_read_link(stream))

        path_file.section4_filler = _read_exact(stream, SECTION4_FILLER_SIZE)

        for _ in range(link_count):
            packed = struct.unpack("<H", _read_exact(stream, 2))[0]
            path_file.navi_links.append(NaviLink.from_packed(packed))
        path_file.link_lengths = list(_read_exact(stream, link_count))
        path_file.intersection_flags = _read_intersection_flags(stream, link_count)
        path_file.trailing_data = stream.read()
        return path_file

    @staticmethod
    def area_id_from_filename(path: str) -> int | None:
        match = re.search(r"nodes(\d+)\.dat$", Path(path).name, re.IGNORECASE)
        return int(match.group(1)) if match else None

    @staticmethod
    def area_origin(area_id: int, tile_size: float = 750.0) -> tuple[float, float]:
        if not 0 <= area_id < 64:
            raise ValueError("GTA SA node area_id must be in range 0..63")
        return (-3000.0 + (area_id % 8) * tile_size,
                -3000.0 + (area_id // 8) * tile_size)

    @property
    def nodes(self) -> list[PathNode]:
        return [*self.vehicle_nodes, *self.ped_nodes]

    @property
    def node_count(self) -> int:
        return len(self.vehicle_nodes) + len(self.ped_nodes)

    @property
    def link_count(self) -> int:
        return len(self.links)

    def get_link_records(self) -> list[PathLinkRecord]:
        self.validate()
        return [
            PathLinkRecord(
                link=self.links[i],
                navi_link=self.navi_links[i],
                length=self.link_lengths[i],
                intersection_flags=PathIntersectionFlag(self.intersection_flags[i]),
            )
            for i in range(len(self.links))
        ]

    def links_for_node(self, node: PathNode) -> list[PathLinkRecord]:
        records = self.get_link_records()
        start = node.link_id
        end = start + node.link_count
        if start > len(records) or end > len(records):
            raise ValueError("node link range points outside the link table")
        return records[start:end]

    def validate(self):
        link_count = len(self.links)
        if len(self.navi_links) != link_count:
            raise ValueError("navi_links length must match links length")
        if len(self.link_lengths) != link_count:
            raise ValueError("link_lengths length must match links length")
        if len(self.intersection_flags) != link_count:
            raise ValueError("intersection_flags length must match links length")
        if len(self.section4_filler) != SECTION4_FILLER_SIZE:
            raise ValueError("section4_filler must be exactly 768 bytes")

    def to_file(self, path: str):
        with open(path, "wb") as f:
            f.write(self.to_bytes())

    def to_bytes(self) -> bytes:
        self.validate()
        out = bytearray()
        out += struct.pack(
            "<5I",
            self.node_count,
            len(self.vehicle_nodes),
            len(self.ped_nodes),
            len(self.navi_nodes),
            len(self.links),
        )

        for node in self.vehicle_nodes:
            out += _pack_path_node(node, PathNodeKind.VEHICLE)
        for node in self.ped_nodes:
            out += _pack_path_node(node, PathNodeKind.PED)
        for node in self.navi_nodes:
            out += _pack_navi_node(node)
        for link in self.links:
            out += struct.pack("<HH", link.area_id, link.node_id)

        out += self.section4_filler

        for navi_link in self.navi_links:
            out += struct.pack("<H", navi_link.to_packed())
        out += bytes(_clamp_u8(length) for length in self.link_lengths)
        out += bytes(_clamp_u8(int(flags)) for flags in self.intersection_flags)
        out += self.trailing_data
        return bytes(out)


SaPaths = SaPathFile


def _read_exact(stream: io.BytesIO, count: int) -> bytes:
    data = stream.read(count)
    if len(data) != count:
        raise EOFError(f"Expected {count} bytes, got {len(data)}")
    return data


def _read_intersection_flags(
    stream: io.BytesIO, link_count: int
) -> list[PathIntersectionFlag]:
    """Read section 7, tolerating path files that omit it entirely.

    Some valid modded path files keep the earlier SA sections intact but do not
    include a full intersection-flags table. In that case we treat the whole
    remainder as trailing data and default the flags to zero.
    """
    remaining = len(stream.getbuffer()) - stream.tell()
    if remaining < link_count:
        return [PathIntersectionFlag(0) for _ in range(link_count)]
    return [PathIntersectionFlag(value) for value in _read_exact(stream, link_count)]


def _read_path_node(stream: io.BytesIO, kind: PathNodeKind) -> PathNode:
    data = _read_exact(stream, PATH_NODE_SIZE)
    mem_address, zero, x, y, z, heuristic_cost, link_id, area_id, node_id, path_width, flood_fill, flags = struct.unpack(
        "<II3hhHHHBBI", data
    )
    return PathNode(
        position=(x / POSITION_SCALE, y / POSITION_SCALE, z / POSITION_SCALE),
        link_id=link_id,
        area_id=area_id,
        node_id=node_id,
        path_width=path_width,
        flood_fill=flood_fill,
        flags=flags,
        heuristic_cost=heuristic_cost,
        mem_address=mem_address,
        zero=zero,
        kind=kind,
    )


def _pack_path_node(node: PathNode, kind: PathNodeKind) -> bytes:
    x, y, z = (_to_i16(coord * POSITION_SCALE) for coord in node.position)
    return struct.pack(
        "<II3hhHHHBBI",
        node.mem_address,
        node.zero,
        x, y, z,
        _to_i16(node.heuristic_cost),
        _to_u16(node.link_id),
        _to_u16(node.area_id),
        _to_u16(node.node_id),
        _clamp_u8(node.path_width),
        _clamp_u8(node.flood_fill),
        int(node.flags),
    )


def _read_navi_node(stream: io.BytesIO) -> NaviNode:
    x, y, area_id, node_id, dx, dy, flags = struct.unpack(
        "<2hHH2bI", _read_exact(stream, NAVI_NODE_SIZE)
    )
    return NaviNode(
        position=(x / POSITION_SCALE, y / POSITION_SCALE),
        area_id=area_id,
        node_id=node_id,
        direction=(dx / NAVI_DIRECTION_SCALE, dy / NAVI_DIRECTION_SCALE),
        flags=flags,
    )


def _pack_navi_node(node: NaviNode) -> bytes:
    return struct.pack(
        "<2hHH2bI",
        _to_i16(node.position[0] * POSITION_SCALE),
        _to_i16(node.position[1] * POSITION_SCALE),
        _to_u16(node.area_id),
        _to_u16(node.node_id),
        _to_i8(node.direction[0] * NAVI_DIRECTION_SCALE),
        _to_i8(node.direction[1] * NAVI_DIRECTION_SCALE),
        int(node.flags),
    )


def _read_link(stream: io.BytesIO) -> PathLink:
    area_id, node_id = struct.unpack("<HH", _read_exact(stream, LINK_SIZE))
    return PathLink(area_id=area_id, node_id=node_id)


def _set_bool_bit(flags: int, bit: int, value: bool) -> int:
    if value:
        return flags | (1 << bit)
    return flags & ~(1 << bit)


def _clamp_u8(value: int) -> int:
    return max(0, min(255, int(value)))


def _to_i8(value: float) -> int:
    return max(-128, min(127, int(round(value))))


def _to_i16(value: float) -> int:
    return max(-32768, min(32767, int(round(value))))


def _to_u16(value: int) -> int:
    return max(0, min(65535, int(value)))
