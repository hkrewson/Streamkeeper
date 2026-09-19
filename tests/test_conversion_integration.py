from __future__ import annotations

import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from streamkeeper.planner import build_plan
from streamkeeper.probe import probe_file
from streamkeeper.validation import validate_copied_stream_hashes, validate_output


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg integration tools are unavailable",
)


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True, text=True)


def execute_plan_and_validate(source: Path, *, verify_hashes: bool = True):
    source_probe = probe_file(source)
    plan = build_plan(source_probe)
    for command in plan.normalized_commands:
        run(command)
    staged = Path(plan.normalized_commands[-1][-1])
    result = validate_output(source_probe, probe_file(staged), plan)
    assert result["passed"] is True, result["errors"]
    if verify_hashes:
        hashes = validate_copied_stream_hashes(source, staged, source_probe, plan)
        assert hashes["passed"] is True, hashes["errors"]
    assert source.is_file()
    assert staged.is_file()
    return plan


def test_planned_flac_fallback_command_produces_semantically_valid_output(tmp_path: Path):
    source = tmp_path / "Fixture.mkv"
    run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=1",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "flac", "-ac", "2",
            "-metadata:s:a:0", "language=eng",
            "-metadata:s:a:0", "title=Fixture FLAC",
            "-disposition:a:0", "default",
            "-f", "matroska", str(source),
        ]
    )

    plan = build_plan(probe_file(source))
    assert plan.audio.action == "transcode"
    assert plan.executable is False

    # Execute only the isolated, reviewed command vector. The parity gate still
    # prevents the CLI executor from touching a user library.
    execute_plan_and_validate(source)


def test_planned_pcm_5_1_fallback_produces_eac3_5_1_without_removing_pcm(tmp_path: Path):
    source = tmp_path / "Multichannel.mkv"
    run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=5.1:sample_rate=48000:d=1",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "pcm_s24le",
            "-metadata:s:a:0", "language=eng",
            "-disposition:a:0", "default",
            "-f", "matroska", str(source),
        ]
    )

    plan = execute_plan_and_validate(source)
    assert (plan.audio.codec, plan.audio.channels, plan.audio.bitrate) == (
        "eac3",
        6,
        640_000,
    )


def test_existing_eac3_becomes_default_without_altering_truehd_or_creating_duplicate(tmp_path: Path):
    source = tmp_path / "Existing-EAC3.mkv"
    run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=7.1:sample_rate=48000:d=1",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=5.1:sample_rate=48000:d=1",
            "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a:0", "truehd", "-c:a:1", "eac3", "-b:a:1", "640k", "-strict", "-2",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:0", "title=TrueHD Atmos 7.1",
            "-metadata:s:a:1", "language=eng", "-metadata:s:a:1", "title=E-AC3 5.1",
            "-disposition:a:0", "default", "-disposition:a:1", "0",
            "-f", "matroska", str(source),
        ]
    )

    plan = execute_plan_and_validate(source)
    assert plan.audio.action == "none"
    assert plan.audio.default_ordinal == 1
    output = probe_file(Path(plan.normalized_commands[-1][-1]))
    audio = [stream for stream in output.streams if stream.get("codec_type") == "audio"]
    assert [stream["codec_name"] for stream in audio] == ["truehd", "eac3"]
    assert [stream.get("disposition", {}).get("default") for stream in audio] == [0, 1]


def test_aac_7_1_is_preserved_while_eac3_5_1_fallback_is_added(tmp_path: Path):
    source = tmp_path / "AAC-7.1.mkv"
    run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=7.1:sample_rate=48000:d=1",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "512k",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:0", "title=AAC 7.1",
            "-disposition:a:0", "default",
            "-f", "matroska", str(source),
        ]
    )

    plan = execute_plan_and_validate(source)
    assert (plan.audio.action, plan.audio.codec, plan.audio.channels, plan.audio.bitrate) == (
        "transcode", "eac3", 6, 640_000,
    )
    output = probe_file(Path(plan.normalized_commands[-1][-1]))
    audio = [stream for stream in output.streams if stream.get("codec_type") == "audio"]
    assert [stream["codec_name"] for stream in audio] == ["aac", "eac3"]
    assert [stream.get("channels") for stream in audio] == [8, 6]
    assert [stream.get("disposition", {}).get("default") for stream in audio] == [0, 1]


