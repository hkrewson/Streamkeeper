from pathlib import Path

from fastapi.testclient import TestClient

from streamkeeper.web import create_app
from streamkeeper.models import CompatibilityFinding, LibraryType, MediaAsset, ProbeSnapshot, ScanRun


def test_all_surfaces_and_read_only_api(tmp_path: Path):
    app = create_app(tmp_path / "web.sqlite3", start_worker=False)
    with TestClient(app) as client:
        for path in ("/library", "/scans", "/findings", "/reports", "/settings"):
            response = client.get(path)
            assert response.status_code == 200
            assert "Streamkeeper" in response.text
        findings_page = client.get("/findings").text
        assert "Last scan" in findings_page
        assert "Last seen" not in findings_page
        settings_page = client.get("/settings").text
        assert 'form="settings-form"' in settings_page
        assert "settings-nav" not in settings_page
        assert "Administrator access" not in settings_page
        assert "Add library" in settings_page
        assert 'id="library-form"' in settings_page
        assert "Discovery exclusions" in settings_page
        assert 'name="excluded_directories"' in settings_page
        assert client.get("/health").json() == {"status": "ok"}
        assert client.post("/api/convert", json={}).status_code == 404
        assert client.get("/api/settings").status_code == 200
        assert client.put("/api/settings", json={"retention_days": 30}).json()["retention_days"] == 30
        assert client.put("/api/settings", json={"retention_days": "invalid"}).status_code == 422
        assert client.put("/api/settings", json={"network_ceiling_bps": 0}).status_code == 422
        assert client.put("/api/settings", json={"schedule": "hourly"}).status_code == 422
        assert client.put("/api/settings", json={"fallback_language": "english"}).status_code == 422
        assert client.put("/api/settings", json={"time_zone": "Central"}).status_code == 422
        normalized = client.put("/api/settings", json={"fallback_language": "FRA"})
        assert normalized.json()["fallback_language"] == "fra"
        configured = client.put("/api/settings", json={"time_zone": "America/Chicago"})
        assert configured.json()["time_zone"] == "America/Chicago"
        settings_page = client.get("/settings")
        assert 'data-time-zone="America/Chicago"' in settings_page.text
        assert '<option value="America/Chicago" selected>Central time</option>' in settings_page.text
        exclusions = client.put(
            "/api/settings",
            json={"excluded_directories": ["Samples", "Samples"], "excluded_files": "*-temp.mkv\n"},
        )
        assert exclusions.json()["excluded_directories"] == ["Samples"]
        assert exclusions.json()["excluded_files"] == ["*-temp.mkv"]
        report = client.get("/api/reports/compatibility.csv")
        assert report.status_code == 200
        assert report.text.startswith("title,relative_path")
        assert "Compatibility overview" in client.get("/reports?report_kind=compatibility").text
        assert "grouped by codec and resolution" in client.get("/reports?report_kind=video").text
        network = client.get("/reports?report_kind=network").text
        assert "Highest measured peaks" in network
        assert "Not measured" in network


def test_tool_health_api_reports_base_and_conditional_readiness(tmp_path: Path, monkeypatch):
    statuses = {
        "ffmpeg": {"name": "ffmpeg", "available": True, "path": "/tools/ffmpeg", "version": "8.0", "error": None},
        "ffprobe": {"name": "ffprobe", "available": True, "path": "/tools/ffprobe", "version": "8.0", "error": None},
        "dovi_tool": {"name": "dovi_tool", "available": False, "path": None, "version": None, "error": "not found"},
    }
    monkeypatch.setattr("streamkeeper.web.tool_status", lambda name: dict(statuses[name]))
    app = create_app(tmp_path / "web.sqlite3", start_worker=False)

    with TestClient(app) as client:
        response = client.get("/api/tools")
        assert response.status_code == 200
        health = response.json()
        assert health["scan_ready"] is True
        assert health["conversion_ready"] is True
        assert health["dolby_vision_ready"] is False
        assert health["tools"]["dovi_tool"]["role"] == "Dolby Vision normalization when required"
        settings = client.get("/settings")
        assert "8.0" in settings.text
        assert "Missing" in settings.text


