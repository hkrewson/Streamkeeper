import json
from types import SimpleNamespace

from streamkeeper.cli import main
from streamkeeper.executor import ParityGateError
from streamkeeper.models import ProbeSnapshot


def test_help(capsys):
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "Scan and plan Plex media compatibility work" in capsys.readouterr().out


def test_empty_path_reports_no_supported_files(tmp_path, capsys):
    assert main(["convert", "--path", str(tmp_path), "--dry-run"]) == 1
    assert "No supported media files" in capsys.readouterr().err


def test_scan_text_is_deterministic_and_forwards_deep_mode(tmp_path, monkeypatch, capsys):
    (tmp_path / "B.mkv").write_bytes(b"b")
    (tmp_path / "a.mkv").write_bytes(b"a")
    calls: list[tuple[str, bool]] = []

    def probe(path, *, deep=False):
        calls.append((path, deep))
        return ProbeSnapshot(path, "2026-01-01T00:00:00+00:00", {}, [])

    monkeypatch.setattr("streamkeeper.cli.probe_file", probe)
    monkeypatch.setattr("streamkeeper.cli.findings_for", lambda _snapshot: [])

    assert main(["scan", "--path", str(tmp_path), "--deep"]) == 0
    output = capsys.readouterr().out.splitlines()
    assert output == ["a.mkv: compatible", "B.mkv: compatible"]
    assert [deep for _path, deep in calls] == [True, True]


def test_scan_json_records_per_file_operating_system_failure(tmp_path, monkeypatch, capsys):
    media = tmp_path / "Broken.mkv"
    media.write_bytes(b"broken")
    monkeypatch.setattr("streamkeeper.cli.probe_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("share disconnected")))

    assert main(["scan", "--path", str(media), "--format", "json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["asset"]["relative_path"] == "Broken.mkv"
    assert payload[0]["error"] == "share disconnected"


def test_empty_scan_returns_nonzero_and_valid_json(tmp_path, capsys):
    assert main(["scan", "--path", str(tmp_path), "--format", "json"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []
    assert "No supported media files" in captured.err


def test_plan_json_and_non_dry_conversion_gate(tmp_path, monkeypatch, capsys):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"media")
    plan = SimpleNamespace(to_dict=lambda: {"source_path": str(media), "video_action": "copy"})
    monkeypatch.setattr("streamkeeper.cli.make_plan", lambda *_args: plan)

    assert main(["plan", "--path", str(media), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["video_action"] == "copy"

    def locked(_plan):
        raise ParityGateError("conversion remains locked")

    monkeypatch.setattr("streamkeeper.cli.execute", locked)
    assert main(["convert", "--path", str(media)]) == 2
    assert "conversion remains locked" in capsys.readouterr().err


def test_compare_scans_returns_match_or_review_status(tmp_path, capsys):
    scan = [{
        "asset": {
            "path": "/one/Movie.mkv", "relative_path": "Movie.mkv", "title": "Movie",
            "media_kind": "movie", "extra_type": None,
        },
        "probe": {"streams": [{"codec_type": "video", "codec_name": "hevc", "tags": {}}]},
        "findings": [],
    }]
    reference = tmp_path / "reference.json"
    candidate = tmp_path / "candidate.json"
    reference.write_text(json.dumps(scan), encoding="utf-8")
    candidate.write_text(json.dumps(scan), encoding="utf-8")

    assert main([
        "compare-scans", "--reference", str(reference), "--candidate", str(candidate),
    ]) == 0
    assert "Result: match" in capsys.readouterr().out

    scan[0]["findings"] = [{"rule_id": "video.codec"}]
    candidate.write_text(json.dumps(scan), encoding="utf-8")
    assert main([
        "compare-scans", "--reference", str(reference), "--candidate", str(candidate), "--format", "json",
    ]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["changed_files"] == 1