def test_planned_ass_fallback_preserves_source_subtitle_and_attachment(tmp_path: Path):
    source = tmp_path / "Subtitles.mkv"
    subtitle = tmp_path / "signs.ass"
    cover = tmp_path / "cover.jpg"
    subtitle.write_text(
        """[Script Info]\nScriptType: v4.00+\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:00.00,0:00:00.80,Default,,0,0,0,,Fixture subtitle\n""",
        encoding="utf-8",
    )
    run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=navy:size=64x64:duration=1",
            "-frames:v", "1", "-update", "1", str(cover),
        ]
    )
    run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=1",
            "-i", str(subtitle),
            "-map", "0:v:0", "-map", "1:a:0", "-map", "2:s:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "2",
            "-c:s", "ass",
            "-metadata:s:a:0", "language=eng",
            "-metadata:s:s:0", "language=eng",
            "-metadata:s:s:0", "title=Signs",
            "-attach", str(cover),
            "-metadata:s:t:0", "filename=cover.jpg",
            "-metadata:s:t:0", "mimetype=image/jpeg",
            "-f", "matroska", str(source),
        ]
    )

    plan = execute_plan_and_validate(source)
    assert plan.subtitles.ass_srt_fallbacks == 1


def test_planned_mpeg2_video_transcodes_to_hevc_and_preserves_aac(tmp_path: Path):
    source = tmp_path / "MPEG2.mkv"
    run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=1",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "mpeg2video", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "2",
            "-metadata:s:a:0", "language=eng",
            "-f", "matroska", str(source),
        ]
    )

    plan = execute_plan_and_validate(source)
    assert plan.video_action == "transcode_hevc"


def test_planned_mov_text_subtitle_is_converted_to_srt_for_matroska(tmp_path: Path):
    source = tmp_path / "MOVTEXT.mp4"
    subtitle = tmp_path / "subtitle.srt"
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:00,800\nFixture subtitle\n",
        encoding="utf-8",
    )
    run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=1",
            "-i", str(subtitle),
            "-map", "0:v:0", "-map", "1:a:0", "-map", "2:s:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "2",
            "-c:s", "mov_text",
            "-metadata:s:a:0", "language=eng",
            "-metadata:s:s:0", "language=eng",
            "-f", "mp4", str(source),
        ]
    )

    plan = execute_plan_and_validate(source)
    assert plan.subtitles.mov_text_conversions == 1


def test_planned_remux_preserves_chapters(tmp_path: Path):
    base = tmp_path / "chapter-base.mkv"
    source = tmp_path / "Chapters.mkv"
    metadata = tmp_path / "chapters.ffmeta"
    metadata.write_text(
        ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=500\ntitle=Opening\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=500\nEND=1000\ntitle=Closing\n",
        encoding="utf-8",
    )
    run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=1",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "2",
            "-metadata:s:a:0", "language=eng",
            "-f", "matroska", str(base),
        ]
    )
    run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-i", str(base), "-i", str(metadata),
            "-map", "0", "-map_metadata", "0", "-map_chapters", "1",
            "-c", "copy", str(source),
        ]
    )

    assert len(probe_file(source).chapters) == 2
    execute_plan_and_validate(source)


@pytest.mark.parametrize(
    ("layout", "source_codec", "expected_codec", "expected_channels", "expected_bitrate"),
    [
        ("mono", "pcm_s24le", "aac", 1, 96_000),
        ("4.0", "flac", "eac3", 4, 640_000),
    ],
)
def test_planned_additional_channel_classes(
    tmp_path: Path,
    layout: str,
    source_codec: str,
    expected_codec: str,
    expected_channels: int,
    expected_bitrate: int,
):
    source = tmp_path / f"Audio-{expected_channels}.mkv"
    run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
            "-f", "lavfi", "-i", f"anullsrc=channel_layout={layout}:sample_rate=48000:d=1",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", source_codec,
            "-metadata:s:a:0", "language=eng",
            "-f", "matroska", str(source),
        ]
    )

    plan = execute_plan_and_validate(source)
    assert (plan.audio.codec, plan.audio.channels, plan.audio.bitrate) == (
        expected_codec,
        expected_channels,
        expected_bitrate,
    )


