"""GTA IFP animation package parser/writer."""

from __future__ import annotations

import io
import json
import math
import struct
from bisect import bisect_right
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Literal


IFP_ANP3_MAGIC = b"ANP3"
IFP_ANPK_MAGIC = b"ANPK"
IFP_V2_NAME_SIZE = 24
IFP_V2_QUAT_SCALE = 4096.0
IFP_V2_TRANS_SCALE = 1024.0
IFP_V2_TIME_SCALE = 60.0
IfpOutOfRange = Literal["clamp", "error"]


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

    def interpolate(self, other: IfpFrame, factor: float) -> IfpFrame:
        if not math.isfinite(factor) or not 0.0 <= factor <= 1.0:
            raise ValueError("Interpolation factor must be finite and between 0 and 1")
        scale = None
        if self.scale is not None or other.scale is not None:
            scale = _lerp_vector(
                self.scale or (1.0, 1.0, 1.0),
                other.scale or (1.0, 1.0, 1.0),
                factor,
            )
        return IfpFrame(
            rotation=_slerp(self.rotation, other.rotation, factor),
            translation=_lerp_vector(self.translation, other.translation, factor),
            scale=scale,
            time=self.time + (other.time - self.time) * factor,
            raw_time=None,
        )

    def to_matrix(self) -> tuple[float, ...]:
        x, y, z, w = _normalize_quaternion(self.rotation)
        sx, sy, sz = self.scale or (1.0, 1.0, 1.0)
        tx, ty, tz = self.translation
        return (
            (1.0 - 2.0 * (y * y + z * z)) * sx,
            (2.0 * (x * y - z * w)) * sy,
            (2.0 * (x * z + y * w)) * sz,
            tx,
            (2.0 * (x * y + z * w)) * sx,
            (1.0 - 2.0 * (x * x + z * z)) * sy,
            (2.0 * (y * z - x * w)) * sz,
            ty,
            (2.0 * (x * z - y * w)) * sx,
            (2.0 * (y * z + x * w)) * sy,
            (1.0 - 2.0 * (x * x + y * y)) * sz,
            tz,
            0.0, 0.0, 0.0, 1.0,
        )


@dataclass(frozen=True)
class IfpValidationIssue:
    code: str
    message: str
    animation_name: str | None = None
    object_name: str | None = None


class IfpValidationError(ValueError):
    def __init__(self, issues: list[IfpValidationIssue]):
        self.issues = issues
        super().__init__("; ".join(issue.message for issue in issues))


@dataclass
class IfpObject:
    name: str = ""
    frame_type: int | IfpFrameType = IfpFrameType.CHILD
    bone_id: int = 0
    frames: list[IfpFrame] = field(default_factory=list)
    keyframe_type: IfpKeyframeType | None = None
    anpk_name_unknown: int = 0
    anpk_unknown: tuple[int, int] = (0, 0)
    anpk_extra: int | None = None
    _raw_name_data: bytes | None = field(default=None, repr=False, compare=False)
    _raw_key_data: bytes | None = field(default=None, repr=False, compare=False)
    _anpk_anim_size: int | None = field(default=None, repr=False, compare=False)
    _raw_cpan_padding: bytes | None = field(default=None, repr=False, compare=False)
    _raw_anim_padding: bytes | None = field(default=None, repr=False, compare=False)
    _raw_key_padding: bytes | None = field(default=None, repr=False, compare=False)

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

    @property
    def duration(self) -> float:
        if not self.frames:
            return 0.0
        times = [frame.time for frame in self.frames]
        if not all(map(math.isfinite, times)):
            raise ValueError(f"Object {self.name!r} contains a non-finite frame time")
        return max(0.0, max(times))

    def sample(
        self,
        time: float,
        *,
        out_of_range: IfpOutOfRange = "clamp",
    ) -> IfpFrame | None:
        if not self.frames:
            return None
        sample_time = _resolve_sample_time(time, self.duration, out_of_range)
        frames = _ordered_unique_frames(self.frames)
        times = [frame.time for frame in frames]
        index = bisect_right(times, sample_time)
        if index == 0:
            return _held_frame(frames[0], sample_time)
        if index == len(frames):
            return _held_frame(frames[-1], sample_time)
        left = frames[index - 1]
        right = frames[index]
        if sample_time == left.time:
            return _held_frame(left, sample_time)
        factor = (sample_time - left.time) / (right.time - left.time)
        sampled = left.interpolate(right, factor)
        sampled.time = sample_time
        return sampled

    def sample_matrix(
        self,
        time: float,
        *,
        out_of_range: IfpOutOfRange = "clamp",
    ) -> tuple[float, ...] | None:
        frame = self.sample(time, out_of_range=out_of_range)
        return frame.to_matrix() if frame is not None else None


