import pytest

from streamkeeper.models import ProbeSnapshot
from streamkeeper.planner import build_plan
from streamkeeper.policy import plan_audio


def audio(codec, channels, *, bitrate=0, language="eng", title="", default=0, profile=""):
    return {
        "codec_type": "audio",
        "codec_name": codec,
        "profile": profile,
        "channels": channels,
        "bit_rate": str(bitrate),
        "tags": {"language": language, "title": title},
        "disposition": {"default": default},
    }


def snapshot(*audio_streams):
    return ProbeSnapshot(
        "/media/Fixture.mkv",
        "2026-01-01T00:00:00Z",
        {"duration": "1"},
        [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "color_transfer": "bt709",
                "disposition": {},
            },
            *(
                {**stream, "index": ordinal}
                for ordinal, stream in enumerate(audio_streams, start=1)
            ),
        ],
    )


@pytest.mark.parametrize(
    ("source", "codec", "channels", "bitrate"),
    [
        (audio("truehd", 8, bitrate=4_000_000), "eac3", 6, 640_000),
        (audio("dts", 6, bitrate=1_509_000), "eac3", 6, 640_000),
        (audio("eac3", 8, bitrate=768_000, title="Atmos"), "eac3", 6, 640_000),
        (audio("aac", 8, bitrate=768_000), "eac3", 6, 640_000),
        (audio("flac", 5, bitrate=448_000), "eac3", 5, 448_000),
        (audio("pcm_s24le", 4, bitrate=4_608_000), "eac3", 4, 640_000),
        (audio("flac", 2, bitrate=1_000_000), "aac", 2, 192_000),
        (audio("pcm_s16le", 1, bitrate=768_000), "aac", 1, 96_000),
    ],
)
def test_generated_fallback_matrix(source, codec, channels, bitrate):
    plan = plan_audio(snapshot(source))
    assert (plan.action, plan.codec, plan.channels, plan.bitrate) == (
        "transcode",
        codec,
        channels,
        bitrate,
    )


@pytest.mark.parametrize(
    "stream",
    [
        audio("eac3", 6, default=1),
        audio("ac3", 6, default=1),
        audio("aac", 6, default=1),
        audio("aac", 2, default=1),
    ],
)
def test_existing_compatible_streams_are_kept_without_unnecessary_duplication(stream):
    plan = plan_audio(snapshot(stream))
    assert plan.action == "none"
    assert plan.default_ordinal == 0


def test_existing_eac3_5_1_prevents_truehd_fallback_and_becomes_default():
    plan = plan_audio(snapshot(audio("truehd", 8, default=1), audio("eac3", 6)))
    assert plan.action == "none"
    assert plan.default_ordinal == 1


def test_ac3_5_1_does_not_block_eac3_generated_from_truehd():
    plan = plan_audio(snapshot(audio("truehd", 8), audio("ac3", 6, default=1)))
    assert (plan.action, plan.codec, plan.channels) == ("transcode", "eac3", 6)
    assert plan.default_ordinal == 2


def test_best_same_class_source_prefers_truehd_over_dts_and_aac():
    plan = plan_audio(
        snapshot(
            audio("aac", 8, bitrate=768_000),
            audio("dts", 6, bitrate=1_509_000),
            audio("truehd", 8, bitrate=4_000_000),
        )
    )
    assert plan.source_ordinal == 2


def test_surround_source_outranks_higher_fidelity_stereo_without_upmixing():
    plan = plan_audio(snapshot(audio("truehd", 2), audio("dts", 6)))
    assert plan.source_ordinal == 1
    assert plan.channels == 6


def test_commentary_and_descriptive_tracks_are_never_fallback_sources():
    plan = plan_audio(
        snapshot(
            audio("truehd", 8, title="Director commentary", default=1),
            audio("dts", 6, title="Main feature"),
        )
    )
    assert plan.source_ordinal == 1


def test_custom_fallback_language_is_applied_to_missing_language():
    plan = plan_audio(snapshot(audio("flac", 2, language="und")), fallback_language="spa")
    assert plan.language == "spa"
    assert plan.label == "Spanish AAC 2.0"


def test_no_audio_cannot_manufacture_a_fallback():
    plan = plan_audio(snapshot())
    assert plan.action == "none"
    assert plan.default_ordinal is None
    assert plan.reason == "no eligible conversion source"


def test_three_to_five_channel_command_does_not_force_a_different_layout():
    plan = build_plan(snapshot(audio("flac", 5, bitrate=448_000)))
    command = plan.normalized_commands[-1]
    assert "-c:a:1" in command
    assert "-b:a:1" in command
    assert "-ac:a:1" not in command
