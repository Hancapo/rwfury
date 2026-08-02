"""GTA IFP animation package parser/writer."""

from __future__ import annotations

import io
import json
import struct
from dataclasses import dataclass, field
from enum import Enum, IntEnum


IFP_ANP3_MAGIC = b"ANP3"
IFP_ANPK_MAGIC = b"ANPK"
IFP_V2_NAME_SIZE = 24
IFP_V2_QUAT_SCALE = 4096.0
IFP_V2_TRANS_SCALE = 1024.0
IFP_V2_TIME_SCALE = 60.0


class IfpVersion(IntEnum):
    ANPK = 1
    ANP3 = 3


class IfpFrameType(IntEnum):
    CHILD_FLOAT = 1
    ROOT_FLOAT = 2
    CHILD = 3
    ROOT = 4


class IfpKeyframeType(str, Enum):
    NONE = "K000"
    ROTATION = "KR00"
    ROTATION_TRANSLATION = "KRT0"
    ROTATION_TRANSLATION_SCALE = "KRTS"


@dataclass
class IfpFrame:
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    time: float = 0.0
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] | None = None
    raw_time: int | None = None


@dataclass
class IfpObject:
    name: str = ""
    frame_type: int | IfpFrameType = IfpFrameType.CHILD
    bone_id: int = 0
    frames: list[IfpFrame] = field(default_factory=list)
    keyframe_type: IfpKeyframeType | None = None
    anpk_unknown: tuple[int, int] = (0, 0)
    sibling_indices: tuple[int, int] | None = None

    @property
    def has_translation(self) -> bool:
        if self.keyframe_type is not None:
            return self.keyframe_type in (
                IfpKeyframeType.ROTATION_TRANSLATION,
                IfpKeyframeType.ROTATION_TRANSLATION_SCALE,
            )
        return int(self.frame_type) in (
            int(IfpFrameType.ROOT_FLOAT),
            int(IfpFrameType.ROOT),
        )

    @property
    def has_scale(self) -> bool:
        return self.keyframe_type == IfpKeyframeType.ROTATION_TRANSLATION_SCALE


@dataclass
class IfpAnimation:
    name: str = ""
    objects: list[IfpObject] = field(default_factory=list)
    unknown: int = 1

    @property
    def object_count(self) -> int:
        return len(self.objects)

    @property
    def frame_data_size(self) -> int:
        total = 0
        for obj in self.objects:
            if obj.keyframe_type not in (None, IfpKeyframeType.NONE):
                frame_size = _anpk_frame_size(obj.keyframe_type)
            else:
                frame_size = _frame_size_for_type(int(obj.frame_type))
            total += len(obj.frames) * frame_size
        return total

    def get_object(self, name: str) -> IfpObject | None:
        name_lower = name.lower()
        for obj in self.objects:
            if obj.name.lower() == name_lower:
                return obj
        return None