@dataclass
class IfpAnimation:
    name: str = ""
    objects: list[IfpObject] = field(default_factory=list)
    unknown: int = 1
    declared_frame_data_size: int | None = None
    _raw_name_data: bytes | None = field(default=None, repr=False, compare=False)
    _raw_name_padding: bytes | None = field(default=None, repr=False, compare=False)
    _raw_dgan_padding: bytes | None = field(default=None, repr=False, compare=False)
    _raw_info_data: bytes | None = field(default=None, repr=False, compare=False)
    _raw_info_padding: bytes | None = field(default=None, repr=False, compare=False)

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

    @property
    def duration(self) -> float:
        return max((obj.duration for obj in self.objects), default=0.0)

    def sample(
        self,
        time: float,
        *,
        out_of_range: IfpOutOfRange = "clamp",
    ) -> list[tuple[IfpObject, IfpFrame | None]]:
        sample_time = _resolve_sample_time(time, self.duration, out_of_range)
        return [
            (obj, obj.sample(sample_time, out_of_range="clamp"))
            for obj in self.objects
        ]


class Ifp:
    """IFP animation package.

    Supports GTA San Andreas `ANP3` and chunked `ANPK` packages.
    """

    def __init__(self):
        self.version: int | IfpVersion = IfpVersion.ANP3
        self.internal_name: str = ""
        self.animations: list[IfpAnimation] = []
        self._raw_internal_name_data: bytes | None = None
        self._raw_info_padding: bytes | None = None

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
        body += _pack_preserved_fixed_string(
            self.internal_name, IFP_V2_NAME_SIZE, self._raw_internal_name_data
        )
        body += struct.pack("<I", len(self.animations))

        for animation in self.animations:
            body += _pack_preserved_fixed_string(
                animation.name, IFP_V2_NAME_SIZE, animation._raw_name_data
            )
            body += struct.pack(
                "<III",
                len(animation.objects),
                animation.frame_data_size,
                animation.unknown,
            )
            for obj in animation.objects:
                body += _pack_preserved_fixed_string(
                    obj.name, IFP_V2_NAME_SIZE, obj._raw_name_data
                )
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

    def get_animation_duration(self, name: str) -> float | None:
        animation = self.get_animation(name)
        return animation.duration if animation is not None else None

    def sample_animation(
        self,
        name: str,
        time: float,
        *,
        out_of_range: IfpOutOfRange = "clamp",
    ) -> list[tuple[IfpObject, IfpFrame | None]]:
        animation = self.get_animation(name)
        if animation is None:
            raise KeyError(f"Animation not found: {name}")
        return animation.sample(time, out_of_range=out_of_range)

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
                    "anpk_name_unknown": obj.anpk_name_unknown,
                    "anpk_unknown": list(obj.anpk_unknown),
                    "anpk_extra": obj.anpk_extra,
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

    def validate(self) -> list[IfpValidationIssue]:
        issues: list[IfpValidationIssue] = []
        animation_names: set[str] = set()

        for animation in self.animations:
            animation_key = animation.name.casefold()
            if animation_key in animation_names:
                issues.append(IfpValidationIssue(
                    "duplicate_animation_name",
                    f"Duplicate animation name {animation.name!r}",
                    animation_name=animation.name,
                ))
            animation_names.add(animation_key)

            if (
                animation.declared_frame_data_size is not None
                and animation.declared_frame_data_size != animation.frame_data_size
            ):
                issues.append(IfpValidationIssue(
                    "frame_data_size_mismatch",
                    f"Animation {animation.name!r} declares "
                    f"{animation.declared_frame_data_size} frame bytes but contains "
                    f"{animation.frame_data_size}",
                    animation_name=animation.name,
                ))

            object_names: set[str] = set()
            for obj in animation.objects:
                object_key = obj.name.casefold()
                if object_key in object_names:
                    issues.append(IfpValidationIssue(
                        "duplicate_object_name",
                        f"Duplicate object name {obj.name!r} in {animation.name!r}",
                        animation_name=animation.name,
                        object_name=obj.name,
                    ))
                object_names.add(object_key)

                if obj.keyframe_type == IfpKeyframeType.NONE and obj.frames:
                    issues.append(IfpValidationIssue(
                        "frames_without_keyframe_type",
                        f"Object {animation.name!r}/{obj.name!r} has frames but uses K000",
                        animation_name=animation.name,
                        object_name=obj.name,
                    ))

                previous_time = -math.inf
                for index, frame in enumerate(obj.frames):
                    context = f"{animation.name!r}/{obj.name!r} frame {index}"
                    values = (*frame.rotation, *frame.translation)
                    if frame.scale is not None:
                        values += frame.scale
                    if not math.isfinite(frame.time) or not all(map(math.isfinite, values)):
                        issues.append(IfpValidationIssue(
                            "non_finite_frame",
                            f"Non-finite value in {context}",
                            animation_name=animation.name,
                            object_name=obj.name,
                        ))
                    if frame.time < previous_time:
                        issues.append(IfpValidationIssue(
                            "non_monotonic_time",
                            f"Frame times are not monotonic in {animation.name!r}/{obj.name!r}",
                            animation_name=animation.name,
                            object_name=obj.name,
                        ))
                        break
                    previous_time = frame.time
                    if sum(component * component for component in frame.rotation) == 0.0:
                        issues.append(IfpValidationIssue(
                            "zero_quaternion",
                            f"Zero-length quaternion in {context}",
                            animation_name=animation.name,
                            object_name=obj.name,
                        ))
                    if int(self.version) == int(IfpVersion.ANP3) and frame.scale is not None:
                        issues.append(IfpValidationIssue(
                            "unsupported_anp3_scale",
                            f"ANP3 cannot store scale in {context}",
                            animation_name=animation.name,
                            object_name=obj.name,
                        ))

        return issues

    def validate_or_raise(self):
        issues = self.validate()
        if issues:
            raise IfpValidationError(issues)

    def _parse_anpk(self, stream: io.BytesIO):
        self.version = IfpVersion.ANPK
        magic = _read_exact(stream, 4)
        assert magic == IFP_ANPK_MAGIC
        _read_exact(stream, 4)

        info, self._raw_info_padding = _read_section_full(stream, b"INFO")
        if len(info) < 5:
            raise ValueError("ANPK package INFO section is too small")
        animation_count = struct.unpack_from("<I", info)[0]
        self._raw_internal_name_data = info[4:]
        self.internal_name = _decode_string(self._raw_internal_name_data)

        for _ in range(animation_count):
            name_data, name_padding = _read_section_full(stream, b"NAME")
            dgan_data, dgan_padding = _read_section_full(stream, b"DGAN")
            animation = IfpAnimation(
                name=_decode_string(name_data),
                _raw_name_data=name_data,
                _raw_name_padding=name_padding,
                _raw_dgan_padding=dgan_padding,
            )
            dgan = io.BytesIO(dgan_data)
            animation_info, animation._raw_info_padding = _read_section_full(
                dgan, b"INFO"
            )
            if len(animation_info) < 4:
                raise ValueError(f"ANPK animation INFO is too small for {animation.name!r}")
            animation._raw_info_data = animation_info
            object_count = struct.unpack_from("<I", animation_info)[0]

            for _ in range(object_count):
                cpan_data, cpan_padding = _read_section_full(dgan, b"CPAN")
                cpan = io.BytesIO(cpan_data)
                object_info, anim_padding = _read_section_full(cpan, b"ANIM")
                if len(object_info) not in (40, 44, 48):
                    raise ValueError(
                        f"Unsupported ANPK ANIM section size {len(object_info)} "
                        f"in {animation.name!r}"
                    )

                name_unknown = struct.unpack_from("<i", object_info, 24)[0]
                frame_count = struct.unpack_from("<I", object_info, 28)[0]
                unknown = struct.unpack_from("<ii", object_info, 32)
                obj = IfpObject(
                    name=_decode_string(object_info[:24]),
                    frame_type=IfpFrameType.CHILD_FLOAT,
                    bone_id=-1,
                    anpk_name_unknown=name_unknown,
                    anpk_unknown=unknown,
                    _raw_name_data=object_info[:24],
                    _anpk_anim_size=len(object_info),
                    _raw_cpan_padding=cpan_padding,
                    _raw_anim_padding=anim_padding,
                )
                if len(object_info) >= 44:
                    obj.bone_id = struct.unpack_from("<i", object_info, 40)[0]
                if len(object_info) == 48:
                    obj.anpk_extra = struct.unpack_from("<i", object_info, 44)[0]

                if frame_count:
                    key_magic, key_data, obj._raw_key_padding = _read_any_section_full(cpan)
                    obj._raw_key_data = key_data
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
        package_info = struct.pack("<I", len(self.animations)) + _pack_preserved_c_string(
            self.internal_name, self._raw_internal_name_data
        )
        content += _pack_section(b"INFO", package_info, self._raw_info_padding)

        for animation in self.animations:
            content += _pack_section(
                b"NAME",
                _pack_preserved_c_string(animation.name, animation._raw_name_data),
                animation._raw_name_padding,
            )
            dgan = bytearray()
            info_tail = (
                animation._raw_info_data[4:]
                if animation._raw_info_data is not None
                else struct.pack("<I", 0)
            )
            dgan += _pack_section(
                b"INFO",
                struct.pack("<I", len(animation.objects)) + info_tail,
                animation._raw_info_padding,
            )

            for obj in animation.objects:
                key_type = obj.keyframe_type or _anpk_type_for_object(obj)
                unknown = obj.anpk_unknown

                object_info = bytearray(_pack_preserved_fixed_string(
                    obj.name, 24, obj._raw_name_data
                ))
                object_info += struct.pack(
                    "<iIii",
                    obj.anpk_name_unknown,
                    len(obj.frames),
                    *unknown,
                )
                anim_size = obj._anpk_anim_size or 44
                if anim_size >= 44:
                    object_info += struct.pack("<i", obj.bone_id)
                if anim_size == 48:
                    object_info += struct.pack("<i", obj.anpk_extra or 0)

                cpan = bytearray(_pack_section(
                    b"ANIM", bytes(object_info), obj._raw_anim_padding
                ))
                if obj.frames:
                    cpan += _pack_section(
                        key_type.value.encode("ascii"),
                        _pack_preserved_anpk_frames(
                            obj.frames, key_type, obj._raw_key_data
                        ),
                        obj._raw_key_padding,
                    )
                dgan += _pack_section(
                    b"CPAN", bytes(cpan), obj._raw_cpan_padding
                )

            content += _pack_section(
                b"DGAN", bytes(dgan), animation._raw_dgan_padding
            )

        return IFP_ANPK_MAGIC + struct.pack("<I", len(content)) + bytes(content)

    def _parse_v2(self, stream: io.BytesIO):
        self.version = IfpVersion.ANP3
        magic = _read_exact(stream, 4)
        assert magic == IFP_ANP3_MAGIC
        _file_end = struct.unpack("<I", _read_exact(stream, 4))[0]
        self.internal_name, self._raw_internal_name_data = _read_fixed_string_raw(
            stream, IFP_V2_NAME_SIZE
        )
        animation_count = struct.unpack("<I", _read_exact(stream, 4))[0]

        for _ in range(animation_count):
            animation_name, animation_name_raw = _read_fixed_string_raw(
                stream, IFP_V2_NAME_SIZE
            )
            animation = IfpAnimation(
                name=animation_name,
                _raw_name_data=animation_name_raw,
            )
            object_count, frame_data_size, animation.unknown = struct.unpack(
                "<III", _read_exact(stream, 12)
            )
            animation.declared_frame_data_size = frame_data_size

            for _ in range(object_count):
                object_name, object_name_raw = _read_fixed_string_raw(
                    stream, IFP_V2_NAME_SIZE
                )
                obj = IfpObject(
                    name=object_name,
                    _raw_name_data=object_name_raw,
                )
                frame_type, frame_count, bone_id = struct.unpack(
                    "<III", _read_exact(stream, 12)
                )
                obj.frame_type = frame_type
                obj.bone_id = _to_i32(bone_id)

                for _ in range(frame_count):
                    obj.frames.append(_read_v2_frame(stream, frame_type))

                animation.objects.append(obj)

            if animation.frame_data_size != animation.declared_frame_data_size:
                raise ValueError(
                    f"ANP3 frame data size mismatch in {animation.name!r}: "
                    f"declared {animation.declared_frame_data_size}, "
                    f"computed {animation.frame_data_size}"
                )
            self.animations.append(animation)


