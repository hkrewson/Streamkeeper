import json
from pathlib import Path

import pytest

from streamkeeper.models import ProbeSnapshot
from streamkeeper.parity import (
    compare_command_vectors,
    compare_decisions,
    normalize_python_plan,
    parse_legacy_dry_run,
    parse_legacy_evidence_commands,
)
from streamkeeper.planner import build_plan


LEGACY_DTS = """
Analyze: /media/Fixture.mkv
  Video: h264, yuv420p, SDR
  Video action: copy (SDR H.264 is already Apple-compatible)
  Existing audio tracks: 1 (all retained)
  Existing subtitle tracks: 6
  New audio: Russian E-AC3 5.1 at 640 kbps, sourced from track 1
  Default audio: Russian E-AC3 5.1 (new)
  Backup name: Fixture Original.mkv
  Output name: Fixture.mkv
  Evidence file: Fixture.conversion.txt
  NFO update: none found (TXT evidence only)
  Label 1: Russian DTS 5.1
  Label 2: Russian E-AC3 5.1
  DRY RUN: no encode, remux, or rename performed.
"""

FIXTURES = Path(__file__).parent / "fixtures"


def load_snapshot(name: str) -> ProbeSnapshot:
    data = json.loads((FIXTURES / name).read_text())
    return ProbeSnapshot(**data)


def test_realistic_dts_dry_run_normalizes_to_python_plan():
    snapshot = ProbeSnapshot(
        "/media/Fixture.mkv", "2026-01-01T00:00:00Z", {},
        [
            {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "color_transfer": "bt709", "disposition": {}},
            {"codec_type": "audio", "codec_name": "dts", "channels": 6, "bit_rate": "1509000", "tags": {"language": "rus"}, "disposition": {"default": 1}},
            *({"codec_type": "subtitle", "codec_name": "subrip", "disposition": {}} for _ in range(6)),
        ],
    )
    reference = parse_legacy_dry_run(LEGACY_DTS)
    candidate = normalize_python_plan(build_plan(snapshot))
    assert compare_decisions(reference, candidate) == {}


def test_sanitized_real_dts_fixture_matches_frozen_shell_output():
    snapshot = load_snapshot("real_dts_5_1.json")
    legacy_output = (FIXTURES / "real_dts_5_1.legacy.txt").read_text()
    reference = parse_legacy_dry_run(legacy_output)
    candidate = normalize_python_plan(build_plan(snapshot))
    assert compare_decisions(reference, candidate) == {}


def test_sanitized_real_ac3_fixture_with_parenthesized_nfo_matches(tmp_path: Path):
    snapshot = load_snapshot("real_ac3_5_1.json")
    source = tmp_path / "Fixture (1969).mkv"
    snapshot.path = str(source)
    (tmp_path / "Fixture (1969).nfo").write_text("<movie><title>Fixture</title></movie>")
    legacy_output = (FIXTURES / "real_ac3_5_1.legacy.txt").read_text()
    reference = parse_legacy_dry_run(legacy_output)
    candidate = normalize_python_plan(build_plan(snapshot))
    assert reference.nfo_name == "Fixture (1969).nfo"
    assert compare_decisions(reference, candidate) == {}


def test_sanitized_real_dolby_vision_truehd_bitmap_fixture_matches(tmp_path: Path):
    snapshot = load_snapshot("real_dovi7_truehd_pgs.json")
    source = tmp_path / "Fixture (2023).mkv"
    snapshot.path = str(source)
    (tmp_path / "Fixture (2023).nfo").write_text("<movie><title>Fixture</title></movie>")
    reference = parse_legacy_dry_run(
        (FIXTURES / "real_dovi7_truehd_pgs.legacy.txt").read_text()
    )
    candidate = normalize_python_plan(build_plan(snapshot))
    assert compare_decisions(reference, candidate) == {}
    remux = build_plan(snapshot).normalized_commands[-1]
    assert remux[remux.index("-bsf:v:0") + 1].endswith(",dovi_rpu")