class Ifp:
    """IFP animation package.

    Supports GTA San Andreas `ANP3` and chunked `ANPK` packages.
    """

    def __init__(self):
        self.version: int | IfpVersion = IfpVersion.ANP3
        self.internal_name: str = ""
        self.animations: list[IfpAnimation] = []

    @classmethod
    def from_file(cls, path: str) -> Ifp:
        with open(path, "rb") as f:
            return cls.from_bytes(f.read())

    @classmethod
    def from_bytes(cls, data: bytes) -> Ifp:
        ifp = cls()
        if data.startswith(IFP_ANP3_MAGIC):
            ifp._parse_v2(io.BytesIO(_slice_ifp_data(data)))
            return ifp
        if data.startswith(IFP_ANPK_MAGIC):
            ifp._parse_anpk(io.BytesIO(_slice_ifp_data(data)))
            return ifp
        raise ValueError("Not an IFP: expected ANP3 or ANPK header")

    def to_file(self, path: str):
        with open(path, "wb") as f:
            f.write(self.to_bytes())

    def to_bytes(self) -> bytes:
        if int(self.version) == int(IfpVersion.ANPK):
            return self._to_anpk_bytes()
        if int(self.version) != int(IfpVersion.ANP3):
            raise NotImplementedError(f"Writing IFP version {self.version!r} is not supported")

        body = bytearray()
        body += _pack_fixed_string(self.internal_name, IFP_V2_NAME_SIZE)
        body += struct.pack("<I", len(self.animations))

        for animation in self.animations:
            body += _pack_fixed_string(animation.name, IFP_V2_NAME_SIZE)
            body += struct.pack(
                "<III",
                len(animation.objects),
                animation.frame_data_size,
                animation.unknown,
            )
            for obj in animation.objects:
                body += _pack_fixed_string(obj.name, IFP_V2_NAME_SIZE)
                body += struct.pack(
                    "<III",
                    int(obj.frame_type),
                    len(obj.frames),
                    _to_u32(obj.bone_id),
                )
                for frame in obj.frames:
                    body += _pack_v2_frame(frame, int(obj.frame_type))

        file_size = 8 + len(body)
        return IFP_ANP3_MAGIC + struct.pack("<I", file_size - 8) + bytes(body)

    def get_animation(self, name: str) -> IfpAnimation | None:
        name_lower = name.lower()
        for animation in self.animations:
            if animation.name.lower() == name_lower:
                return animation
        return None

    def get_animation_names(self) -> list[str]:
        return [animation.name for animation in self.animations]

    def iter_objects(self):
        for animation in self.animations:
            for obj in animation.objects:
                yield animation, obj

    def get_objects(self, animation_name: str | None = None) -> list[dict]:
        if animation_name is None:
            return [
                {
                    "animation": animation,
                    "animation_name": animation.name,
                    "object": obj,
                    "object_name": obj.name,
                    "frame_type": int(obj.frame_type),
                    "frame_count": len(obj.frames),
                    "bone_id": obj.bone_id,
                    "has_translation": obj.has_translation,
                    "has_scale": obj.has_scale,
                    "keyframe_type": obj.keyframe_type.value if obj.keyframe_type else None,
                }
                for animation, obj in self.iter_objects()
            ]

        animation = self.get_animation(animation_name)
        if animation is None:
            return []

        return [
            {
                "animation": animation,
                "animation_name": animation.name,
                "object": obj,
                "object_name": obj.name,
                "frame_type": int(obj.frame_type),
                "frame_count": len(obj.frames),
                "bone_id": obj.bone_id,
                "has_translation": obj.has_translation,
                "has_scale": obj.has_scale,
                "keyframe_type": obj.keyframe_type.value if obj.keyframe_type else None,
            }
            for obj in animation.objects
        ]

    def get_object(self, animation_name: str, object_name: str) -> IfpObject | None:
        animation = self.get_animation(animation_name)
        if animation is None:
            return None
        return animation.get_object(object_name)

    def to_animation_data(self) -> dict:
        animations = []
        object_count = 0
        frame_count = 0

        for animation in self.animations:
            objects = []
            for obj in animation.objects:
                object_count += 1
                frame_count += len(obj.frames)
                objects.append({
                    "name": obj.name,
                    "frame_type": int(obj.frame_type),
                    "bone_id": obj.bone_id,
                    "frame_count": len(obj.frames),
                    "has_translation": obj.has_translation,
                    "has_scale": obj.has_scale,
                    "keyframe_type": obj.keyframe_type.value if obj.keyframe_type else None,
                    "sibling_indices": list(obj.sibling_indices) if obj.sibling_indices else None,
                    "frames": [
                        {
                            "time": frame.time,
                            "raw_time": frame.raw_time,
                            "rotation": list(frame.rotation),
                            "translation": list(frame.translation),
                            "scale": list(frame.scale) if frame.scale is not None else None,
                        }
                        for frame in obj.frames
                    ],
                })

            animations.append({
                "name": animation.name,
                "object_count": len(animation.objects),
                "frame_data_size": animation.frame_data_size,
                "unknown": animation.unknown,
                "objects": objects,
            })

        return {
            "version": int(self.version),
            "internal_name": self.internal_name,
            "animation_count": len(self.animations),
            "object_count": object_count,
            "frame_count": frame_count,
            "animations": animations,
        }

    def to_animation_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_animation_data(), indent=indent)

    def export_animations(self, path: str) -> dict:
        data = self.to_animation_data()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return data

    def _parse_anpk(self, stream: io.BytesIO):
        self.version = IfpVersion.ANPK
        magic = _read_exact(stream, 4)
        assert magic == IFP_ANPK_MAGIC
        _read_exact(stream, 4)

        info = _read_section(stream, b"INFO")
        if len(info) < 5:
            raise ValueError("ANPK package INFO section is too small")
        animation_count = struct.unpack_from("<I", info)[0]
        self.internal_name = _decode_string(info[4:])

        for _ in range(animation_count):
            animation = IfpAnimation(name=_decode_string(_read_section(stream, b"NAME")))
            dgan = io.BytesIO(_read_section(stream, b"DGAN"))
            animation_info = _read_section(dgan, b"INFO")
            if len(animation_info) < 4:
                raise ValueError(f"ANPK animation INFO is too small for {animation.name!r}")
            object_count = struct.unpack_from("<I", animation_info)[0]

            for _ in range(object_count):
                cpan = io.BytesIO(_read_section(dgan, b"CPAN"))
                object_info = _read_section(cpan, b"ANIM")
                if len(object_info) not in (40, 44, 48):
                    raise ValueError(
                        f"Unsupported ANPK ANIM section size {len(object_info)} "
                        f"in {animation.name!r}"
                    )

                frame_count = struct.unpack_from("<I", object_info, 28)[0]
                unknown = struct.unpack_from("<ii", object_info, 32)
                obj = IfpObject(
                    name=_decode_string(object_info[:28]),
                    frame_type=IfpFrameType.CHILD_FLOAT,
                    bone_id=-1,
                    anpk_unknown=unknown,
                )
                if len(object_info) == 44:
                    obj.bone_id = struct.unpack_from("<i", object_info, 40)[0]
                elif len(object_info) == 48:
                    obj.sibling_indices = struct.unpack_from("<ii", object_info, 40)

                if frame_count:
                    key_magic, key_data = _read_any_section(cpan)
                    try:
                        obj.keyframe_type = IfpKeyframeType(key_magic.decode("ascii"))
                    except (UnicodeDecodeError, ValueError) as exc:
                        raise ValueError(
                            f"Unsupported ANPK keyframe section {key_magic!r} "
                            f"in {animation.name!r}/{obj.name!r}"
                        ) from exc
                    obj.frame_type = (
                        IfpFrameType.ROOT_FLOAT
                        if obj.has_translation
                        else IfpFrameType.CHILD_FLOAT
                    )
                    obj.frames = _read_anpk_frames(
                        key_data, frame_count, obj.keyframe_type,
                        f"{animation.name}/{obj.name}",
                    )
                else:
                    obj.keyframe_type = IfpKeyframeType.NONE

                if cpan.tell() != len(cpan.getbuffer()):
                    raise ValueError(
                        f"Unexpected trailing data in ANPK CPAN for "
                        f"{animation.name!r}/{obj.name!r}"
                    )
                animation.objects.append(obj)

            if dgan.tell() != len(dgan.getbuffer()):
                raise ValueError(f"Unexpected trailing data in ANPK DGAN for {animation.name!r}")
            self.animations.append(animation)

    def _to_anpk_bytes(self) -> bytes:
        content = bytearray()
        package_info = struct.pack("<I", len(self.animations)) + _pack_c_string(
            self.internal_name
        )
        content += _pack_section(b"INFO", package_info)

        for animation in self.animations:
            content += _pack_section(b"NAME", _pack_c_string(animation.name))
            dgan = bytearray()
            dgan += _pack_section(b"INFO", struct.pack("<II", len(animation.objects), 0))

            for obj in animation.objects:
                key_type = obj.keyframe_type or _anpk_type_for_object(obj)
                unknown = obj.anpk_unknown
                if unknown == (0, 0) and obj.frames:
                    unknown = (0, len(obj.frames) - 1)

                object_info = bytearray(_pack_fixed_string(obj.name, 28))
                object_info += struct.pack("<Iii", len(obj.frames), *unknown)
                if obj.sibling_indices is None:
                    object_info += struct.pack("<i", obj.bone_id)
                else:
                    object_info += struct.pack("<ii", *obj.sibling_indices)

                cpan = bytearray(_pack_section(b"ANIM", bytes(object_info)))
                if obj.frames:
                    cpan += _pack_section(
                        key_type.value.encode("ascii"),
                        _pack_anpk_frames(obj.frames, key_type),
                    )
                dgan += _pack_section(b"CPAN", bytes(cpan))

            content += _pack_section(b"DGAN", bytes(dgan))

        return IFP_ANPK_MAGIC + struct.pack("<I", len(content)) + bytes(content)

    def _parse_v2(self, stream: io.BytesIO):
        self.version = IfpVersion.ANP3
        magic = _read_exact(stream, 4)
        assert magic == IFP_ANP3_MAGIC
        _file_end = struct.unpack("<I", _read_exact(stream, 4))[0]
        self.internal_name = _read_fixed_string(stream, IFP_V2_NAME_SIZE)
        animation_count = struct.unpack("<I", _read_exact(stream, 4))[0]

        for _ in range(animation_count):
            animation = IfpAnimation(
                name=_read_fixed_string(stream, IFP_V2_NAME_SIZE),
            )
            object_count, _frame_data_size, animation.unknown = struct.unpack(
                "<III", _read_exact(stream, 12)
            )

            for _ in range(object_count):
                obj = IfpObject(
                    name=_read_fixed_string(stream, IFP_V2_NAME_SIZE),
                )
                frame_type, frame_count, bone_id = struct.unpack(
                    "<III", _read_exact(stream, 12)
                )
                obj.frame_type = frame_type
                obj.bone_id = _to_i32(bone_id)

                for _ in range(frame_count):
                    obj.frames.append(_read_v2_frame(stream, frame_type))

                animation.objects.append(obj)

            self.animations.append(animation)