def _resolve_sample_time(
    time: float,
    duration: float,
    out_of_range: IfpOutOfRange,
) -> float:
    if out_of_range not in ("clamp", "error"):
        raise ValueError("out_of_range must be 'clamp' or 'error'")
    if not math.isfinite(time):
        raise ValueError("Sample time must be finite")
    if out_of_range == "error" and not 0.0 <= time <= duration:
        raise ValueError(f"Sample time {time} is outside 0..{duration}")
    return min(duration, max(0.0, time))


def _ordered_unique_frames(frames: list[IfpFrame]) -> list[IfpFrame]:
    ordered = sorted(enumerate(frames), key=lambda item: (item[1].time, item[0]))
    unique: list[IfpFrame] = []
    for _index, frame in ordered:
        if unique and frame.time == unique[-1].time:
            unique[-1] = frame
        else:
            unique.append(frame)
    return unique


def _held_frame(frame: IfpFrame, time: float) -> IfpFrame:
    return IfpFrame(
        rotation=frame.rotation,
        translation=frame.translation,
        scale=frame.scale,
        time=time,
        raw_time=None,
    )


def _lerp_vector(left, right, factor: float):
    return tuple(a + (b - a) * factor for a, b in zip(left, right))


def _normalize_quaternion(rotation) -> tuple[float, float, float, float]:
    if len(rotation) != 4 or not all(map(math.isfinite, rotation)):
        raise ValueError("Quaternion components must be finite")
    length_squared = sum(component * component for component in rotation)
    if length_squared == 0.0:
        raise ValueError("Cannot normalize a zero-length quaternion")
    inverse_length = 1.0 / math.sqrt(length_squared)
    return tuple(component * inverse_length for component in rotation)