def test_planned_ts_conversion_omits_non_playback_data_stream(tmp_path: Path):
    source = tmp_path / "WithData.ts"
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"streamkeeper-data-stream-fixture" * 64)
    run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=1",
            "-f", "data", "-i", str(payload),
            "-map", "0:v:0", "-map", "1:a:0", "-map", "2:0",
            "-c:v", "mpeg2video", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "2",
            "-c:d", "copy",
            "-metadata:s:a:0", "language=eng",
            "-f", "mpegts", str(source),
        ]
    )

    source_probe = probe_file(source)
    assert any(stream.get("codec_type") == "data" for stream in source_probe.streams)
    plan = execute_plan_and_validate(source)
    assert any("non-playback data" in warning for warning in plan.warnings)
    output_probe = probe_file(Path(plan.normalized_commands[-1][-1]))
    assert not any(stream.get("codec_type") == "data" for stream in output_probe.streams)


@pytest.mark.parametrize(
    ("encoder", "layout", "expected_source_codec"),
    [
        ("truehd", "7.1", "truehd"),
        ("dca", "5.1", "dts"),
    ],
)
def test_planned_lossless_and_dts_sources_add_eac3_without_altering_original(
    tmp_path: Path,
    encoder: str,
    layout: str,
    expected_source_codec: str,
):
    source = tmp_path / f"{expected_source_codec}.mkv"
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
        "-f", "lavfi", "-i", f"anullsrc=channel_layout={layout}:sample_rate=48000:d=1",
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", encoder,
    ]
    if encoder == "dca":
        command += ["-b:a", "768k"]
    command += [
        "-strict", "-2",
        "-metadata:s:a:0", "language=eng",
        "-f", "matroska", str(source),
    ]
    run(command)

    source_probe = probe_file(source)
    assert source_probe.streams[1]["codec_name"] == expected_source_codec
    plan = execute_plan_and_validate(source)
    assert (plan.audio.codec, plan.audio.channels, plan.audio.bitrate) == (
        "eac3",
        6,
        640_000,
    )


@pytest.mark.parametrize(
    ("transfer", "expected_hdr_mode"),
    [("smpte2084", "HDR10"), ("arib-std-b67", "HLG")],
)
def test_planned_hdr_transcode_preserves_native_hdr_signaling(
    tmp_path: Path,
    transfer: str,
    expected_hdr_mode: str,
):
    source = tmp_path / f"{expected_hdr_mode}.mkv"
    run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=1",
            "-vf", "format=yuv420p10le",
            "-c:v", "libx265", "-preset", "ultrafast", "-crf", "28",
            "-x265-params", (
                "repeat-headers=1:hdr10=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc"
                ":master-display=G(13250,34500)B(7500,3000)R(34000,16000)"
                "WP(15635,16450)L(10000000,50):max-cll=1000,400"
                if transfer == "smpte2084"
                else "repeat-headers=1:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc"
            ),
            "-color_primaries", "bt2020",
            "-color_trc", transfer,
            "-colorspace", "bt2020nc",
            "-an", "-f", "matroska", str(source),
        ]
    )

    actual_probe = probe_file(source)
    assert actual_probe.streams[0]["color_transfer"] == transfer
    if transfer == "smpte2084":
        side_types = {
            item.get("side_data_type")
            for item in actual_probe.streams[0].get("side_data_list", [])
        }
        assert "Mastering display metadata" in side_types
        assert "Content light level metadata" in side_types
    planning_probe = deepcopy(actual_probe)
    # The local VP9 encoder cannot emit recoverable PQ/HLG transfer metadata.
    # Reclassify only the codec to exercise the incompatible-video branch while
    # retaining a real, verifiable HDR input and an actual HEVC re-encode.
    planning_probe.streams[0]["codec_name"] = "vp9"
    plan = build_plan(planning_probe)
    for command in plan.normalized_commands:
        run(command)
    staged = Path(plan.normalized_commands[-1][-1])
    validation = validate_output(planning_probe, probe_file(staged), plan)
    assert validation["passed"] is True, validation["errors"]
    assert plan.video_action == "transcode_hevc"
    assert plan.hdr_mode == expected_hdr_mode
