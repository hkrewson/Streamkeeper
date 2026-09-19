from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .database import Database
from .discovery import discover_with_exclusions
from .models import CompatibilityFinding, LibraryType, ProbeSnapshot, ScanRun
from .policy import findings_for
from .probe import ProbeError, probe_file


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ScanTask:
    scan_id: int
    library_id: int | None
    path: str
    library_type: LibraryType
    deep: bool


def due_libraries(database: Database, current_timestamp: float | None = None) -> list[dict]:
    """Return enabled libraries due under the persisted global schedule."""
    schedule = database.settings().get("schedule", "manual")
    if schedule not in {"daily", "weekly"}:
        return []
    scans = database.scans(limit=1000)
    if any(scan["status"] in {"queued", "running"} for scan in scans):
        return []
    interval = 86_400 if schedule == "daily" else 604_800
    latest_by_library: dict[int | None, dict] = {}
    for scan in scans:
        latest_by_library.setdefault(scan["library_id"], scan)
    current = time.time() if current_timestamp is None else current_timestamp
    due: list[dict] = []
    for library in database.list_libraries():
        if not library["enabled"]:
            continue
        latest = latest_by_library.get(library["id"])
        if latest:
            reference = latest.get("started_at") or latest.get("created_at")
            try:
                last_run = datetime.fromisoformat(reference).timestamp() if reference else 0
            except (TypeError, ValueError):
                last_run = 0
            if current - last_run < interval:
                continue
        due.append(library)
    return due


class ScanWorker:
    """One persisted scan lane; deep packet analysis can never fan out."""

    def __init__(self, database: Database):
        self.database = database
        self.tasks: queue.Queue[ScanTask | None] = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="streamkeeper-scanner", daemon=True)
        self.scheduler = threading.Thread(target=self._schedule, name="streamkeeper-scheduler", daemon=True)
        self.database.apply_retention()
        for scan in self.database.recover_scan_tasks():
            self.tasks.put(
                ScanTask(
                    int(scan["id"]), scan["library_id"], scan["path"],
                    LibraryType(scan["library_type"]), bool(scan["deep"]),
                )
            )
        self.thread.start()
        self.scheduler.start()

    def enqueue(
        self, *, library_id: int | None, path: str, library_type: LibraryType,
        deep: bool = False, trigger: str = "manual",
    ) -> int:
        run = ScanRun(
            None, library_id, str(Path(path).expanduser().resolve()), library_type, deep,
            trigger=trigger,
        )
        scan_id, created = self.database.create_scan_if_idle(run)
        if not created:
            return scan_id
        label = "Deep scan" if deep else "Scan"
        self.database.add_scan_event(scan_id, f"{label} queued ({trigger})")
        self.tasks.put(ScanTask(scan_id, library_id, run.path, library_type, deep))
        return scan_id

    def close(self) -> None:
        self.stop_event.set()
        self.tasks.put(None)
        self.thread.join(timeout=5)
        self.scheduler.join(timeout=5)

    def _schedule(self) -> None:
        while not self.stop_event.wait(60):
            if not self.tasks.empty():
                continue
            for library in due_libraries(self.database):
                self.enqueue(
                    library_id=library["id"], path=library["path"],
                    library_type=LibraryType(library["library_type"]), deep=False,
                    trigger="scheduled",
                )

    def _run(self) -> None:
        while True:
            task = self.tasks.get()
            try:
                if task is None:
                    return
                self._scan(task)
            finally:
                self.tasks.task_done()

    def _scan(self, task: ScanTask) -> None:
        self.database.update_scan(task.scan_id, status="running", started_at=now(), phase="discovery", message="Finding media files")
        self.database.add_scan_event(task.scan_id, "Scan started; discovering media files")
        try:
            settings = self.database.settings()
            library = self.database.library(task.library_id) if task.library_id is not None else None
            excluded_directories = list(settings.get("excluded_directories", []))
            excluded_files = list(settings.get("excluded_files", []))
            if library:
                excluded_directories.extend(library.get("excluded_directories", []))
                excluded_files.extend(library.get("excluded_files", []))
            discovery = discover_with_exclusions(
                task.path,
                task.library_type,
                excluded_directories=list(dict.fromkeys(excluded_directories)),
                excluded_files=list(dict.fromkeys(excluded_files)),
            )
            assets = discovery.assets
            self.database.save_scan_exclusions(task.scan_id, discovery.exclusions)
        except Exception as exc:
            self.database.update_scan(task.scan_id, status="failed", finished_at=now(), message=str(exc))
            self.database.add_scan_event(task.scan_id, f"Discovery failed: {exc}", "error")
            return
        self.database.update_scan(
            task.scan_id, phase="probing", total_files=len(assets),
            excluded_paths=len(discovery.exclusions), message="Reading stream metadata",
        )
        self.database.add_scan_event(task.scan_id, f"Discovered {len(assets)} supported media files")
        if discovery.exclusions:
            self.database.add_scan_event(
                task.scan_id,
                f"Excluded {len(discovery.exclusions)} paths by hidden-path and configured-pattern rules",
            )
        failed = 0
        probed = 0
        reused = 0
        state_counts = {"new": 0, "changed": 0, "unchanged": 0}
        completed_assets: list[tuple[int, set[str]]] = []
        for index, asset in enumerate(assets, 1):
            asset_state = self.database.asset_state(task.library_id, asset)
            state_counts[asset_state] += 1
            try:
                snapshot = None if task.deep else self.database.cached_probe(task.library_id, asset)
                record_probe = snapshot is None
                if snapshot is None:
                    probed += 1
                    snapshot = probe_file(asset.path, deep=task.deep)
                else:
                    reused += 1
                settings = self.database.settings()
                findings = findings_for(snapshot, int(settings["network_ceiling_bps"]))
                asset_id = self.database.save_asset(
                    task.library_id, asset, snapshot, findings, task.scan_id,
                    record_probe=record_probe,
                )
                completed_assets.append((asset_id, {finding.rule_id for finding in findings}))
            except (ProbeError, OSError, ValueError) as exc:
                failed += 1
                snapshot = ProbeSnapshot(asset.path, now(), {}, [], error=str(exc))
                failure = CompatibilityFinding(
                    "probe.failed", "probe", "error", "Probe failed", str(exc),
                    "Inspect the file and retry the scan.",
                )
                self.database.save_asset(task.library_id, asset, snapshot, [failure], task.scan_id)
                self.database.add_scan_event(task.scan_id, f"Probe failed for {asset.relative_path}: {exc}", "error")
            self.database.update_scan(
                task.scan_id, processed_files=index, failed_files=failed,
                new_files=state_counts["new"], changed_files=state_counts["changed"],
                unchanged_files=state_counts["unchanged"], probed_files=probed,
                reused_probes=reused, message=asset.relative_path,
            )
        for asset_id, seen_rules in completed_assets:
            self.database.resolve_absent_findings(asset_id, seen_rules)
        removed = self.database.resolve_unseen_asset_findings(task.library_id, task.scan_id)
        status = "completed_with_errors" if failed else "completed"
        self.database.update_scan(
            task.scan_id, status=status, phase="complete", finished_at=now(), removed_files=removed,
            message=(f"Scanned {len(assets)} files; {state_counts['new']} new, "
                     f"{state_counts['changed']} changed, {removed} removed; "
                     f"{probed} probed, {reused} reused; {failed} failed"),
        )
        self.database.add_scan_event(
            task.scan_id,
            (f"Scan completed: {len(assets)} files; {state_counts['new']} new, "
             f"{state_counts['changed']} modified, {removed} removed; {failed} failed"),
            "warning" if failed else "info",
        )
