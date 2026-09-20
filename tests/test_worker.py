import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from streamkeeper.database import Database
from streamkeeper.models import LibraryType, ProbeSnapshot, ScanRun
from streamkeeper.probe import ProbeCancelled, ProbeError
from streamkeeper.worker import ScanWorker, due_libraries


def test_probe_failure_is_persisted_as_a_finding(tmp_path: Path, monkeypatch):
    media = tmp_path / "Broken Movie.mkv"
    media.write_bytes(b"not-media")
    db = Database(tmp_path / "worker.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")

    def fail_probe(*_args, **_kwargs):
        raise ProbeError("synthetic failure")

    monkeypatch.setattr("streamkeeper.worker.probe_file", fail_probe)
    worker = ScanWorker(db)
    try:
        scan_id = worker.enqueue(library_id=library_id, path=str(tmp_path), library_type=LibraryType.MOVIE)
        worker.tasks.join()
        scan = db.scan(scan_id)
        findings = db.findings("open")
        assert scan["status"] == "completed_with_errors"
        assert findings[0]["rule_id"] == "probe.failed"
        failures = db.scan_failures(scan_id)
        assert failures[0]["relative_path"] == "Broken Movie.mkv"
        assert failures[0]["error"] == "synthetic failure"
        events = db.scan_events(scan_id)
        assert events[0]["message"] == "Scan queued (manual)"
        assert any("Probe failed for Broken Movie.mkv" in event["message"] for event in events)
        assert events[-1]["level"] == "warning"
    finally:
        worker.close()


