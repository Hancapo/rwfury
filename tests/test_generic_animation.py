import math
import struct

import pytest

from rwfury import (
    GenericAnimationTrack,
    Ifp,
    IfpAnimation,
    IfpFrame,
    IfpFrameType,
    IfpKeyframeType,
    IfpObject,
    IfpVersion,
)


def test_generic_export_preserves_source_order_channels_and_duplicate_names():
    first_frame = IfpFrame(
        rotation=(2.0, 3.0, 4.0, 5.0),
        translation=(10.0, 20.0, 30.0),
        time=2.0,
        raw_time=120,
    )
    package = Ifp()
    package.internal_name = "ped"
    package.animations = [IfpAnimation(
        name="odd",
        unknown=17,
        declared_frame_data_size=123,
        objects=[
            IfpObject(
                name="Root",
                frame_type=IfpFrameType.ROOT,
                bone_id=0,
                frames=[
                    first_frame,
                    IfpFrame(
                        rotation=(0.0, 0.0, 0.0, 1.0),
                        translation=(-1.0, -2.0, -3.0),
                        time=-1.0,
                        raw_time=-60,
                    ),
                ],
            ),
            IfpObject(
                name="root",
                frame_type=IfpFrameType.CHILD_FLOAT,
                bone_id=-1,
                frames=[IfpFrame(
                    translation=(99.0, 99.0, 99.0),
                    time=0.5,
                )],
            ),
        ],
    )]

    generic = package.to_generic_animation_set()
    clip = generic.animations[0]
    root, duplicate = clip.get_tracks("ROOT")

    assert generic.name == "ped"
    assert generic.source_format == "ANP3"
    assert generic.time_unit == "seconds"
    assert generic.rotation_order == "xyzw"
    assert generic.transform_space == "local"
    assert clip.source_index == 0
    assert [track.source_index for track in clip.tracks] == [0, 1]
    assert root.times == [2.0, -1.0]
    assert root.rotations[:4] == [2.0, 3.0, 4.0, 5.0]
    assert root.translations == [10.0, 20.0, 30.0, -1.0, -2.0, -3.0]
    assert root.raw_times == [120, -60]
    assert duplicate.translations is None
    assert duplicate.raw_times is None
    assert duplicate.get_keyframe(0).translation is None
    assert root.get_keyframe(-1).raw_time == -60
    assert clip.get_tracks_by_bone_id(0) == [root]
    assert clip.source_metadata["source_unknown"] == 17
    assert root.source_metadata["source_frame_type"] == int(IfpFrameType.ROOT)

    first_frame.rotation = (9.0, 9.0, 9.0, 9.0)
    assert root.get_keyframe(0).rotation == (2.0, 3.0, 4.0, 5.0)


def test_generic_export_preserves_anpk_scale_and_opaque_metadata():
    package = Ifp()
    package.version = IfpVersion.ANPK
    package.internal_name = "props"
    package.animations = [IfpAnimation(
        name="scaled",
        objects=[IfpObject(
            name="PrismLAS",
            bone_id=21,
            keyframe_type=IfpKeyframeType.ROTATION_TRANSLATION_SCALE,
            anpk_name_unknown=0x1020304,
            anpk_unknown=(-20, 40),
            anpk_extra=99,
            frames=[IfpFrame(
                rotation=(0.1, 0.2, 0.3, 0.9),
                translation=(1.0, 2.0, 3.0),
                scale=(4.0, 5.0, 6.0),
                time=1.25,
            )],
        )],
    )]

    track = package.get_generic_animation("SCALED").tracks[0]
    frame = next(track.iter_keyframes())

    assert track.has_translation is True
    assert track.has_scale is True
    assert frame.translation == (1.0, 2.0, 3.0)
    assert frame.scale == (4.0, 5.0, 6.0)
    assert track.source_metadata == {
        "source_frame_type": int(IfpFrameType.CHILD),
        "source_keyframe_type": "KRTS",
        "anpk_name_unknown": 0x1020304,
        "anpk_unknown": (-20, 40),
        "anpk_extra": 99,
    }


def test_generic_track_exposes_portable_deinterleaved_buffers():
    track = GenericAnimationTrack(
        times=[0.25, 0.5],
        rotations=[
            0.0, 0.0, 0.0, 1.0,
            math.nan, 0.0, 1.0, 0.0,
        ],
        translations=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        raw_times=[15, 30],
    )

    buffers = track.to_buffers("f64", "big", "i32")

    assert buffers.keyframe_count == 2
    assert struct.unpack(">2d", buffers.times) == (0.25, 0.5)
    rotations = struct.unpack(">8d", buffers.rotations)
    assert rotations[:4] == (0.0, 0.0, 0.0, 1.0)
    assert math.isnan(rotations[4])
    assert struct.unpack(">6d", buffers.translations) == (
        1.0, 2.0, 3.0, 4.0, 5.0, 6.0,
    )
    assert buffers.scales is None
    assert struct.unpack(">2i", buffers.raw_times) == (15, 30)
    assert buffers.raw_time_format == "i32"


def test_generic_track_rejects_ambiguous_or_inconsistent_channels():
    with pytest.raises(ValueError, match="rotations"):
        GenericAnimationTrack(times=[0.0], rotations=[])
    with pytest.raises(ValueError, match="translations"):
        GenericAnimationTrack(
            times=[0.0],
            rotations=[0.0, 0.0, 0.0, 1.0],
            translations=[],
        )

    track = GenericAnimationTrack(
        times=[0.0],
        rotations=[0.0, 0.0, 0.0, 1.0],
        raw_times=[None],
    )
    with pytest.raises(ValueError, match="missing"):
        track.raw_times_as_bytes()


def test_generic_animation_iterator_does_not_require_eager_package_conversion():
    package = Ifp()
    package.animations = [
        IfpAnimation(name="first"),
        IfpAnimation(name="second"),
    ]

    iterator = package.iter_generic_animations()

    assert next(iterator).name == "first"
    package.animations[1].name = "changed-before-conversion"
    assert next(iterator).name == "changed-before-conversion"
    with pytest.raises(StopIteration):
        next(iterator)