def test_sanitized_real_hdr10plus_fixture_records_approved_policy_correction(tmp_path: Path):
    snapshot = load_snapshot("real_hdr10plus_eac3.json")
    source = tmp_path / "Fixture (2021).mkv"
    snapshot.path = str(source)
    (tmp_path / "Fixture (2021).nfo").write_text("<movie><title>Fixture</title></movie>")
    reference = parse_legacy_dry_run(
        (FIXTURES / "real_hdr10plus_eac3.legacy.txt").read_text()
    )
    plan = build_plan(snapshot)
    candidate = normalize_python_plan(plan)

    assert compare_decisions(reference, candidate) == {
        "video_action": {"reference": "copy", "candidate": "strip_hdr10plus"},
        "video_reason": {
            "reference": "already Apple-compatible",
            "candidate": "retain HDR10 base and remove HDR10+ metadata",
        },
        "hdr_mode": {"reference": "HDR10", "candidate": "HDR10 + HDR10+"},
    }
    dovi_command = next(command for command in plan.normalized_commands if command[0] == "dovi_tool")
    assert dovi_command[1:3] == ["--drop-hdr10plus", "convert"]
    assert plan.required_tools == ["ffmpeg", "ffprobe", "dovi_tool"]
    remux = plan.normalized_commands[-1]
    assert remux[remux.index("-r:v:0") + 1] == "24000/1001"
    timestamps = remux[remux.index("-bsf:v:0") + 1]
    assert timestamps == (
        "setts=pts=N*1001/24000/TB:dts=N*1001/24000/TB:"
        "duration=1001/24000/TB"
    )


def test_comparator_reports_structured_fields():
    reference = parse_legacy_dry_run(LEGACY_DTS)
    candidate = parse_legacy_dry_run(LEGACY_DTS.replace("Video action: copy", "Video action: transcode_hevc"))
    assert compare_decisions(reference, candidate) == {
        "video_action": {"reference": "copy", "candidate": "transcode_hevc"}
    }


def test_flac_command_matches_normalized_shell_evidence():
    snapshot = ProbeSnapshot(
        "/media/Fixture.mkv", "2026-01-01T00:00:00Z", {},
        [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "color_transfer": "bt709", "disposition": {}},
            {"index": 1, "codec_type": "audio", "codec_name": "flac", "channels": 2, "tags": {"language": "eng"}, "disposition": {"default": 1}},
        ],
    )
    legacy_evidence = r"""
Commands
--------
  Command: ffmpeg -hide_banner -nostdin -y -i /case/Fixture.mkv -map 0 -map 0:a:0 -map_metadata 0 -map_chapters 0 -c copy -disposition:v:0 0 -c:a:1 aac -b:a:1 192000 -ac:a:1 2 -metadata:s:a:0 title=English\ FLAC\ 2.0 -metadata:s:a:0 language=eng -disposition:a:0 0 -metadata:s:a:1 title=English\ AAC\ 2.0 -metadata:s:a:1 language=eng -disposition:a:1 default -max_muxing_queue_size 4096 -f matroska /case/.Fixture.convert.4T0yy8
"""
    reference = parse_legacy_evidence_commands(legacy_evidence)
    candidate = build_plan(snapshot).normalized_commands
    assert compare_command_vectors(
        reference,
        candidate,
        reference_source="/case/Fixture.mkv",
        candidate_source="/media/Fixture.mkv",
    ) == {}