def test_worker_rehydrates_persisted_queue(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "worker.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    scan_id = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, False))
    seen: list[int] = []

    def record_scan(self, task):
        seen.append(task.scan_id)
        self.database.update_scan(task.scan_id, status="completed", phase="complete")

    monkeypatch.setattr(ScanWorker, "_scan", record_scan)
    worker = ScanWorker(db)
    try:
        worker.tasks.join()
        assert seen == [scan_id]
        assert db.scan(scan_id)["status"] == "completed"
    finally:
        worker.close()


def test_worker_replaces_interrupted_scan_once(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "worker.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    original_id = db.create_scan(ScanRun(None, library_id, str(tmp_path), LibraryType.MOVIE, True))
    db.update_scan(original_id, status="running", phase="probing", started_at="2026-01-01T00:00:00Z")
    seen: list[int] = []

    def record_scan(self, task):
        seen.append(task.scan_id)
        self.database.update_scan(task.scan_id, status="completed", phase="complete")

    monkeypatch.setattr(ScanWorker, "_scan", record_scan)
    worker = ScanWorker(db)
    try:
        worker.tasks.join()
        assert len(seen) == 1
        assert seen[0] != original_id
        assert db.scan(original_id)["status"] == "interrupted"
        assert db.scan(seen[0])["status"] == "completed"
        assert db.scan(seen[0])["trigger"] == "recovery"
        assert "replacement" in db.scan_events(original_id)[0]["message"]
        assert "Recovered" in db.scan_events(seen[0])[0]["message"]
    finally:
        worker.close()


def test_incremental_scan_reuses_unchanged_probe_but_deep_scan_does_not(tmp_path: Path, monkeypatch):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"synthetic media")
    db = Database(tmp_path / "worker.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    calls: list[bool] = []

    def probe(path, *, deep=False, cancel_event=None):
        calls.append(deep)
        return ProbeSnapshot(str(path), "2026-01-01T00:00:00Z", {"duration": "60"}, [])

    monkeypatch.setattr("streamkeeper.worker.probe_file", probe)
    worker = ScanWorker(db)
    try:
        first = worker.enqueue(library_id=library_id, path=str(tmp_path), library_type=LibraryType.MOVIE)
        worker.tasks.join()
        second = worker.enqueue(library_id=library_id, path=str(tmp_path), library_type=LibraryType.MOVIE)
        worker.tasks.join()
        third = worker.enqueue(library_id=library_id, path=str(tmp_path), library_type=LibraryType.MOVIE, deep=True)
        worker.tasks.join()

        assert calls == [False, True]
        assert "1 probed, 0 reused" in db.scan(first)["message"]
        assert "0 probed, 1 reused" in db.scan(second)["message"]
        assert "1 probed, 0 reused" in db.scan(third)["message"]
        assert db.scan(first)["new_files"] == 1
        assert db.scan(second)["unchanged_files"] == 1
        assert db.scan(second)["reused_probes"] == 1
        assert db.scan(third)["unchanged_files"] == 1
        assert db.scan(third)["probed_files"] == 1
    finally:
        worker.close()


def test_worker_suppresses_duplicate_active_scan_but_queues_different_depth(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "worker.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    started = threading.Event()
    release = threading.Event()
    seen: list[int] = []

    def blocked_scan(self, task):
        seen.append(task.scan_id)
        self.database.update_scan(task.scan_id, status="running", phase="probing")
        started.set()
        assert release.wait(3)
        self.database.update_scan(task.scan_id, status="completed", phase="complete")

    monkeypatch.setattr(ScanWorker, "_scan", blocked_scan)
    worker = ScanWorker(db)
    try:
        first = worker.enqueue(library_id=library_id, path=str(tmp_path), library_type=LibraryType.MOVIE)
        assert started.wait(2)
        duplicate = worker.enqueue(library_id=library_id, path=str(tmp_path), library_type=LibraryType.MOVIE)
        deep = worker.enqueue(
            library_id=library_id, path=str(tmp_path), library_type=LibraryType.MOVIE, deep=True,
        )

        assert duplicate == first
        assert deep != first
        assert len(db.scans()) == 2
        assert len(db.scan_events(first)) == 1

        release.set()
        worker.tasks.join()
        assert seen == [first, deep]
    finally:
        release.set()
        worker.close()


def test_incremental_scan_reprobes_changed_file(tmp_path: Path, monkeypatch):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"first")
    db = Database(tmp_path / "worker.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    calls: list[str] = []

    def probe(path, *, deep=False, cancel_event=None):
        calls.append(Path(path).read_text())
        return ProbeSnapshot(str(path), "2026-01-01T00:00:00Z", {"duration": "60"}, [])

    monkeypatch.setattr("streamkeeper.worker.probe_file", probe)
    worker = ScanWorker(db)
    try:
        worker.enqueue(library_id=library_id, path=str(tmp_path), library_type=LibraryType.MOVIE)
        worker.tasks.join()
        media.write_bytes(b"second version")
        changed_scan = worker.enqueue(library_id=library_id, path=str(tmp_path), library_type=LibraryType.MOVIE)
        worker.tasks.join()
        assert calls == ["first", "second version"]
        assert db.scan(changed_scan)["changed_files"] == 1
    finally:
        worker.close()


def test_worker_applies_global_and_library_exclusions_and_records_them(tmp_path: Path, monkeypatch):
    keep = tmp_path / "Movie" / "Featurettes" / "Interview.mkv"
    global_skip = tmp_path / "node_modules" / "fixture.mkv"
    library_skip = tmp_path / "Movie" / "Movie-workprint.mkv"
    hidden_skip = tmp_path / ".deletedByTMM" / "Old.mkv"
    for path in (keep, global_skip, library_skip, hidden_skip):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    db = Database(tmp_path / "worker.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    db.update_library(library_id, excluded_files=["*-workprint.mkv"])
    monkeypatch.setattr(
        "streamkeeper.worker.probe_file",
        lambda path, *, deep=False, cancel_event=None: ProbeSnapshot(str(path), "2026-01-01T00:00:00Z", {}, []),
    )
    worker = ScanWorker(db)
    try:
        scan_id = worker.enqueue(
            library_id=library_id, path=str(tmp_path), library_type=LibraryType.MOVIE,
        )
        worker.tasks.join()
        assert [asset["relative_path"] for asset in db.assets()] == [
            "Movie/Featurettes/Interview.mkv"
        ]
        scan = db.scan(scan_id)
        assert scan["excluded_paths"] == 3
        exclusions = db.scan_exclusions(scan_id)
        assert {item["relative_path"] for item in exclusions} == {
            ".deletedByTMM", "node_modules", "Movie/Movie-workprint.mkv",
        }
        assert any("Excluded 3 paths" in item["message"] for item in db.scan_events(scan_id))
    finally:
        worker.close()


def test_running_scan_cancellation_reaches_probe_and_is_not_a_failure(tmp_path: Path, monkeypatch):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"synthetic media")
    db = Database(tmp_path / "worker.sqlite3")
    library_id = db.add_library("Movies", str(tmp_path), "movie")
    started = threading.Event()

    def blocking_probe(path, *, deep=False, cancel_event=None):
        assert cancel_event is not None
        started.set()
        assert cancel_event.wait(2)
        raise ProbeCancelled("Probe cancelled")

    monkeypatch.setattr("streamkeeper.worker.probe_file", blocking_probe)
    worker = ScanWorker(db)
    try:
        scan_id = worker.enqueue(
            library_id=library_id, path=str(tmp_path), library_type=LibraryType.MOVIE, deep=True,
        )
        assert started.wait(2)
        requested = worker.cancel(scan_id)
        assert requested["phase"] in {"cancelling", "cancelled"}
        worker.tasks.join()

        scan = db.scan(scan_id)
        assert scan["status"] == "cancelled"
        assert scan["phase"] == "cancelled"
        assert scan["failed_files"] == 0
        assert db.findings() == []
        assert [event["message"] for event in db.scan_events(scan_id)][-2:] == [
            "Cancellation requested", "Scan cancelled",
        ]
    finally:
        worker.close()


def test_queued_scan_can_be_cancelled_before_probe_work_starts(tmp_path: Path, monkeypatch):
    first_root = tmp_path / "one"
    second_root = tmp_path / "two"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "One.mkv").write_bytes(b"one")
    (second_root / "Two.mkv").write_bytes(b"two")
    db = Database(tmp_path / "worker.sqlite3")
    first_library = db.add_library("One", str(first_root), "movie")
    second_library = db.add_library("Two", str(second_root), "movie")
    started = threading.Event()
    release = threading.Event()
    probed: list[str] = []

    def blocking_probe(path, *, deep=False, cancel_event=None):
        probed.append(Path(path).name)
        started.set()
        assert release.wait(2)
        return ProbeSnapshot(str(path), "2026-01-01T00:00:00Z", {}, [])

    monkeypatch.setattr("streamkeeper.worker.probe_file", blocking_probe)
    worker = ScanWorker(db)
    try:
        worker.enqueue(
            library_id=first_library, path=str(first_root), library_type=LibraryType.MOVIE,
        )
        assert started.wait(2)
        queued = worker.enqueue(
            library_id=second_library, path=str(second_root), library_type=LibraryType.MOVIE,
        )
        cancelled = worker.cancel(queued)
        assert cancelled["status"] == "cancelled"
        release.set()
        worker.tasks.join()

        assert probed == ["One.mkv"]
        assert db.scan(queued)["status"] == "cancelled"
        assert db.scan_events(queued)[-1]["message"] == "Scan cancelled before it started"
    finally:
        release.set()
        worker.close()


def test_scheduled_libraries_respect_interval_enabled_state_and_active_work(tmp_path: Path):
    db = Database(tmp_path / "worker.sqlite3")
    movies_id = db.add_library("Movies", str(tmp_path / "movies"), "movie")
    tv_id = db.add_library("Television", str(tmp_path / "tv"), "tv")
    disabled_id = db.add_library("Archive", str(tmp_path / "archive"), "movie")
    db.update_library(disabled_id, enabled=0)
    current = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)

    assert due_libraries(db, current.timestamp()) == []

    db.set_settings({"schedule": "daily"})
    assert [library["id"] for library in due_libraries(db, current.timestamp())] == [movies_id, tv_id]

    recent = ScanRun(
        None, movies_id, str(tmp_path / "movies"), LibraryType.MOVIE, False,
        status="completed", started_at=(current - timedelta(hours=2)).isoformat(),
    )
    old = ScanRun(
        None, tv_id, str(tmp_path / "tv"), LibraryType.TV, False,
        status="completed", started_at=(current - timedelta(days=2)).isoformat(),
    )
    db.create_scan(recent)
    db.create_scan(old)
    assert [library["id"] for library in due_libraries(db, current.timestamp())] == [tv_id]

    db.set_settings({"schedule": "weekly"})
    assert due_libraries(db, current.timestamp()) == []

    queued_id = db.create_scan(ScanRun(None, tv_id, str(tmp_path / "tv"), LibraryType.TV, False))
    db.set_settings({"schedule": "daily"})
    assert due_libraries(db, current.timestamp()) == []
    db.update_scan(queued_id, status="completed", started_at=(current - timedelta(days=2)).isoformat())
    assert [library["id"] for library in due_libraries(db, current.timestamp())] == [tv_id]
