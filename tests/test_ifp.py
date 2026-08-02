import math

import pytest

from rwfury import (
    Ifp,
    IfpAnimation,
    IfpFrame,
    IfpFrameType,
    IfpKeyframeType,
    IfpObject,
    IfpValidationError,
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


def test_anpk_preserves_raw_object_metadata():
    package = Ifp()
    package.version = IfpVersion.ANPK
    package.internal_name = "objects"
    package.animations = [IfpAnimation(
        name="move",
        objects=[IfpObject(
            name="part",
            bone_id=21,
            keyframe_type=IfpKeyframeType.ROTATION,
            anpk_name_unknown=0x1020304,
            anpk_unknown=(-20, 40),
            frames=[IfpFrame(time=0.0)],
        )],
    )]

    decoded = Ifp.from_bytes(package.to_bytes())
    obj = decoded.animations[0].objects[0]

    assert obj.bone_id == 21
    assert obj.anpk_name_unknown == 0x1020304
    assert obj.anpk_unknown == (-20, 40)


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


def test_anp3_preserves_name_padding_when_frames_are_edited():
    package = Ifp()
    package.internal_name = "ped"
    package.animations = [IfpAnimation(
        name="idle",
        objects=[IfpObject(
            name="Root",
            frame_type=IfpFrameType.CHILD_FLOAT,
            frames=[IfpFrame(time=0.0)],
        )],
    )]
    encoded = bytearray(package.to_bytes())
    encoded[12] = 0xA5
    encoded[41] = 0xB6
    encoded[77] = 0xC7

    decoded = Ifp.from_bytes(bytes(encoded))
    decoded.animations[0].objects[0].frames[0].time = 1.0
    rewritten = decoded.to_bytes()

    assert rewritten[8:32] == encoded[8:32]
    assert rewritten[36:60] == encoded[36:60]
    assert rewritten[72:96] == encoded[72:96]
    assert rewritten != bytes(encoded)


def test_anpk_preserves_external_section_padding_when_frames_are_edited():
    package = Ifp()
    package.version = IfpVersion.ANPK
    package.internal_name = "pkg"
    package.animations = [IfpAnimation(
        name="move1",
        objects=[IfpObject(
            name="Root",
            keyframe_type=IfpKeyframeType.ROTATION,
            frames=[IfpFrame(time=0.0)],
        )],
    )]
    encoded = bytearray(package.to_bytes())
    name_offset = encoded.index(b"NAME")
    name_size = int.from_bytes(encoded[name_offset + 4:name_offset + 8], "little")
    padding_offset = name_offset + 8 + name_size
    encoded[padding_offset:padding_offset + 2] = b"\xA5\xB6"

    decoded = Ifp.from_bytes(bytes(encoded))
    decoded.animations[0].objects[0].frames[0].time = 2.0
    rewritten = decoded.to_bytes()

    assert rewritten[padding_offset:padding_offset + 2] == b"\xA5\xB6"


def test_anpk_unedited_nan_payload_round_trips_bit_exactly():
    package = Ifp()
    package.version = IfpVersion.ANPK
    package.internal_name = "pkg"
    package.animations = [IfpAnimation(
        name="broken",
        objects=[IfpObject(
            name="Root",
            keyframe_type=IfpKeyframeType.ROTATION,
            frames=[IfpFrame()],
        )],
    )]
    encoded = bytearray(package.to_bytes())
    key_offset = encoded.index(b"KR00") + 8
    encoded[key_offset:key_offset + 4] = (0x7FC01234).to_bytes(4, "little")

    decoded = Ifp.from_bytes(bytes(encoded))

    assert math.isnan(decoded.animations[0].objects[0].frames[0].rotation[0])
    assert decoded.to_bytes() == bytes(encoded)


def test_validation_reports_duplicate_names_bad_time_and_zero_quaternion():
    package = Ifp()
    package.animations = [
        IfpAnimation(name="idle"),
        IfpAnimation(
            name="IDLE",
            objects=[IfpObject(
                name="Root",
                frames=[
                    IfpFrame(rotation=(0.0, 0.0, 0.0, 0.0), time=1.0),
                    IfpFrame(time=0.5),
                ],
            )],
        ),
    ]

    issues = package.validate()

    assert {issue.code for issue in issues} == {
        "duplicate_animation_name",
        "zero_quaternion",
        "non_monotonic_time",
    }
    with pytest.raises(IfpValidationError) as error:
        package.validate_or_raise()
    assert error.value.issues == issues


def test_anp3_writer_rejects_quantization_overflow():
    package = Ifp()
    package.animations = [IfpAnimation(
        name="move",
        objects=[IfpObject(
            name="Root",
            frame_type=IfpFrameType.ROOT,
            frames=[IfpFrame(translation=(100.0, 0.0, 0.0))],
        )],
    )]

    with pytest.raises(ValueError, match="signed 16-bit"):
        package.to_bytes()


def test_writer_rejects_names_that_do_not_fit_fixed_fields():
    package = Ifp()
    package.internal_name = "x" * 25

    with pytest.raises(ValueError, match="24-byte"):
        package.to_bytes()


def test_anp3_writer_rejects_scale_instead_of_dropping_it():
    package = Ifp()
    package.animations = [IfpAnimation(
        name="scaled",
        objects=[IfpObject(
            name="Root",
            frame_type=IfpFrameType.CHILD_FLOAT,
            frames=[IfpFrame(scale=(1.0, 1.0, 1.0))],
        )],
    )]

    with pytest.raises(ValueError, match="cannot store scale"):
        package.to_bytes()


def test_object_sampling_sorts_without_mutating_and_last_duplicate_wins():
    last = IfpFrame(translation=(5.0, 0.0, 0.0), time=2.0)
    first = IfpFrame(translation=(0.0, 0.0, 0.0), time=0.0)
    duplicate_a = IfpFrame(translation=(1.0, 0.0, 0.0), time=1.0)
    duplicate_b = IfpFrame(translation=(3.0, 0.0, 0.0), time=1.0)
    obj = IfpObject(frames=[last, first, duplicate_a, duplicate_b])
    original_order = list(obj.frames)

    assert obj.sample(0.5).translation == pytest.approx((1.5, 0.0, 0.0))
    assert obj.sample(1.0).translation == pytest.approx((3.0, 0.0, 0.0))
    assert obj.sample(1.5).translation == pytest.approx((4.0, 0.0, 0.0))
    assert obj.frames == original_order


def test_frame_interpolation_uses_shortest_slerp_and_identity_scale():
    first = IfpFrame(rotation=(0.0, 0.0, 0.0, 1.0), scale=None, time=0.0)
    second = IfpFrame(
        rotation=(0.0, 0.0, 1.0, 0.0),
        scale=(3.0, 5.0, 7.0),
        time=2.0,
    )

    sampled = first.interpolate(second, 0.5)

    assert sampled.rotation == pytest.approx(
        (0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5))
    )
    assert sampled.scale == pytest.approx((2.0, 3.0, 4.0))
    assert sampled.time == pytest.approx(1.0)
    assert sampled.raw_time is None
    same_orientation = first.interpolate(
        IfpFrame(rotation=(0.0, 0.0, 0.0, -1.0)), 0.5
    )
    assert same_orientation.rotation == pytest.approx((0.0, 0.0, 0.0, 1.0))


