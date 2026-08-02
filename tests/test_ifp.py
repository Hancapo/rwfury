import math

import pytest

from rwfury import (
    Ifp,
    IfpAnimation,
    IfpFrame,
    IfpFrameType,
    IfpKeyframeType,
    IfpObject,
    IfpVersion,
)


def _assert_tuple_close(actual, expected):
    assert actual == pytest.approx(expected)


def test_anpk_reads_and_writes_all_keyframe_types():
    package = Ifp()
    package.version = IfpVersion.ANPK
    package.internal_name = "cutscene"
    package.animations = [
        IfpAnimation(
            name="camera_move",
            objects=[
                IfpObject(
                    name="rotation",
                    bone_id=1,
                    keyframe_type=IfpKeyframeType.ROTATION,
                    frames=[IfpFrame(rotation=(0.1, 0.2, 0.3, 0.9), time=0.5)],
                ),
                IfpObject(
                    name="translated",
                    bone_id=2,
                    keyframe_type=IfpKeyframeType.ROTATION_TRANSLATION,
                    frames=[IfpFrame(
                        rotation=(0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5)),
                        translation=(1.0, -2.0, 3.5),
                        time=1.25,
                    )],
                ),
                IfpObject(
                    name="scaled",
                    bone_id=-1,
                    keyframe_type=IfpKeyframeType.ROTATION_TRANSLATION_SCALE,
                    frames=[IfpFrame(
                        translation=(4.0, 5.0, 6.0),
                        scale=(1.0, 2.0, 0.5),
                        time=2.0,
                    )],
                ),
                IfpObject(
                    name="empty",
                    bone_id=4,
                    keyframe_type=IfpKeyframeType.NONE,
                ),
            ],
        )
    ]

    encoded = package.to_bytes()
    decoded = Ifp.from_bytes(encoded)

    assert decoded.version == IfpVersion.ANPK
    assert decoded.internal_name == "cutscene"
    assert decoded.get_animation_names() == ["camera_move"]
    objects = decoded.animations[0].objects
    assert [obj.keyframe_type for obj in objects] == [
        IfpKeyframeType.ROTATION,
        IfpKeyframeType.ROTATION_TRANSLATION,
        IfpKeyframeType.ROTATION_TRANSLATION_SCALE,
        IfpKeyframeType.NONE,
    ]
    _assert_tuple_close(objects[0].frames[0].rotation, (0.1, 0.2, 0.3, 0.9))
    _assert_tuple_close(objects[1].frames[0].translation, (1.0, -2.0, 3.5))
    _assert_tuple_close(objects[2].frames[0].scale, (1.0, 2.0, 0.5))
    assert decoded.to_bytes() == encoded


def test_anpk_preserves_sibling_metadata_shape():
    package = Ifp()
    package.version = IfpVersion.ANPK
    package.internal_name = "objects"
    package.animations = [IfpAnimation(
        name="move",
        objects=[IfpObject(
            name="part",
            bone_id=-1,
            keyframe_type=IfpKeyframeType.ROTATION,
            sibling_indices=(2, 4),
            frames=[IfpFrame(time=0.0)],
        )],
    )]

    decoded = Ifp.from_bytes(package.to_bytes())

    assert decoded.animations[0].objects[0].sibling_indices == (2, 4)


def test_anp3_uncompressed_rotation_track_type_one():
    package = Ifp()
    package.internal_name = "ped"
    package.animations = [IfpAnimation(
        name="idle",
        objects=[IfpObject(
            name="Spine",
            frame_type=IfpFrameType.CHILD_FLOAT,
            bone_id=3,
            frames=[IfpFrame(rotation=(0.1, 0.2, 0.3, 0.9), time=0.75)],
        )],
    )]

    decoded = Ifp.from_bytes(package.to_bytes())
    obj = decoded.animations[0].objects[0]

    assert obj.frame_type == IfpFrameType.CHILD_FLOAT
    assert obj.has_translation is False
    _assert_tuple_close(obj.frames[0].rotation, (0.1, 0.2, 0.3, 0.9))
    assert obj.frames[0].time == pytest.approx(0.75)


def test_anpk_rejects_inconsistent_keyframe_section_size():
    package = Ifp()
    package.version = IfpVersion.ANPK
    package.internal_name = "bad"
    package.animations = [IfpAnimation(
        name="bad",
        objects=[IfpObject(
            name="bone",
            keyframe_type=IfpKeyframeType.ROTATION,
            frames=[IfpFrame()],
        )],
    )]
    encoded = bytearray(package.to_bytes())
    key_offset = encoded.index(b"KR00")
    encoded[key_offset + 4:key_offset + 8] = (16).to_bytes(4, "little")

    with pytest.raises((EOFError, ValueError)):
        Ifp.from_bytes(bytes(encoded))