def _align4(value: int) -> int:
    return (value + 3) & ~3


def _read_any_section(stream: io.BytesIO) -> tuple[bytes, bytes]:
    magic = _read_exact(stream, 4)
    size = struct.unpack("<I", _read_exact(stream, 4))[0]
    data = _read_exact(stream, size)
    padding = _align4(size) - size
    if padding:
        _read_exact(stream, padding)
    return magic, data


def _read_section(stream: io.BytesIO, expected: bytes) -> bytes:
    magic, data = _read_any_section(stream)
    if magic != expected:
        raise ValueError(f"Expected ANPK section {expected!r}, got {magic!r}")
    return data


def _pack_section(magic: bytes, data: bytes) -> bytes:
    if len(magic) != 4:
        raise ValueError(f"IFP section identifiers must be four bytes: {magic!r}")
    padding = _align4(len(data)) - len(data)
    return magic + struct.pack("<I", len(data)) + data + b"\x00" * padding


def _decode_string(data: bytes) -> str:
    return data.split(b"\x00", 1)[0].decode("ascii", errors="replace")


def _pack_c_string(value: str) -> bytes:
    return value.encode("ascii", errors="replace") + b"\x00"


def _anpk_frame_size(key_type: IfpKeyframeType) -> int:
    if key_type == IfpKeyframeType.ROTATION:
        return 20
    if key_type == IfpKeyframeType.ROTATION_TRANSLATION:
        return 32
    if key_type == IfpKeyframeType.ROTATION_TRANSLATION_SCALE:
        return 44
    raise ValueError(f"ANPK keyframe type {key_type.value!r} cannot contain frames")