def _slerp(left, right, factor: float) -> tuple[float, float, float, float]:
    first = _normalize_quaternion(left)
    second = _normalize_quaternion(right)
    dot = sum(a * b for a, b in zip(first, second))
    if dot < 0.0:
        second = tuple(-component for component in second)
        dot = -dot
    dot = min(1.0, max(-1.0, dot))
    if dot > 0.9995:
        return _normalize_quaternion(_lerp_vector(first, second, factor))
    angle = math.acos(dot)
    sine = math.sin(angle)
    left_weight = math.sin((1.0 - factor) * angle) / sine
    right_weight = math.sin(factor * angle) / sine
    return tuple(
        a * left_weight + b * right_weight
        for a, b in zip(first, second)
    )


def _align4(value: int) -> int:
    return (value + 3) & ~3


def _read_any_section_full(stream: io.BytesIO) -> tuple[bytes, bytes, bytes]:
    magic = _read_exact(stream, 4)
    size = struct.unpack("<I", _read_exact(stream, 4))[0]
    data = _read_exact(stream, size)
    padding_size = _align4(size) - size
    padding = _read_exact(stream, padding_size) if padding_size else b""
    return magic, data, padding


def _read_any_section(stream: io.BytesIO) -> tuple[bytes, bytes]:
    magic, data, _padding = _read_any_section_full(stream)
    return magic, data


