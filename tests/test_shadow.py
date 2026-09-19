import json
from pathlib import Path

import pytest

from streamkeeper.shadow import compare_scans, load_scan, normalize_scan, render_comparison


def row(
    relative_path: str,
    *,
    root: str = "/reference",
    video: str = "hevc",
    findings: tuple[str, ...] = (),
    captured_at: str = "2026-01-01T00:00:00Z",
):
    return {
        "asset": {
            "path": f"{root}/{relative_path}",
            "relative_path": relative_path,
            "title": Path(relative_path).stem,
            "library_type": "movie",
            "size_bytes": 100,
            "modified_ns": 123,
            "media_kind": "movie",
            "extra_type": None,
        },
        "probe": {
            "path": f"{root}/{relative_path}",
            "captured_at": captured_at,
            "format": {"duration": "60"},
            "streams": [
                {
                    "codec_type": "video", "codec_name": video, "profile": "Main 10",
                    "width": 3840, "height": 2160, "pix_fmt": "yuv420p10le",
                    "color_transfer": "smpte2084", "tags": {},
                },
                {
                    "codec_type": "audio", "codec_name": "eac3", "channels": 6,
                    "channel_layout": "5.1(side)", "tags": {"language": "eng"},
                },
            ],
            "peak_bitrate_bps": 80_000_000,
        },
        "findings": [{"rule_id": rule} for rule in findings],
    }


def test_shadow_comparison_ignores_roots_timestamps_and_probe_measurements():
    reference = [row("Movie/Movie.mkv")]
    candidate = [row("Movie/Movie.mkv", root="/candidate", captured_at="2026-09-19T12:00:00Z")]
    candidate[0]["asset"]["size_bytes"] = 999
    candidate[0]["asset"]["modified_ns"] = 456
    candidate[0]["probe"]["peak_bitrate_bps"] = 81_000_000

    result = compare_scans(reference, candidate)

    assert result["matches"] is True
    assert result["summary"]["changed_files"] == 0
    assert render_comparison(result).endswith("Result: match")


def test_shadow_comparison_reports_missing_added_and_changed_classification():
    reference = [row("Missing.mkv"), row("Changed.mkv", findings=("video.codec",))]
    candidate = [row("Added.mkv"), row("Changed.mkv", video="h264")]

    result = compare_scans(reference, candidate)

    assert result["matches"] is False
    assert result["missing"] == ["Missing.mkv"]
    assert result["added"] == ["Added.mkv"]
    assert set(result["changed"]["Changed.mkv"]) == {"video", "finding_rules"}
    rendered = render_comparison(result)
    assert "Result: review required" in rendered
    assert "Changed: Changed.mkv (video, finding_rules)" in rendered


def test_shadow_scan_rejects_duplicates_and_invalid_json(tmp_path: Path):
    with pytest.raises(ValueError, match="duplicate relative path"):
        normalize_scan([row("Movie.mkv"), row("Movie.mkv")])

    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid scan JSON"):
        load_scan(invalid)

    wrong_shape = tmp_path / "wrong.json"
    wrong_shape.write_text(json.dumps({"asset": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="must contain a list"):
        load_scan(wrong_shape)
