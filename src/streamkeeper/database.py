from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import CompatibilityFinding, MediaAsset, ProbeSnapshot, ScanRun


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS libraries (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    library_type TEXT NOT NULL CHECK(library_type IN ('movie','tv','mixed')),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY,
                    library_id INTEGER REFERENCES libraries(id) ON DELETE SET NULL,
                    path TEXT NOT NULL,
                    library_type TEXT NOT NULL,
                    deep INTEGER NOT NULL DEFAULT 0,
                    trigger TEXT NOT NULL DEFAULT 'manual',
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    total_files INTEGER NOT NULL DEFAULT 0,
                    processed_files INTEGER NOT NULL DEFAULT 0,
                    failed_files INTEGER NOT NULL DEFAULT 0,
                    new_files INTEGER NOT NULL DEFAULT 0,
                    changed_files INTEGER NOT NULL DEFAULT 0,
                    unchanged_files INTEGER NOT NULL DEFAULT 0,
                    removed_files INTEGER NOT NULL DEFAULT 0,
                    probed_files INTEGER NOT NULL DEFAULT 0,
                    reused_probes INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT,
                    finished_at TEXT,
                    message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assets (
                    id INTEGER PRIMARY KEY,
                    library_id INTEGER REFERENCES libraries(id) ON DELETE CASCADE,
                    path TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    library_type TEXT NOT NULL,
                    media_kind TEXT NOT NULL,
                    extra_type TEXT,
                    title TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    assessment TEXT NOT NULL DEFAULT 'compatible',
                    present INTEGER NOT NULL DEFAULT 1,
                    last_scan_id INTEGER REFERENCES scans(id) ON DELETE SET NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(library_id, path)
                );
                CREATE TABLE IF NOT EXISTS probes (
                    id INTEGER PRIMARY KEY,
                    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    scan_id INTEGER REFERENCES scans(id) ON DELETE SET NULL,
                    captured_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS findings (
                    id INTEGER PRIMARY KEY,
                    library_id INTEGER REFERENCES libraries(id) ON DELETE CASCADE,
                    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    rule_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    recommended_action TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    first_scan_id INTEGER REFERENCES scans(id) ON DELETE SET NULL,
                    last_scan_id INTEGER REFERENCES scans(id) ON DELETE SET NULL,
                    UNIQUE(library_id, asset_id, rule_id)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scan_events (
                    id INTEGER PRIMARY KEY,
                    scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_assets_library ON assets(library_id, relative_path);
                CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status, last_seen_at);
                CREATE INDEX IF NOT EXISTS idx_scans_created ON scans(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_scan_events_scan ON scan_events(scan_id,id);
                """
            )
            asset_columns = {row[1] for row in db.execute("PRAGMA table_info(assets)")}
            if "present" not in asset_columns:
                db.execute("ALTER TABLE assets ADD COLUMN present INTEGER NOT NULL DEFAULT 1")
            scan_columns = {row[1] for row in db.execute("PRAGMA table_info(scans)")}
            if "trigger" not in scan_columns:
                db.execute("ALTER TABLE scans ADD COLUMN trigger TEXT NOT NULL DEFAULT 'manual'")
            for column in (
                "new_files", "changed_files", "unchanged_files", "removed_files",
                "probed_files", "reused_probes",
            ):
                if column not in scan_columns:
                    db.execute(f"ALTER TABLE scans ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")

    def recover_scan_tasks(self) -> list[dict[str, Any]]:
        """Return persisted queued work and replace scans interrupted by a restart.

        An interrupted scan remains in history. Its replacement receives a new ID so
        partial progress from the abandoned run is never mistaken for a complete scan.
        Calling this method more than once does not create additional replacements.
        """
        now = utcnow()
        with self.connect() as db:
            interrupted = list(
                db.execute(
                    """SELECT id,library_id,path,library_type,deep
                       FROM scans WHERE status='running' ORDER BY id"""
                )
            )
            for scan in interrupted:
                cursor = db.execute(
                    """INSERT INTO scans(library_id,path,library_type,deep,trigger,status,phase,total_files,
                       processed_files,failed_files,started_at,finished_at,message,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        scan["library_id"], scan["path"], scan["library_type"], scan["deep"], "recovery",
                        "queued", "recovery", 0, 0, 0, None, None,
                        f"Recovered from interrupted scan #{scan['id']}", now,
                    ),
                )
                replacement_id = int(cursor.lastrowid)
                db.execute(
                    """UPDATE scans SET status='interrupted', phase='interrupted', finished_at=?,
                       message=? WHERE id=?""",
                    (now, f"Application restarted during scan; replacement queued as #{replacement_id}", scan["id"]),
                )
                db.execute(
                    "INSERT INTO scan_events(scan_id,created_at,level,message) VALUES(?,?,?,?)",
                    (scan["id"], now, "warning", f"Application restart interrupted this scan; replacement #{replacement_id} was queued"),
                )
                db.execute(
                    "INSERT INTO scan_events(scan_id,created_at,level,message) VALUES(?,?,?,?)",
                    (replacement_id, now, "info", f"Recovered from interrupted scan #{scan['id']}"),
                )

            rows = db.execute(
                """SELECT id,library_id,path,library_type,deep
                   FROM scans WHERE status='queued' ORDER BY id"""
            )
            return [dict(row) for row in rows]

    def add_library(self, name: str, path: str, library_type: str) -> int:
        now = utcnow()
        resolved = str(Path(path).expanduser().resolve())
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO libraries(name,path,library_type,created_at,updated_at) VALUES(?,?,?,?,?)",
                (name.strip() or Path(resolved).name, resolved, library_type, now, now),
            )
            return int(cursor.lastrowid)

    def list_libraries(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM libraries ORDER BY name COLLATE NOCASE")]

    def library(self, library_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
            return dict(row) if row else None

    def update_library(self, library_id: int, **values: Any) -> dict[str, Any] | None:
        allowed = {"name", "path", "library_type", "enabled"}
        values = {key: value for key, value in values.items() if key in allowed}
        if "path" in values:
            values["path"] = str(Path(values["path"]).expanduser().resolve())
        if not values:
            return self.library(library_id)
        values["updated_at"] = utcnow()
        fields = ",".join(f"{key}=?" for key in values)
        with self.connect() as db:
            db.execute(f"UPDATE libraries SET {fields} WHERE id=?", (*values.values(), library_id))
        return self.library(library_id)

    @staticmethod
    def _insert_scan(db: sqlite3.Connection, run: ScanRun) -> int:
        cursor = db.execute(
            """INSERT INTO scans(library_id,path,library_type,deep,trigger,status,phase,total_files,
               processed_files,failed_files,new_files,changed_files,unchanged_files,removed_files,
               probed_files,reused_probes,started_at,finished_at,message,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run.library_id, run.path, run.library_type.value, int(run.deep), run.trigger, run.status, run.phase,
             run.total_files, run.processed_files, run.failed_files, run.new_files, run.changed_files,
             run.unchanged_files, run.removed_files, run.probed_files, run.reused_probes,
             run.started_at, run.finished_at, run.message, utcnow()),
        )
        return int(cursor.lastrowid)

    def create_scan(self, run: ScanRun) -> int:
        with self.connect() as db:
            return self._insert_scan(db, run)

    def create_scan_if_idle(self, run: ScanRun) -> tuple[int, bool]:
        """Create a scan unless the same target and depth is already active."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if run.library_id is None:
                active = db.execute(
                    """SELECT id FROM scans
                       WHERE library_id IS NULL AND path=? AND deep=?
                         AND status IN ('queued','running')
                       ORDER BY id LIMIT 1""",
                    (run.path, int(run.deep)),
                ).fetchone()
            else:
                active = db.execute(
                    """SELECT id FROM scans
                       WHERE library_id=? AND deep=? AND status IN ('queued','running')
                       ORDER BY id LIMIT 1""",
                    (run.library_id, int(run.deep)),
                ).fetchone()
            if active:
                return int(active["id"]), False
            return self._insert_scan(db, run), True

    def update_scan(self, scan_id: int, **values: Any) -> None:
        allowed = {
            "status", "phase", "total_files", "processed_files", "failed_files",
            "new_files", "changed_files", "unchanged_files", "removed_files",
            "probed_files", "reused_probes", "started_at", "finished_at", "message",
        }
        values = {key: value for key, value in values.items() if key in allowed}
        if not values:
            return
        fields = ",".join(f"{key}=?" for key in values)
        with self.connect() as db:
            db.execute(f"UPDATE scans SET {fields} WHERE id=?", (*values.values(), scan_id))

    def scans(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT s.*, l.name AS library_name FROM scans s LEFT JOIN libraries l ON l.id=s.library_id
                   ORDER BY s.id DESC LIMIT ?""", (limit,),
            )
            return [dict(row) for row in rows]

    def scan(self, scan_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT s.*, l.name AS library_name FROM scans s LEFT JOIN libraries l ON l.id=s.library_id WHERE s.id=?",
                (scan_id,),
            ).fetchone()
            return dict(row) if row else None

    def scan_failures(self, scan_id: int, limit: int = 100) -> list[dict[str, Any]]:
        """Return file-level probe failures captured during one scan run."""
        with self.connect() as db:
            rows = db.execute(
                """SELECT a.id AS asset_id,a.title,a.relative_path,a.path,
                   json_extract(p.snapshot_json,'$.error') AS error
                   FROM probes p JOIN assets a ON a.id=p.asset_id
                   WHERE p.scan_id=? AND json_extract(p.snapshot_json,'$.error') IS NOT NULL
                   ORDER BY a.relative_path COLLATE NOCASE LIMIT ?""",
                (scan_id, limit),
            )
            return [dict(row) for row in rows]

    def add_scan_event(self, scan_id: int, message: str, level: str = "info") -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO scan_events(scan_id,created_at,level,message) VALUES(?,?,?,?)",
                (scan_id, utcnow(), level, message),
            )

    def scan_events(self, scan_id: int, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT id,created_at,level,message FROM scan_events
                   WHERE scan_id=? ORDER BY id LIMIT ?""",
                (scan_id, limit),
            )
            return [dict(row) for row in rows]

    def cached_probe(self, library_id: int | None, asset: MediaAsset) -> ProbeSnapshot | None:
        """Return the latest successful probe when the filesystem identity is unchanged."""
        with self.connect() as db:
            row = db.execute(
                """SELECT p.snapshot_json FROM assets a JOIN probes p ON p.asset_id=a.id
                   WHERE a.library_id IS ? AND a.path=? AND a.size_bytes=? AND a.modified_ns=?
                   ORDER BY p.id DESC LIMIT 1""",
                (library_id, asset.path, asset.size_bytes, asset.modified_ns),
            ).fetchone()
        if not row:
            return None
        try:
            snapshot = ProbeSnapshot(**json.loads(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return None if snapshot.error else snapshot

    def asset_state(self, library_id: int | None, asset: MediaAsset) -> str:
        """Classify a discovered path relative to the current catalog."""
        with self.connect() as db:
            row = db.execute(
                """SELECT size_bytes,modified_ns,present FROM assets
                   WHERE library_id IS ? AND path=?""",
                (library_id, asset.path),
            ).fetchone()
        if not row or not row["present"]:
            return "new"
        if row["size_bytes"] != asset.size_bytes or row["modified_ns"] != asset.modified_ns:
            return "changed"
        return "unchanged"

    def save_asset(
        self, library_id: int | None, asset: MediaAsset, snapshot: ProbeSnapshot,
        findings: list[CompatibilityFinding], scan_id: int, *, record_probe: bool = True,
    ) -> int:
        now = utcnow()
        assessment = "action" if any(item.severity in {"action", "error"} for item in findings) else "warning" if findings else "compatible"
        with self.connect() as db:
            db.execute(
                """INSERT INTO assets(library_id,path,relative_path,library_type,media_kind,extra_type,title,
                   size_bytes,modified_ns,assessment,present,last_scan_id,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(library_id,path) DO UPDATE SET relative_path=excluded.relative_path,
                   library_type=excluded.library_type, media_kind=excluded.media_kind, extra_type=excluded.extra_type,
                   title=excluded.title, size_bytes=excluded.size_bytes, modified_ns=excluded.modified_ns,
                   assessment=excluded.assessment, present=1,
                   last_scan_id=excluded.last_scan_id, last_seen_at=excluded.last_seen_at""",
                (library_id, asset.path, asset.relative_path, asset.library_type.value, asset.media_kind,
                 asset.extra_type, asset.title, asset.size_bytes, asset.modified_ns, assessment, 1, scan_id, now),
            )
            row = db.execute("SELECT id FROM assets WHERE library_id IS ? AND path=?", (library_id, asset.path)).fetchone()
            assert row
            asset_id = int(row[0])
            if record_probe:
                db.execute(
                    "INSERT INTO probes(asset_id,scan_id,captured_at,snapshot_json) VALUES(?,?,?,?)",
                    (asset_id, scan_id, snapshot.captured_at, json.dumps(snapshot.to_dict(), separators=(",", ":"))),
                )
            seen_rules: set[str] = set()
            for finding in findings:
                seen_rules.add(finding.rule_id)
                db.execute(
                    """INSERT INTO findings(library_id,asset_id,rule_id,category,severity,title,detail,recommended_action,
                       status,first_seen_at,last_seen_at,first_scan_id,last_scan_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(library_id,asset_id,rule_id) DO UPDATE SET category=excluded.category,
                       severity=excluded.severity,title=excluded.title,detail=excluded.detail,
                       recommended_action=excluded.recommended_action,
                       status=CASE WHEN findings.status='ignored' THEN 'ignored' ELSE 'open' END,
                       last_seen_at=excluded.last_seen_at,last_scan_id=excluded.last_scan_id""",
                    (library_id, asset_id, finding.rule_id, finding.category, finding.severity, finding.title,
                     finding.detail, finding.recommended_action, finding.status.value, now, now, scan_id, scan_id),
                )
            return asset_id

    def resolve_absent_findings(self, asset_id: int, seen_rules: set[str]) -> None:
        """Resolve only after the caller has completed a successful scan pass."""
        with self.connect() as db:
            if seen_rules:
                placeholders = ",".join("?" for _ in seen_rules)
                db.execute(
                    f"UPDATE findings SET status='resolved' WHERE asset_id=? AND status IN ('open','ignored') AND rule_id NOT IN ({placeholders})",
                    (asset_id, *seen_rules),
                )
            else:
                db.execute(
                    "UPDATE findings SET status='resolved' WHERE asset_id=? AND status IN ('open','ignored')",
                    (asset_id,),
                )

    def resolve_unseen_asset_findings(self, library_id: int | None, scan_id: int) -> int:
        if library_id is None:
            return 0
        with self.connect() as db:
            db.execute(
                """UPDATE findings SET status='resolved'
                   WHERE library_id=? AND status IN ('open','ignored') AND asset_id IN
                   (SELECT id FROM assets WHERE library_id=? AND (last_scan_id IS NULL OR last_scan_id<>?))""",
                (library_id, library_id, scan_id),
            )
            cursor = db.execute(
                """UPDATE assets SET present=0
                   WHERE library_id=? AND present=1 AND (last_scan_id IS NULL OR last_scan_id<>?)""",
                (library_id, scan_id),
            )
            return max(cursor.rowcount, 0)

    def assets(
        self, library_id: int | None = None, assessment: str | None = None,
        limit: int = 500, *, include_missing: bool = False,
    ) -> list[dict[str, Any]]:
        clauses, parameters = ([] if include_missing else ["a.present=1"]), []
        if library_id is not None:
            clauses.append("a.library_id=?")
            parameters.append(library_id)
        if assessment:
            clauses.append("a.assessment=?")
            parameters.append(assessment)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as db:
            rows = db.execute(
                f"""SELECT a.*, l.name AS library_name,
                    json_extract(p.snapshot_json,'$.streams[0].codec_name') AS video_codec,
                    json_extract(p.snapshot_json,'$.peak_bitrate_bps') AS peak_bitrate_bps,
                    p.snapshot_json AS probe_json
                    FROM assets a LEFT JOIN libraries l ON l.id=a.library_id
                    LEFT JOIN probes p ON p.id=(SELECT id FROM probes WHERE asset_id=a.id ORDER BY id DESC LIMIT 1)
                    {where} ORDER BY a.relative_path COLLATE NOCASE LIMIT ?""",
                (*parameters, limit),
            )
            return [self._decorate_asset(dict(row)) for row in rows]

    def asset(self, asset_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
            return dict(row) if row else None

    def asset_counts(self, library_id: int | None = None) -> dict[str, int]:
        clauses = ["present=1"]
        parameters: list[Any] = []
        if library_id is not None:
            clauses.append("library_id=?")
            parameters.append(library_id)
        result = {"total": 0, "compatible": 0, "warning": 0, "action": 0}
        with self.connect() as db:
            rows = db.execute(
                f"""SELECT assessment,COUNT(*) AS count FROM assets
                    WHERE {' AND '.join(clauses)} GROUP BY assessment""",
                parameters,
            )
            for row in rows:
                result[row["assessment"]] = int(row["count"])
                result["total"] += int(row["count"])
        return result

    @staticmethod
    def _decorate_asset(item: dict[str, Any]) -> dict[str, Any]:
        try:
            snapshot = json.loads(item.pop("probe_json") or "{}")
        except json.JSONDecodeError:
            snapshot = {}
        streams = snapshot.get("streams", [])
        video = next((stream for stream in streams if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")), {})
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
        item["video_codec"] = video.get("codec_name", "Unknown").upper()
        channels = audio.get("channels")
        item["audio_summary"] = f"{str(audio.get('codec_name', 'Unknown')).upper()} {channels or '?'} ch"
        item["resolution"] = f"{video.get('width', '?')}×{video.get('height', '?')}"
        item["duration_seconds"] = float(snapshot.get("format", {}).get("duration", 0) or 0)
        return item

    def probe(self, asset_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT snapshot_json FROM probes WHERE asset_id=? ORDER BY id DESC LIMIT 1", (asset_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def findings(
        self, status: str | None = None, limit: int = 500,
        library_id: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if status == "new":
            clauses.extend(["f.status='open'", "f.first_scan_id=f.last_scan_id"])
        elif status:
            clauses.append("f.status=?")
            parameters.append(status)
        if library_id is not None:
            clauses.append("f.library_id=?")
            parameters.append(library_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as db:
            rows = db.execute(
                f"""SELECT f.*, a.title AS asset_title, a.relative_path, a.path, l.name AS library_name
                    FROM findings f JOIN assets a ON a.id=f.asset_id LEFT JOIN libraries l ON l.id=f.library_id
                    {where} ORDER BY CASE f.severity WHEN 'error' THEN 0 WHEN 'action' THEN 1 ELSE 2 END,
                    f.last_seen_at DESC LIMIT ?""", (*parameters, limit),
            )
            return [dict(row) for row in rows]

    def finding_counts(self, library_id: int | None = None) -> dict[str, int]:
        where = " WHERE library_id=?" if library_id is not None else ""
        parameters: tuple[Any, ...] = (library_id,) if library_id is not None else ()
        with self.connect() as db:
            result = {"open": 0, "new": 0, "ignored": 0, "resolved": 0}
            for row in db.execute(f"SELECT status, COUNT(*) AS count FROM findings{where} GROUP BY status", parameters):
                result[row[0]] = int(row[1])
            new_where = "WHERE first_scan_id=last_scan_id AND status='open'"
            if library_id is not None:
                new_where += " AND library_id=?"
            row = db.execute(f"SELECT COUNT(*) FROM findings {new_where}", parameters).fetchone()
            result["new"] = int(row[0]) if row else 0
            return result

    def set_finding_status(self, finding_id: int, status: str) -> dict[str, Any] | None:
        if status not in {"open", "ignored", "resolved"}:
            raise ValueError("status must be open, ignored, or resolved")
        with self.connect() as db:
            db.execute("UPDATE findings SET status=? WHERE id=?", (status, finding_id))
            row = db.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
            return dict(row) if row else None

    def report(self, library_id: int | None = None) -> dict[str, Any]:
        assets = self.assets(library_id=library_id, limit=100000)
        codec_counts: dict[str, int] = {}
        assessment_counts: dict[str, int] = {}
        resolution_counts: dict[str, int] = {}
        network_counts = {"comfortable": 0, "near_limit": 0, "over_limit": 0, "not_measured": 0}
        network_assets: list[dict[str, Any]] = []
        network_ceiling = int(self.settings()["network_ceiling_bps"])
        for asset in assets:
            codec_counts[asset["video_codec"]] = codec_counts.get(asset["video_codec"], 0) + 1
            assessment_counts[asset["assessment"]] = assessment_counts.get(asset["assessment"], 0) + 1
            resolution_counts[asset["resolution"]] = resolution_counts.get(asset["resolution"], 0) + 1
            peak = asset.get("peak_bitrate_bps")
            if not peak:
                network_counts["not_measured"] += 1
                continue
            ratio = int(peak) / network_ceiling if network_ceiling else 0
            bucket = "over_limit" if ratio > 1 else "near_limit" if ratio >= 0.8 else "comfortable"
            network_counts[bucket] += 1
            network_assets.append({
                "title": asset["title"],
                "relative_path": asset["relative_path"],
                "peak_bitrate_bps": int(peak),
                "ceiling_percent": round(ratio * 100),
                "assessment": bucket,
            })
        network_assets.sort(key=lambda item: item["peak_bitrate_bps"], reverse=True)
        return {
            "total": len(assets),
            "video_codecs": codec_counts,
            "resolutions": resolution_counts,
            "assessments": assessment_counts,
            "network": network_counts,
            "network_ceiling_bps": network_ceiling,
            "network_assets": network_assets[:20],
        }

    def settings(self) -> dict[str, Any]:
        defaults = {"network_ceiling_bps": 900_000_000, "schedule": "manual", "retention_days": 90, "fallback_language": "eng"}
        with self.connect() as db:
            for row in db.execute("SELECT key,value_json FROM settings"):
                defaults[row[0]] = json.loads(row[1])
        return defaults

    def set_settings(self, values: dict[str, Any]) -> None:
        with self.connect() as db:
            for key, value in values.items():
                db.execute(
                    "INSERT INTO settings(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                    (key, json.dumps(value), utcnow()),
                )

    def apply_retention(self, retention_days: int | None = None) -> dict[str, int]:
        """Prune expired operational history without deleting current catalog data."""
        days = max(int(retention_days or self.settings()["retention_days"]), 1)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as db:
            scan_cursor = db.execute(
                """DELETE FROM scans WHERE created_at<?
                   AND status NOT IN ('queued','running')""",
                (cutoff,),
            )
            probe_cursor = db.execute(
                """DELETE FROM probes WHERE captured_at<? AND id NOT IN
                   (SELECT MAX(id) FROM probes GROUP BY asset_id)""",
                (cutoff,),
            )
            return {
                "scans": max(scan_cursor.rowcount, 0),
                "probes": max(probe_cursor.rowcount, 0),
            }