def _anpk_type_for_object(obj: IfpObject) -> IfpKeyframeType:
    if obj.has_scale or any(frame.scale is not None for frame in obj.frames):
        return IfpKeyframeType.ROTATION_TRANSLATION_SCALE
    if obj.has_translation:
        return IfpKeyframeType.ROTATION_TRANSLATION
    return IfpKeyframeType.ROTATION


def _read_anpk_frames(
    data: bytes,
    count: int,
    key_type: IfpKeyframeType,
    context: str,
) -> list[IfpFrame]:
    frame_size = _anpk_frame_size(key_type)
    expected = count * frame_size
    if len(data) != expected:
        raise ValueError(
            f"ANPK keyframe data size mismatch in {context!r}: "
            f"expected {expected}, got {len(data)}"
        )

    frames = []
    offset = 0
    for _ in range(count):
        qx, qy, qz, qw = struct.unpack_from("<4f", data, offset)
        offset += 16
        translation = (0.0, 0.0, 0.0)
        scale = None
        if key_type in (
            IfpKeyframeType.ROTATION_TRANSLATION,
            IfpKeyframeType.ROTATION_TRANSLATION_SCALE,
        ):
            translation = struct.unpack_from("<3f", data, offset)
            offset += 12
        if key_type == IfpKeyframeType.ROTATION_TRANSLATION_SCALE:
            scale = struct.unpack_from("<3f", data, offset)
            offset += 12
        time = struct.unpack_from("<f", data, offset)[0]
        offset += 4
        frames.append(IfpFrame(
            rotation=(-qx, -qy, -qz, qw),
            translation=translation,
            scale=scale,
            time=time,
        ))
    return frames