def _read_section(stream: io.BytesIO, expected: bytes) -> bytes:
    magic, data = _read_any_section(stream)
    if magic != expected:
        raise ValueError(f"Expected ANPK section {expected!r}, got {magic!r}")
    return data


def _read_section_full(stream: io.BytesIO, expected: bytes) -> tuple[bytes, bytes]:
    magic, data, padding = _read_any_section_full(stream)
    if magic != expected:
        raise ValueError(f"Expected ANPK section {expected!r}, got {magic!r}")
    return data, padding


def _pack_section(magic: bytes, data: bytes, preserved_padding: bytes | None = None) -> bytes:
    if len(magic) != 4:
        raise ValueError(f"IFP section identifiers must be four bytes: {magic!r}")
    padding_size = _align4(len(data)) - len(data)
    padding = (
        preserved_padding
        if preserved_padding is not None and len(preserved_padding) == padding_size
        else b"\x00" * padding_size
    )
    return magic + struct.pack("<I", len(data)) + data + padding


def _decode_string(data: bytes) -> str:
    return data.split(b"\x00", 1)[0].decode("ascii", errors="replace")


def _pack_c_string(value: str) -> bytes:
    return _encode_name(value) + b"\x00"


def _pack_preserved_c_string(value: str, raw: bytes | None) -> bytes:
    if raw is not None and _decode_string(raw) == value:
        return raw
    return _pack_c_string(value)


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