def test_sampling_range_empty_tracks_and_animation_order():
    short = IfpObject(name="same", frames=[IfpFrame(time=1.0)])
    empty = IfpObject(name="same")
    animation = IfpAnimation(name="clip", objects=[short, empty])

    assert animation.duration == pytest.approx(1.0)
    assert short.sample(-5.0).time == pytest.approx(0.0)
    assert short.sample(5.0).time == pytest.approx(1.0)
    with pytest.raises(ValueError, match="outside"):
        animation.sample(2.0, out_of_range="error")
    sampled = animation.sample(0.5)
    assert [obj for obj, _frame in sampled] == [short, empty]
    assert sampled[0][1] is not None
    assert sampled[1][1] is None


def test_ifp_high_level_sampling_and_local_matrix():
    package = Ifp()
    obj = IfpObject(name="Root", frames=[IfpFrame(
        translation=(1.0, 2.0, 3.0),
        scale=(2.0, 3.0, 4.0),
        time=0.0,
    )])
    package.animations = [IfpAnimation(name="move", objects=[obj])]

    assert package.get_animation_duration("MOVE") == pytest.approx(0.0)
    sampled = package.sample_animation("move", 0.0)[0][1]
    assert sampled.to_matrix() == pytest.approx((
        2.0, 0.0, 0.0, 1.0,
        0.0, 3.0, 0.0, 2.0,
        0.0, 0.0, 4.0, 3.0,
        0.0, 0.0, 0.0, 1.0,
    ))
    with pytest.raises(KeyError):
        package.sample_animation("missing", 0.0)


def test_interpolation_rejects_invalid_inputs():
    with pytest.raises(ValueError, match="zero-length"):
        IfpFrame(rotation=(0.0, 0.0, 0.0, 0.0)).interpolate(IfpFrame(), 0.5)
    with pytest.raises(ValueError, match="factor"):
        IfpFrame().interpolate(IfpFrame(), 2.0)
    with pytest.raises(ValueError, match="finite"):
        IfpObject(frames=[IfpFrame()]).sample(math.nan)