def _pack_anpk_frames(
    frames: list[IfpFrame],
    key_type: IfpKeyframeType,
) -> bytes:
    if key_type == IfpKeyframeType.NONE:
        raise ValueError("ANPK K000 tracks cannot contain frames")
    data = bytearray()
    for frame in frames:
        qx, qy, qz, qw = frame.rotation
        data += struct.pack("<4f", -qx, -qy, -qz, qw)
        if key_type in (
            IfpKeyframeType.ROTATION_TRANSLATION,
            IfpKeyframeType.ROTATION_TRANSLATION_SCALE,
        ):
            data += struct.pack("<3f", *frame.translation)
        if key_type == IfpKeyframeType.ROTATION_TRANSLATION_SCALE:
            data += struct.pack("<3f", *(frame.scale or (1.0, 1.0, 1.0)))
        data += struct.pack("<f", frame.time)
    return bytes(data)


def _read_exact(stream: io.BytesIO, count: int) -> bytes:
    data = stream.read(count)
    if len(data) != count:
        raise EOFError(f"Expected {count} bytes, got {len(data)}")
    return data


def _read_fixed_string(stream: io.BytesIO, size: int) -> str:
    raw = _read_exact(stream, size)
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")


def _pack_fixed_string(value: str, size: int) -> bytes:
    return value.encode("ascii", errors="replace").ljust(size, b"\x00")[:size]


def _frame_size_for_type(frame_type: int) -> int:
    if frame_type == int(IfpFrameType.CHILD_FLOAT):
        return 20
    if frame_type == int(IfpFrameType.ROOT_FLOAT):
        return 32
    if frame_type == int(IfpFrameType.ROOT):
        return 16
    if frame_type == int(IfpFrameType.CHILD):
        return 10
    raise ValueError(f"Unsupported ANP3 frame type: {frame_type}")