def _same_float(left: float, right: float) -> bool:
    return left == right or (math.isnan(left) and math.isnan(right))


def _same_vector(left, right) -> bool:
    return all(_same_float(a, b) for a, b in zip(left, right))


def _anpk_frames_match_raw(
    frames: list[IfpFrame],
    key_type: IfpKeyframeType,
    raw: bytes,
) -> bool:
    if len(raw) != len(frames) * _anpk_frame_size(key_type):
        return False
    offset = 0
    for frame in frames:
        qx, qy, qz, qw = struct.unpack_from("<4f", raw, offset)
        offset += 16
        if not _same_vector(frame.rotation, (-qx, -qy, -qz, qw)):
            return False
        if key_type in (
            IfpKeyframeType.ROTATION_TRANSLATION,
            IfpKeyframeType.ROTATION_TRANSLATION_SCALE,
        ):
            translation = struct.unpack_from("<3f", raw, offset)
            offset += 12
            if not _same_vector(frame.translation, translation):
                return False
        if key_type == IfpKeyframeType.ROTATION_TRANSLATION_SCALE:
            scale = struct.unpack_from("<3f", raw, offset)
            offset += 12
            if frame.scale is None or not _same_vector(frame.scale, scale):
                return False
        time = struct.unpack_from("<f", raw, offset)[0]
        offset += 4
        if not _same_float(frame.time, time):
            return False
    return True


def _pack_preserved_anpk_frames(
    frames: list[IfpFrame],
    key_type: IfpKeyframeType,
    raw: bytes | None,
) -> bytes:
    if raw is not None and _anpk_frames_match_raw(frames, key_type, raw):
        return raw
    return _pack_anpk_frames(frames, key_type)


def _read_exact(stream: io.BytesIO, count: int) -> bytes:
    data = stream.read(count)
    if len(data) != count:
        raise EOFError(f"Expected {count} bytes, got {len(data)}")
    return data


def _read_fixed_string(stream: io.BytesIO, size: int) -> str:
    value, _raw = _read_fixed_string_raw(stream, size)
    return value


def _read_fixed_string_raw(stream: io.BytesIO, size: int) -> tuple[str, bytes]:
    raw = _read_exact(stream, size)
    return _decode_string(raw), raw


def _encode_name(value: str) -> bytes:
    try:
        return value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"IFP names must be ASCII: {value!r}") from exc


def _pack_fixed_string(value: str, size: int) -> bytes:
    return _encode_name(value).ljust(size, b"\x00")[:size]


def _pack_preserved_fixed_string(
    value: str,
    size: int,
    raw: bytes | None,
) -> bytes:
    if raw is not None and len(raw) == size and _decode_string(raw) == value:
        return raw
    return _pack_fixed_string(value, size)


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
    if (
        frame.raw_time is not None
        and math.isclose(
            frame.time,
            frame.raw_time / IFP_V2_TIME_SCALE,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        raw_time = _to_i16(frame.raw_time)
    else:
        raw_time = _to_i16(round(frame.time * IFP_V2_TIME_SCALE))

    if frame_type == int(IfpFrameType.ROOT):
        tx, ty, tz = (_to_i16(component * IFP_V2_TRANS_SCALE) for component in frame.translation)
        return struct.pack("<8h", qx, qy, qz, qw, raw_time, tx, ty, tz)

    if frame_type == int(IfpFrameType.CHILD):
        return struct.pack("<5h", qx, qy, qz, qw, raw_time)

    raise ValueError(f"Unsupported ANP3 frame type: {frame_type}")


def _to_i16(value: float) -> int:
    rounded = int(round(value))
    if not -32768 <= rounded <= 32767:
        raise ValueError(f"IFP value {value!r} does not fit in a signed 16-bit field")
    return rounded


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