def test_http_basic_authentication_and_public_health(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STREAMKEEPER_USER", "operator")
    monkeypatch.setenv("STREAMKEEPER_PASSWORD", "correct horse")
    app = create_app(tmp_path / "web.sqlite3", start_worker=False)

    with TestClient(app) as client:
        denied = client.get("/library")
        assert denied.status_code == 401
        assert denied.headers["www-authenticate"] == 'Basic realm="Streamkeeper"'
        assert client.get("/api/settings", auth=("operator", "wrong")).status_code == 401
        assert client.get("/library", auth=("operator", "correct horse")).status_code == 200
        assert client.get("/api/settings", auth=("operator", "correct horse")).status_code == 200
        assert client.get("/health").json() == {"status": "ok"}


def test_incomplete_authentication_configuration_fails_closed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STREAMKEEPER_USER", "operator")
    monkeypatch.delenv("STREAMKEEPER_PASSWORD", raising=False)
    app = create_app(tmp_path / "web.sqlite3", start_worker=False)

    with TestClient(app) as client:
        response = client.get("/library")
        assert response.status_code == 503
        assert response.json()["detail"] == "Authentication configuration is incomplete"
        assert client.get("/api/settings").status_code == 503
        health = client.get("/health")
        assert health.status_code == 503
        assert health.json()["detail"] == "Authentication configuration is incomplete"


def test_library_creation_validation(tmp_path: Path):
    app = create_app(tmp_path / "web.sqlite3", start_worker=False)
    with TestClient(app) as client:
        response = client.post("/api/libraries", json={"name": "Movies", "path": str(tmp_path), "library_type": "movie"})
        assert response.status_code == 201
        assert response.json()["library_type"] == "movie"
        library_id = response.json()["id"]
        updated = client.patch(f"/api/libraries/{library_id}", json={"name": "Films"})
        assert updated.json()["name"] == "Films"
        disabled = client.patch(f"/api/libraries/{library_id}", json={"enabled": 0})
        assert disabled.json()["enabled"] == 0
        patterns = client.patch(
            f"/api/libraries/{library_id}",
            json={"excluded_directories": ["Samples"], "excluded_files": ["*-workprint.mkv"]},
        )
        assert patterns.json()["excluded_directories"] == ["Samples"]
        assert patterns.json()["excluded_files"] == ["*-workprint.mkv"]
        settings_page = client.get("/settings")
        assert "Films" in settings_page.text
        assert "Disabled" in settings_page.text


def test_library_creation_rejects_missing_and_duplicate_paths(tmp_path: Path):
    app = create_app(tmp_path / "web.sqlite3", start_worker=False)
    with TestClient(app) as client:
        missing = client.post("/api/libraries", json={"name": "Movies", "path": "", "library_type": "movie"})
        assert missing.status_code == 422
        created = client.post("/api/libraries", json={"name": "Movies", "path": str(tmp_path), "library_type": "movie"})
        assert created.status_code == 201
        duplicate = client.post("/api/libraries", json={"name": "Again", "path": str(tmp_path), "library_type": "movie"})
        assert duplicate.status_code == 409


def test_scan_detail_exposes_file_failures(tmp_path: Path):
    app = create_app(tmp_path / "web.sqlite3", start_worker=False)
    db = app.state.database
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    scan_id = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False, status="completed_with_errors", failed_files=1))
    asset = MediaAsset(str(tmp_path / "Broken.mkv"), "Broken.mkv", LibraryType.MOVIE, 1, 1, "movie", "Broken")
    snapshot = ProbeSnapshot(asset.path, "2026-01-01T00:00:00+00:00", {}, [], error="invalid container")
    db.save_asset(library_id, asset, snapshot, [], scan_id)
    db.add_scan_event(scan_id, "Probe failed for Broken.mkv", "error")

    with TestClient(app) as client:
        detail = client.get(f"/api/scans/{scan_id}")
        assert detail.json()["failures"][0]["error"] == "invalid container"
        assert detail.json()["events"][0]["message"] == "Probe failed for Broken.mkv"
        page = client.get(f"/scans?selected={scan_id}")
        assert "Failed files" in page.text
        assert "Changes" in page.text
        assert "Metadata work" in page.text
        assert "Activity log" in page.text
        assert "Broken.mkv" in page.text
        assert "invalid container" in page.text
        assert 'data-local="datetime"' in page.text
        assert 'data-local="time"' in page.text
        assert " UTC</time>" in page.text


