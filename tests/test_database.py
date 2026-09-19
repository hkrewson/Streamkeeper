from pathlib import Path

from streamkeeper.database import Database
from streamkeeper.discovery import DiscoveryExclusion
from streamkeeper.models import CompatibilityFinding, LibraryType, MediaAsset, ProbeSnapshot, ScanRun


def test_findings_persist_then_resolve(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    scan_one = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    asset = MediaAsset(str(tmp_path / "Movie.mkv"), "Movie.mkv", LibraryType.MOVIE, 1, 1, "movie", "Movie")
    probe = ProbeSnapshot(asset.path, "2026-01-01T00:00:00Z", {}, [])
    finding = CompatibilityFinding("audio.missing", "audio", "warning", "No audio", "Missing")
    db.save_asset(library_id, asset, probe, [finding], scan_one)
    assert db.finding_counts()["open"] == 1

    scan_two = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    asset_id = db.save_asset(library_id, asset, probe, [], scan_two)
    db.resolve_absent_findings(asset_id, set())
    assert db.finding_counts()["open"] == 0
    assert db.finding_counts()["resolved"] == 1


def test_ignored_finding_stays_ignored_while_present_then_resolves_and_reopens(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    asset = MediaAsset(str(tmp_path / "Movie.mkv"), "Movie.mkv", LibraryType.MOVIE, 1, 1, "movie", "Movie")
    probe = ProbeSnapshot(asset.path, "2026-01-01T00:00:00Z", {}, [])
    finding = CompatibilityFinding("video.codec", "video", "action", "Convert video", "Unsupported")

    first = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    asset_id = db.save_asset(library_id, asset, probe, [finding], first)
    finding_id = db.findings("open")[0]["id"]
    db.set_finding_status(finding_id, "ignored")

    second = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    db.save_asset(library_id, asset, probe, [finding], second)
    db.resolve_absent_findings(asset_id, {finding.rule_id})
    assert db.findings("ignored")[0]["id"] == finding_id

    third = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    db.save_asset(library_id, asset, probe, [], third)
    db.resolve_absent_findings(asset_id, set())
    assert db.findings("resolved")[0]["id"] == finding_id

    fourth = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    db.save_asset(library_id, asset, probe, [finding], fourth)
    assert db.findings("open")[0]["id"] == finding_id


def test_ignored_finding_resolves_when_asset_disappears(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    asset = MediaAsset(str(tmp_path / "Removed.mkv"), "Removed.mkv", LibraryType.MOVIE, 1, 1, "movie", "Removed")
    probe = ProbeSnapshot(asset.path, "2026-01-01T00:00:00Z", {}, [])
    finding = CompatibilityFinding("video.codec", "video", "action", "Convert video", "Unsupported")
    first = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    db.save_asset(library_id, asset, probe, [finding], first)
    db.set_finding_status(db.findings("open")[0]["id"], "ignored")

    second = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    assert db.resolve_unseen_asset_findings(library_id, second) == 1
    assert len(db.findings("resolved")) == 1
    assert db.findings("ignored") == []


def test_settings_round_trip(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    assert db.settings()["time_zone"] == "local"
    db.set_settings({"network_ceiling_bps": 850_000_000, "fallback_language": "fra", "time_zone": "America/Chicago"})
    assert db.settings()["network_ceiling_bps"] == 850_000_000
    assert db.settings()["fallback_language"] == "fra"
    assert db.settings()["time_zone"] == "America/Chicago"
    assert "@eaDir" in db.settings()["excluded_directories"]


def test_library_exclusions_round_trip_and_scan_exclusions_are_persisted(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    library = db.update_library(
        library_id,
        excluded_directories=["Samples"],
        excluded_files=["*-workprint.mkv"],
    )
    assert library["excluded_directories"] == ["Samples"]
    assert db.list_libraries()[0]["excluded_files"] == ["*-workprint.mkv"]

    scan_id = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    db.save_scan_exclusions(
        scan_id,
        [DiscoveryExclusion("Samples", "directory", "Directory pattern", "Samples")],
    )
    assert db.scan_exclusions(scan_id)[0]["relative_path"] == "Samples"


def test_create_scan_if_idle_deduplicates_matching_active_work(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    normal = ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False)

    first_id, first_created = db.create_scan_if_idle(normal)
    duplicate_id, duplicate_created = db.create_scan_if_idle(normal)
    deep_id, deep_created = db.create_scan_if_idle(
        ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, True)
    )

    assert first_created is True
    assert duplicate_created is False
    assert duplicate_id == first_id
    assert deep_created is True
    assert deep_id != first_id

    db.update_scan(first_id, status="completed")
    replacement_id, replacement_created = db.create_scan_if_idle(normal)
    assert replacement_created is True
    assert replacement_id not in {first_id, deep_id}


def test_create_scan_if_idle_uses_path_for_unregistered_scan(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    first = ScanRun(None, None, str(tmp_path / "one"), LibraryType.MIXED, False)
    second = ScanRun(None, None, str(tmp_path / "two"), LibraryType.MIXED, False)

    first_id, _ = db.create_scan_if_idle(first)
    duplicate_id, duplicate_created = db.create_scan_if_idle(first)
    second_id, second_created = db.create_scan_if_idle(second)

    assert duplicate_id == first_id
    assert duplicate_created is False
    assert second_created is True
    assert second_id != first_id


def test_scan_recovery_preserves_history_and_is_idempotent(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    queued_id = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    running_id = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, True))
    db.update_scan(running_id, status="running", phase="probing", started_at="2026-01-01T00:00:00Z")

    recovered = db.recover_scan_tasks()
    recovered_ids = [scan["id"] for scan in recovered]
    replacement_ids = [scan_id for scan_id in recovered_ids if scan_id != queued_id]

    assert queued_id in recovered_ids
    assert len(replacement_ids) == 1
    interrupted = db.scan(running_id)
    assert interrupted["status"] == "interrupted"
    assert f"replacement queued as #{replacement_ids[0]}" in interrupted["message"]
    assert db.scan(replacement_ids[0])["message"] == f"Recovered from interrupted scan #{running_id}"

    recovered_again = db.recover_scan_tasks()
    assert [scan["id"] for scan in recovered_again] == recovered_ids


def test_cached_probe_requires_matching_file_identity_and_success(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    scan_id = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    asset = MediaAsset(str(tmp_path / "Movie.mkv"), "Movie.mkv", LibraryType.MOVIE, 10, 20, "movie", "Movie")
    snapshot = ProbeSnapshot(asset.path, "2026-01-01T00:00:00Z", {"duration": "60"}, [])
    db.save_asset(library_id, asset, snapshot, [], scan_id)

    assert db.cached_probe(library_id, asset) == snapshot
    changed = MediaAsset(asset.path, asset.relative_path, asset.library_type, 11, 21, asset.media_kind, asset.title)
    assert db.cached_probe(library_id, changed) is None

    failed_asset = MediaAsset(str(tmp_path / "Broken.mkv"), "Broken.mkv", LibraryType.MOVIE, 1, 2, "movie", "Broken")
    failed = ProbeSnapshot(failed_asset.path, "2026-01-01T00:00:00Z", {}, [], error="bad file")
    db.save_asset(library_id, failed_asset, failed, [], scan_id)
    assert db.cached_probe(library_id, failed_asset) is None


def test_successful_scan_hides_missing_assets_but_keeps_resolved_history(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    first_scan = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    kept = MediaAsset(str(tmp_path / "Kept.mkv"), "Kept.mkv", LibraryType.MOVIE, 1, 1, "movie", "Kept")
    removed = MediaAsset(str(tmp_path / "Removed.mkv"), "Removed.mkv", LibraryType.MOVIE, 1, 1, "movie", "Removed")
    snapshot = ProbeSnapshot(kept.path, "2026-01-01T00:00:00Z", {}, [])
    finding = CompatibilityFinding("video.codec", "video", "action", "Convert video", "Unsupported")
    db.save_asset(library_id, kept, snapshot, [], first_scan)
    db.save_asset(library_id, removed, snapshot, [finding], first_scan)

    second_scan = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    db.save_asset(library_id, kept, snapshot, [], second_scan, record_probe=False)
    removed_count = db.resolve_unseen_asset_findings(library_id, second_scan)

    assert removed_count == 1
    assert [asset["title"] for asset in db.assets()] == ["Kept"]
    assert {asset["title"] for asset in db.assets(include_missing=True)} == {"Kept", "Removed"}
    resolved = db.findings("resolved")
    assert len(resolved) == 1
    assert resolved[0]["asset_title"] == "Removed"


def test_retention_prunes_old_history_but_keeps_active_scans_and_latest_probe(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    old_completed = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False, status="completed"))
    old_running = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False, status="running"))
    current_scan = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False, status="completed"))
    asset = MediaAsset(str(tmp_path / "Movie.mkv"), "Movie.mkv", LibraryType.MOVIE, 1, 1, "movie", "Movie")
    old_probe = ProbeSnapshot(asset.path, "2020-01-01T00:00:00+00:00", {}, [])
    current_probe = ProbeSnapshot(asset.path, "2026-01-01T00:00:00+00:00", {}, [])
    db.save_asset(library_id, asset, old_probe, [], old_completed)
    db.save_asset(library_id, asset, current_probe, [], current_scan)
    with db.connect() as connection:
        connection.execute(
            "UPDATE scans SET created_at='2020-01-01T00:00:00+00:00' WHERE id IN (?,?)",
            (old_completed, old_running),
        )

    result = db.apply_retention(30)

    assert result == {"scans": 1, "probes": 1}
    assert db.scan(old_completed) is None
    assert db.scan(old_running)["status"] == "running"
    assert db.scan(current_scan) is not None
    assert db.probe(next(asset["id"] for asset in db.assets() if asset["title"] == "Movie"))["captured_at"] == current_probe.captured_at


def test_report_groups_video_and_network_measurements(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    db.set_settings({"network_ceiling_bps": 100_000_000})
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    scan_id = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    cases = [
        ("Comfortable", 79_000_000, "h264", 1920, 1080),
        ("Near", 80_000_000, "hevc", 3840, 2160),
        ("Over", 101_000_000, "hevc", 3840, 2160),
        ("Unknown", None, "mpeg2video", 720, 480),
    ]
    for index, (title, peak, codec, width, height) in enumerate(cases):
        path = str(tmp_path / f"{title}.mkv")
        asset = MediaAsset(path, f"{title}.mkv", LibraryType.MOVIE, index + 1, index + 1, "movie", title)
        snapshot = ProbeSnapshot(
            path, "2026-01-01T00:00:00+00:00", {},
            [{"codec_type": "video", "codec_name": codec, "width": width, "height": height}],
            peak_bitrate_bps=peak,
        )
        db.save_asset(library_id, asset, snapshot, [], scan_id)

    report = db.report()

    assert report["video_codecs"] == {"H264": 1, "HEVC": 2, "MPEG2VIDEO": 1}
    assert report["resolutions"] == {"1920×1080": 1, "3840×2160": 2, "720×480": 1}
    assert report["network"] == {"comfortable": 1, "near_limit": 1, "over_limit": 1, "not_measured": 1}
    assert [item["title"] for item in report["network_assets"]] == ["Over", "Near", "Comfortable"]