@pytest.mark.parametrize(
    ("source_name", "streams", "legacy_output"),
    [
        (
            "MPEG2 PCM Stereo.mkv",
            [
                {"index": 0, "codec_type": "video", "codec_name": "mpeg2video", "pix_fmt": "yuv420p", "disposition": {}},
                {"index": 1, "codec_type": "audio", "codec_name": "pcm_s16le", "channels": 2, "channel_layout": "stereo", "tags": {"language": "eng"}, "disposition": {"default": 1}},
            ],
            """
  Video: mpeg2video, yuv420p, SDR
  Video action: transcode_hevc (transcode mpeg2video to HEVC)
  Existing audio tracks: 1 (all retained)
  New audio: English AAC 2.0 at 192 kbps, sourced from track 1
  Default audio: English AAC 2.0 (new)
  Backup name: MPEG2 PCM Stereo Original.mkv
  Output name: MPEG2 PCM Stereo.mkv
  Evidence file: MPEG2 PCM Stereo.conversion.txt
  NFO update: none found (TXT evidence only)
  Label 1: English PCM 2.0
  Label 2: English AAC 2.0
""",
        ),
        (
            "Main and Commentary.mkv",
            [
                {"index": 0, "codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "color_transfer": "bt709", "disposition": {}},
                {"index": 1, "codec_type": "audio", "codec_name": "flac", "channels": 6, "channel_layout": "5.1", "tags": {"language": "eng", "title": "Main Audio"}, "disposition": {}},
                {"index": 2, "codec_type": "audio", "codec_name": "flac", "channels": 2, "channel_layout": "stereo", "tags": {"language": "eng", "title": "Director Commentary"}, "disposition": {"default": 1}},
            ],
            """
  Video: h264, yuv420p, SDR
  Video action: copy (SDR H.264 is already Apple-compatible)
  Existing audio tracks: 2 (all retained)
  New audio: English E-AC3 5.1 at 640 kbps, sourced from track 1
  Default audio: English E-AC3 5.1 (new)
  Backup name: Main and Commentary Original.mkv
  Output name: Main and Commentary.mkv
  Evidence file: Main and Commentary.conversion.txt
  NFO update: none found (TXT evidence only)
  Label 1: English FLAC 5.1
  Label 2: English FLAC 2.0 - Director Commentary
  Label 3: English E-AC3 5.1
""",
        ),
        (
            "Mono FLAC.mkv",
            [
                {"index": 0, "codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "color_transfer": "bt709", "disposition": {}},
                {"index": 1, "codec_type": "audio", "codec_name": "flac", "channels": 1, "channel_layout": "mono", "tags": {"language": "und"}, "disposition": {"default": 1}},
            ],
            """
  Video: h264, yuv420p, SDR
  Video action: copy (SDR H.264 is already Apple-compatible)
  Existing audio tracks: 1 (all retained)
  New audio: English AAC 1.0 at 96 kbps, sourced from track 1
  Default audio: English AAC 1.0 (new)
  Backup name: Mono FLAC Original.mkv
  Output name: Mono FLAC.mkv
  Evidence file: Mono FLAC.conversion.txt
  NFO update: none found (TXT evidence only)
  Label 1: English FLAC 1.0
  Label 2: English AAC 1.0
""",
        ),
        (
            "PCM Quad.mkv",
            [
                {"index": 0, "codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "color_transfer": "bt709", "disposition": {}},
                {"index": 1, "codec_type": "audio", "codec_name": "pcm_s16le", "channels": 4, "channel_layout": "quad", "tags": {"language": "eng"}, "disposition": {"default": 1}},
            ],
            """
  Video: h264, yuv420p, SDR
  Video action: copy (SDR H.264 is already Apple-compatible)
  Existing audio tracks: 1 (all retained)
  New audio: English E-AC3 4.0 at 640 kbps, sourced from track 1
  Default audio: English E-AC3 4.0 (new)
  Backup name: PCM Quad Original.mkv
  Output name: PCM Quad.mkv
  Evidence file: PCM Quad.conversion.txt
  NFO update: none found (TXT evidence only)
  Label 1: English PCM 4.0
  Label 2: English E-AC3 4.0
""",
        ),
        (
            "Silent.mkv",
            [
                {"index": 0, "codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "color_transfer": "bt709", "disposition": {}},
            ],
            """
  Video: h264, yuv420p, SDR
  Video action: copy (SDR H.264 is already Apple-compatible)
  Existing audio tracks: 0 (all retained)
  New audio: none (no eligible conversion source)
  Default audio: none
  Backup name: Silent Original.mkv
  Output name: Silent.mkv
  Evidence file: Silent.conversion.txt
  NFO update: none found (TXT evidence only)
""",
        ),
        (
            "ASS Subtitle.mkv",
            [
                {"index": 0, "codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "color_transfer": "bt709", "disposition": {}},
                {"index": 1, "codec_type": "audio", "codec_name": "ac3", "channels": 2, "channel_layout": "stereo", "tags": {"language": "eng"}, "disposition": {"default": 1}},
                {"index": 2, "codec_type": "subtitle", "codec_name": "ass", "tags": {"language": "eng"}, "disposition": {}},
            ],
            """
  Video: h264, yuv420p, SDR
  Video action: copy (SDR H.264 is already Apple-compatible)
  Existing audio tracks: 1 (all retained)
  Existing subtitle tracks: 1
  New subtitle fallbacks: 1 SRT track(s) from ASS/SSA
  New audio: none (no eligible conversion source)
  Default audio: English AC3 2.0
  Backup name: ASS Subtitle Original.mkv
  Output name: ASS Subtitle.mkv
  Evidence file: ASS Subtitle.conversion.txt
  NFO update: none found (TXT evidence only)
  Label 1: English AC3 2.0
""",
        ),
        (
            "MOV TEXT.mp4",
            [
                {"index": 0, "codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "color_transfer": "bt709", "disposition": {"default": 1}},
                {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2, "channel_layout": "stereo", "tags": {"language": "eng"}, "disposition": {"default": 1}},
                {"index": 2, "codec_type": "subtitle", "codec_name": "mov_text", "tags": {"language": "eng"}, "disposition": {"default": 1}},
            ],
            """
  Video: h264, yuv420p, SDR
  Video action: copy (SDR H.264 is already Apple-compatible)
  Existing audio tracks: 1 (all retained)
  Existing subtitle tracks: 1
  Subtitle conversion: 1 MOV_TEXT track(s) converted to SRT for MKV
  New audio: none (matching compatibility stream already exists)
  Default audio: English AAC 2.0
  Backup name: MOV TEXT Original.mp4
  Output name: MOV TEXT.mkv
  Evidence file: MOV TEXT.conversion.txt
  NFO update: none found (TXT evidence only)
  Label 1: English AAC 2.0
""",
        ),
    ],
)
def test_generated_policy_cases_match_frozen_shell_dry_runs(
    source_name: str,
    streams: list[dict],
    legacy_output: str,
):
    snapshot = ProbeSnapshot(f"/media/{source_name}", "2026-01-01T00:00:00Z", {}, streams)
    reference = parse_legacy_dry_run(legacy_output)
    candidate = normalize_python_plan(build_plan(snapshot))

    assert compare_decisions(reference, candidate) == {}


def test_generated_plex_extra_naming_matches_frozen_shell_dry_run():
    snapshot = ProbeSnapshot(
        "/media/Movie (2020)/Featurettes/Behind the Scenes.mkv",
        "2026-01-01T00:00:00Z",
        {},
        [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "color_transfer": "bt709", "disposition": {}},
            {"index": 1, "codec_type": "audio", "codec_name": "flac", "channels": 1, "channel_layout": "mono", "tags": {"language": "und"}, "disposition": {"default": 1}},
        ],
    )
    reference = parse_legacy_dry_run(
        """
  Video: h264, yuv420p, SDR
  Video action: copy (SDR H.264 is already Apple-compatible)
  Existing audio tracks: 1 (all retained)
  New audio: English AAC 1.0 at 96 kbps, sourced from track 1
  Default audio: English AAC 1.0 (new)
  Backup name: Behind the Scenes-featurette Original.mkv
  Output name: Behind the Scenes-featurette.mkv
  Evidence file: Behind the Scenes-featurette.conversion.txt
  NFO update: none found (TXT evidence only)
  Label 1: English FLAC 1.0
  Label 2: English AAC 1.0
"""
    )
    candidate = normalize_python_plan(
        build_plan(snapshot, media_kind="extra", extra_type="featurette")
    )

    assert compare_decisions(reference, candidate) == {}


def test_aac_7_1_generated_case_records_approved_audio_correction():
    snapshot = ProbeSnapshot(
        "/media/AAC 7.1.mkv",
        "2026-01-01T00:00:00Z",
        {},
        [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "color_transfer": "bt709", "disposition": {}},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 8, "channel_layout": "7.1", "tags": {"language": "eng"}, "disposition": {"default": 1}},
        ],
    )
    reference = parse_legacy_dry_run(
        """
  Video: h264, yuv420p, SDR
  Video action: copy (SDR H.264 is already Apple-compatible)
  Existing audio tracks: 1 (all retained)
  New audio: none (no eligible conversion source)
  Default audio: English AAC 7.1
  Backup name: AAC 7.1 Original.mkv
  Output name: AAC 7.1.mkv
  Evidence file: AAC 7.1.conversion.txt
  NFO update: none found (TXT evidence only)
  Label 1: English AAC 7.1
"""
    )
    candidate = normalize_python_plan(build_plan(snapshot))

    assert compare_decisions(reference, candidate) == {
        "new_audio_label": {"reference": None, "candidate": "English E-AC3 5.1"},
        "new_audio_bitrate_kbps": {"reference": None, "candidate": 640},
        "new_audio_source_track": {"reference": None, "candidate": 1},
        "new_audio_reason": {"reference": "no eligible conversion source", "candidate": None},
        "default_audio": {"reference": "English AAC 7.1", "candidate": "English E-AC3 5.1 (new)"},
        "audio_labels": {
            "reference": ["English AAC 7.1"],
            "candidate": ["English AAC 7.1", "English E-AC3 5.1"],
        },
    }