def test_completed_scan_snapshot_is_downloadable_in_cli_comparison_format(tmp_path: Path):
    app = create_app(tmp_path / "web.sqlite3", start_worker=False)
    db = app.state.database
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    scan_id = db.create_scan(
        ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False, status="completed", total_files=1)
    )
    asset = MediaAsset(str(tmp_path / "Film.mkv"), "Film.mkv", LibraryType.MOVIE, 1, 1, "movie", "Film")
    probe = ProbeSnapshot(asset.path, "2026-09-19T12:00:00+00:00", {}, [])
    db.save_asset(library_id, asset, probe, [], scan_id)

    with TestClient(app) as client:
        response = client.get(f"/api/scans/{scan_id}/snapshot.json")
        assert response.status_code == 200
        assert response.headers["content-disposition"] == f'attachment; filename="streamkeeper-scan-{scan_id}.json"'
        assert response.json()[0]["asset"]["relative_path"] == "Film.mkv"
        assert response.json()[0]["probe"]["captured_at"] == "2026-09-19T12:00:00+00:00"
        page = client.get(f"/scans?selected={scan_id}")
        assert "Export scan snapshot" in page.text


def test_library_scope_and_assessment_counts(tmp_path: Path):
    app = create_app(tmp_path / "web.sqlite3", start_worker=False)
    db = app.state.database
    movies_id = db.add_library("Movies", str(tmp_path / "movies"), "movie")
    tv_id = db.add_library("Television", str(tmp_path / "tv"), "tv")
    movie_scan = db.create_scan(ScanRun(None, movies_id, str(tmp_path / "movies"), LibraryType.MOVIE, False))
    tv_scan = db.create_scan(ScanRun(None, tv_id, str(tmp_path / "tv"), LibraryType.TV, False))
    movie = MediaAsset(str(tmp_path / "movies" / "Film.mkv"), "Film.mkv", LibraryType.MOVIE, 1, 1, "movie", "Film")
    episode = MediaAsset(str(tmp_path / "tv" / "Show S01E01.mkv"), "Show S01E01.mkv", LibraryType.TV, 1, 1, "episode", "Show episode")
    video = [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}]
    db.save_asset(movies_id, movie, ProbeSnapshot(movie.path, "2026-01-01T00:00:00+00:00", {}, video), [], movie_scan)
    db.save_asset(tv_id, episode, ProbeSnapshot(episode.path, "2026-01-01T00:00:00+00:00", {}, video), [], tv_scan)

    with TestClient(app) as client:
        scoped = client.get(f"/library?library_id={movies_id}")
        assert "Film" in scoped.text
        assert "Show episode" not in scoped.text
        assert '<strong>1</strong><span>All media</span>' in scoped.text
        filtered = client.get(f"/library?assessment=warning&library_id={movies_id}")
        assert "No media matches this filter" in filtered.text
        assert '<strong>1</strong><span>All media</span>' in filtered.text
        report = client.get(f"/reports?report_kind=video&library_id={movies_id}")
        assert "1 current media files" in report.text
        assert f"compatibility.csv?library_id={movies_id}" in report.text
        assert client.get(f"/api/reports/compatibility?library_id={movies_id}").json()["total"] == 1
        csv_report = client.get(f"/api/reports/compatibility.csv?library_id={movies_id}").text
        assert "Film.mkv" in csv_report
        assert "Show S01E01.mkv" not in csv_report
        assert client.get("/library?library_id=999999").status_code == 404
        assert client.get("/reports?library_id=999999").status_code == 404