def _read_v2_frame(stream: io.BytesIO, frame_type: int) -> IfpFrame:
    if frame_type == int(IfpFrameType.CHILD_FLOAT):
        qx, qy, qz, qw, time = struct.unpack("<5f", _read_exact(stream, 20))
        return IfpFrame(rotation=(qx, qy, qz, qw), time=time)

    if frame_type == int(IfpFrameType.ROOT_FLOAT):
        qx, qy, qz, qw, time, tx, ty, tz = struct.unpack(
            "<8f", _read_exact(stream, 32)
        )
        return IfpFrame(
            rotation=(qx, qy, qz, qw),
            time=time,
            translation=(tx, ty, tz),
        )

    if frame_type == int(IfpFrameType.ROOT):
        qx, qy, qz, qw, raw_time, tx, ty, tz = struct.unpack(
            "<8h", _read_exact(stream, 16)
        )
        return IfpFrame(
            rotation=(
                qx / IFP_V2_QUAT_SCALE,
                qy / IFP_V2_QUAT_SCALE,
                qz / IFP_V2_QUAT_SCALE,
                qw / IFP_V2_QUAT_SCALE,
            ),
            time=raw_time / IFP_V2_TIME_SCALE,
            translation=(
                tx / IFP_V2_TRANS_SCALE,
                ty / IFP_V2_TRANS_SCALE,
                tz / IFP_V2_TRANS_SCALE,
            ),
            raw_time=raw_time,
        )

    if frame_type == int(IfpFrameType.CHILD):
        qx, qy, qz, qw, raw_time = struct.unpack("<5h", _read_exact(stream, 10))
        return IfpFrame(
            rotation=(
                qx / IFP_V2_QUAT_SCALE,
                qy / IFP_V2_QUAT_SCALE,
                qz / IFP_V2_QUAT_SCALE,
                qw / IFP_V2_QUAT_SCALE,
            ),
            time=raw_time / IFP_V2_TIME_SCALE,
            raw_time=raw_time,
        )

    raise ValueError(f"Unsupported ANP3 frame type: {frame_type}")


def _pack_v2_frame(frame: IfpFrame, frame_type: int) -> bytes:
    if frame_type == int(IfpFrameType.CHILD_FLOAT):
        return struct.pack(
            "<5f",
            float(frame.rotation[0]),
            float(frame.rotation[1]),
            float(frame.rotation[2]),
            float(frame.rotation[3]),
            float(frame.time),
        )

    if frame_type == int(IfpFrameType.ROOT_FLOAT):
        return struct.pack(
            "<8f",
            float(frame.rotation[0]),
            float(frame.rotation[1]),
            float(frame.rotation[2]),
            float(frame.rotation[3]),
            float(frame.time),
            float(frame.translation[0]),
            float(frame.translation[1]),
            float(frame.translation[2]),
        )

    qx, qy, qz, qw = (
        _to_i16(component * IFP_V2_QUAT_SCALE) for component in frame.rotation
    )
    raw_time = _to_i16(round(frame.time * IFP_V2_TIME_SCALE))

    if frame_type == int(IfpFrameType.ROOT):
        tx, ty, tz = (_to_i16(component * IFP_V2_TRANS_SCALE) for component in frame.translation)
        return struct.pack("<8h", qx, qy, qz, qw, raw_time, tx, ty, tz)

    if frame_type == int(IfpFrameType.CHILD):
        return struct.pack("<5h", qx, qy, qz, qw, raw_time)

    raise ValueError(f"Unsupported ANP3 frame type: {frame_type}")


def _to_i16(value: float) -> int:
    return max(-32768, min(32767, int(round(value))))


def _to_i32(value: int) -> int:
    value &= 0xFFFFFFFF
    if value >= 0x80000000:
        return value - 0x100000000
    return value


def _to_u32(value: int) -> int:
    return value & 0xFFFFFFFF


def _slice_ifp_data(data: bytes) -> bytes:
    if len(data) < 8:
        raise EOFError("Expected at least 8 bytes for IFP header")

    if data.startswith((IFP_ANP3_MAGIC, IFP_ANPK_MAGIC)):
        size = struct.unpack("<I", data[4:8])[0] + 8
        if size > len(data):
            raise EOFError(f"Expected {size} bytes, got {len(data)}")
        return data[:size]

    return data
