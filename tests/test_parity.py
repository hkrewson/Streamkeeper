import json
from pathlib import Path

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