def test_findings_scope_preserves_status_counts_and_api_filter(tmp_path: Path):
    app = create_app(tmp_path / "web.sqlite3", start_worker=False)
    db = app.state.database
    movies_id = db.add_library("Movies", str(tmp_path / "movies"), "movie")
    tv_id = db.add_library("Television", str(tmp_path / "tv"), "tv")
    movie_scan = db.create_scan(ScanRun(None, movies_id, str(tmp_path / "movies"), LibraryType.MOVIE, False))
    tv_scan = db.create_scan(ScanRun(None, tv_id, str(tmp_path / "tv"), LibraryType.TV, False))
    finding = CompatibilityFinding("audio.fallback", "audio", "action", "Audio fallback needed", "No compatible default")
    for library_id, scan_id, title in ((movies_id, movie_scan, "Film"), (tv_id, tv_scan, "Episode")):
        path = str(tmp_path / title / f"{title}.mkv")
        asset = MediaAsset(path, f"{title}.mkv", LibraryType.MIXED, 1, 1, "movie", title)
        db.save_asset(library_id, asset, ProbeSnapshot(path, "2026-01-01T00:00:00+00:00", {}, []), [finding], scan_id)

    with TestClient(app) as client:
        page = client.get(f"/findings?status_filter=open&library_id={movies_id}")
        assert "Film" in page.text
        assert "Episode" not in page.text
        assert "Open <span>1</span>" in page.text
        assert f"status_filter=ignored&library_id={movies_id}" in page.text
        scoped_api = client.get(f"/api/findings?status_filter=open&library_id={tv_id}").json()
        assert [item["asset_title"] for item in scoped_api] == ["Episode"]
        assert client.get("/findings?library_id=999999").status_code == 404


def test_finding_inspector_renders_human_readable_probe_and_plan_tabs(tmp_path: Path):
    app = create_app(tmp_path / "web.sqlite3", start_worker=False)
    db = app.state.database
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    scan_id = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    asset = MediaAsset(str(tmp_path / "Film.mkv"), "Film.mkv", LibraryType.MOVIE, 1, 1, "movie", "Film")
    snapshot = ProbeSnapshot(
        asset.path,
        "2026-09-19T12:00:00+00:00",
        {"format_long_name": "Matroska / WebM", "duration": "600", "size": str(2 * 1024**3)},
        [
            {"codec_type": "video", "codec_name": "h264", "codec_long_name": "H.264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p", "tags": {}},
            {"codec_type": "audio", "codec_name": "aac", "codec_long_name": "AAC", "channels": 2, "channel_layout": "stereo", "tags": {"language": "eng", "title": "English AAC 2.0"}, "disposition": {"default": 1}},
        ],
        peak_bitrate_bps=50_000_000,
        tool_version="ffprobe 8.0",
    )
    finding = CompatibilityFinding("test.review", "video", "warning", "Review media", "Review this fixture")
    db.save_asset(library_id, asset, snapshot, [finding], scan_id)

    with TestClient(app) as client:
        page = client.get("/findings")
        assert page.status_code == 200
        for label in ("Summary", "Probe", "Plan"):
            assert f">{label}</button>" in page.text
        assert "Matroska / WebM" in page.text
        assert "10.0 minutes" in page.text
        assert "50.0 Mbps" in page.text
        assert "Video 1" in page.text
        assert "Audio 2" in page.text
        assert "Keep existing video" in page.text
        assert "Use an existing compatible stream" in page.text
        assert "Raw probe data" in page.text
        assert "Raw dry-run plan" in page.text
        assert "View probe data" not in page.text
        assert "View dry-run plan" not in page.text


def test_failed_probe_inspector_explains_that_plan_is_unavailable(tmp_path: Path):
    app = create_app(tmp_path / "web.sqlite3", start_worker=False)
    db = app.state.database
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    scan_id = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    asset = MediaAsset(str(tmp_path / "Broken.mkv"), "Broken.mkv", LibraryType.MOVIE, 1, 1, "movie", "Broken")
    snapshot = ProbeSnapshot(asset.path, "2026-09-19T12:00:00+00:00", {}, [], error="invalid container")
    finding = CompatibilityFinding("probe.failed", "probe", "error", "Probe failed", "ffprobe failed", "Inspect the file and retry the scan.")
    asset_id = db.save_asset(library_id, asset, snapshot, [finding], scan_id)

    with TestClient(app) as client:
        page = client.get("/findings")
        assert "invalid container" in page.text
        assert "Plan unavailable" in page.text
        assert "A successful probe is required" in page.text
        plan = client.get(f"/api/assets/{asset_id}/plan")
        assert plan.status_code == 409
        assert plan.json()["detail"] == "A dry-run plan requires a successful probe"
