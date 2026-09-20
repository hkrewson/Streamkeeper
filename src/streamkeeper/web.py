from __future__ import annotations

import base64
import csv
import io
import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import Database
from .discovery import classify
from .models import LibraryType, ProbeSnapshot
from .planner import build_plan
from .probe import ProbeError, ffmpeg_has_bitstream_filter, probe_file, tool_status
from .worker import ScanWorker

PACKAGE_DIR = Path(__file__).parent


def create_app(database_path: str | Path | None = None, *, start_worker: bool = True) -> FastAPI:
    db_path = Path(
        database_path
        or os.getenv("STREAMKEEPER_DB")
        or os.getenv("PLEX_CONVERT_DB")
        or Path.cwd() / "data" / "streamkeeper.sqlite3"
    )
    database = Database(db_path)
    worker: ScanWorker | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal worker
        if start_worker:
            worker = ScanWorker(database)
            _app.state.worker = worker
        yield
        if worker:
            worker.close()

    app = FastAPI(title="Streamkeeper", version="0.1.0", lifespan=lifespan)
    app.state.database = database
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    def authorize(request: Request) -> None:
        username = os.getenv("STREAMKEEPER_USER") or os.getenv("PLEX_CONVERT_USER", "")
        password = os.getenv("STREAMKEEPER_PASSWORD") or os.getenv("PLEX_CONVERT_PASSWORD", "")
        if not username and not password:
            return
        if not username or not password:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Authentication configuration is incomplete",
            )
        supplied = request.headers.get("authorization", "")
        expected = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        if not secrets.compare_digest(supplied, expected):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": 'Basic realm="Streamkeeper"'},
            )

    time_zones = {
        "local": "Browser or device time",
        "UTC": "UTC",
        "America/New_York": "Eastern time",
        "America/Chicago": "Central time",
        "America/Denver": "Mountain time",
        "America/Los_Angeles": "Pacific time",
        "America/Anchorage": "Alaska time",
        "Pacific/Honolulu": "Hawaii time",
        "Europe/London": "United Kingdom time",
        "Europe/Paris": "Central European time",
        "Asia/Tokyo": "Japan time",
        "Australia/Sydney": "Sydney time",
    }

    def page(request: Request, name: str, **context: Any) -> HTMLResponse:
        settings = database.settings()
        base = {
            "request": request,
            "page": name,
            "libraries": database.list_libraries(),
            "finding_counts": database.finding_counts(),
            "display_time_zone": settings["time_zone"],
        }
        base.update(context)
        return templates.TemplateResponse(request, f"{name}.html", base)

    def validated_settings(payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "network_ceiling_bps", "schedule", "retention_days", "fallback_language",
            "time_zone", "excluded_directories", "excluded_files",
        }
        updates = {key: value for key, value in payload.items() if key in allowed}
        if "network_ceiling_bps" in updates:
            try:
                updates["network_ceiling_bps"] = int(updates["network_ceiling_bps"])
            except (TypeError, ValueError) as exc:
                raise HTTPException(422, "network_ceiling_bps must be a positive integer") from exc
            if updates["network_ceiling_bps"] < 1:
                raise HTTPException(422, "network_ceiling_bps must be a positive integer")
        if "retention_days" in updates:
            try:
                updates["retention_days"] = int(updates["retention_days"])
            except (TypeError, ValueError) as exc:
                raise HTTPException(422, "retention_days must be a positive integer") from exc
            if updates["retention_days"] < 1:
                raise HTTPException(422, "retention_days must be a positive integer")
        if "schedule" in updates and updates["schedule"] not in {"manual", "daily", "weekly"}:
            raise HTTPException(422, "schedule must be manual, daily, or weekly")
        if "fallback_language" in updates:
            language = str(updates["fallback_language"]).strip().lower() or "eng"
            if len(language) != 3 or not language.isalpha():
                raise HTTPException(422, "fallback_language must be a three-letter language code")
            updates["fallback_language"] = language
        if "time_zone" in updates and updates["time_zone"] not in time_zones:
            raise HTTPException(422, "time_zone is not supported")
        for key in ("excluded_directories", "excluded_files"):
            if key in updates:
                updates[key] = normalize_patterns(updates[key], key)
        return updates

    def normalize_patterns(value: Any, label: str) -> list[str]:
        if isinstance(value, str):
            values = value.splitlines()
        elif isinstance(value, list):
            values = value
        else:
            raise HTTPException(422, f"{label} must be a list or one pattern per line")
        patterns = list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))
        if len(patterns) > 100 or any(len(pattern) > 256 for pattern in patterns):
            raise HTTPException(422, f"{label} contains too many or overly long patterns")
        return patterns

    def tool_health() -> dict[str, Any]:
        tools = {name: tool_status(name) for name in ("ffmpeg", "ffprobe", "dovi_tool")}
        tools["ffmpeg"]["role"] = "Media conversion"
        tools["ffprobe"]["role"] = "Scanning and output validation"
        tools["dovi_tool"]["role"] = "Dolby Vision normalization when required"
        dovi_rpu_ready = False
        dovi_rpu_error = None
        if tools["ffmpeg"]["available"]:
            try:
                dovi_rpu_ready = ffmpeg_has_bitstream_filter("dovi_rpu")
                if not dovi_rpu_ready:
                    dovi_rpu_error = "The installed FFmpeg does not provide the dovi_rpu filter"
            except ProbeError as exc:
                dovi_rpu_error = str(exc)
        return {
            "scan_ready": bool(tools["ffprobe"]["available"]),
            "conversion_ready": bool(tools["ffmpeg"]["available"] and tools["ffprobe"]["available"]),
            "dolby_vision_ready": bool(tools["dovi_tool"]["available"] and dovi_rpu_ready),
            "dovi_rpu_ready": dovi_rpu_ready,
            "dovi_rpu_error": dovi_rpu_error,
            "tools": tools,
        }

    def probe_presentation(snapshot_data: dict[str, Any] | None) -> dict[str, Any] | None:
        if not snapshot_data:
            return None
        streams = []
        for ordinal, stream in enumerate(snapshot_data.get("streams", []), 1):
            tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
            stream_type = str(stream.get("codec_type", "unknown"))
            details: list[str] = []
            if stream_type == "video":
                if stream.get("width") and stream.get("height"):
                    details.append(f"{stream['width']}×{stream['height']}")
                if stream.get("profile"):
                    details.append(str(stream["profile"]))
                if stream.get("pix_fmt"):
                    details.append(str(stream["pix_fmt"]))
            elif stream_type == "audio":
                channels = stream.get("channels")
                details.append(f"{channels} channel{'s' if channels != 1 else ''}" if channels else "Channels unknown")
                if stream.get("channel_layout"):
                    details.append(str(stream["channel_layout"]))
            language = tags.get("language")
            if language:
                details.append(str(language))
            streams.append({
                "number": ordinal,
                "type": stream_type.capitalize(),
                "codec": str(stream.get("codec_long_name") or stream.get("codec_name") or "Unknown"),
                "title": tags.get("title"),
                "details": details,
                "default": bool(stream.get("disposition", {}).get("default")),
                "forced": bool(stream.get("disposition", {}).get("forced")),
            })
        format_data = snapshot_data.get("format") if isinstance(snapshot_data.get("format"), dict) else {}
        try:
            duration = float(format_data.get("duration", 0) or 0)
        except (TypeError, ValueError):
            duration = 0
        try:
            size = int(format_data.get("size", 0) or 0)
        except (TypeError, ValueError):
            size = 0
        return {
            "error": snapshot_data.get("error"),
            "captured_at": snapshot_data.get("captured_at"),
            "tool_version": snapshot_data.get("tool_version"),
            "container": format_data.get("format_long_name") or format_data.get("format_name") or "Unknown",
            "duration": f"{duration / 60:.1f} minutes" if duration else "Unknown",
            "size": f"{size / (1024 ** 3):.2f} GiB" if size else "Unknown",
            "peak_bitrate": (
                f"{int(snapshot_data['peak_bitrate_bps']) / 1_000_000:.1f} Mbps"
                if snapshot_data.get("peak_bitrate_bps") else "Not measured"
            ),
            "chapters": len(snapshot_data.get("chapters", [])),
            "streams": streams,
        }

    def plan_presentation(plan) -> dict[str, Any]:
        video_labels = {
            "copy": "Keep existing video",
            "transcode_hevc": "Convert video to HEVC",
            "dovi_convert": "Normalize Dolby Vision to profile 8.1",
            "strip_hdr10plus": "Remove HDR10+ metadata and retain HDR10",
            "dovi_convert_strip_hdr10plus": "Normalize Dolby Vision and remove HDR10+ metadata",
        }
        audio = plan.audio
        if audio.action == "transcode":
            audio_action = f"Add {audio.label or str(audio.codec).upper()}"
            audio_detail = f"Source track {(audio.source_ordinal or 0) + 1} · {(audio.bitrate or 0) // 1000} kbps"
        else:
            audio_action = "Use an existing compatible stream"
            audio_detail = audio.reason
        default_label = None
        if audio.default_ordinal is not None:
            if audio.action == "transcode" and audio.default_ordinal == len(plan.audio_labels):
                default_label = audio.label
            elif audio.default_ordinal < len(plan.audio_labels):
                default_label = plan.audio_labels[audio.default_ordinal]["label"]
        return {
            "video_action": video_labels.get(plan.video_action, plan.video_action.replace("_", " ").capitalize()),
            "video_reason": plan.video_reason,
            "hdr_mode": plan.hdr_mode,
            "audio_action": audio_action,
            "audio_detail": audio_detail,
            "default_audio": default_label or "Not selected",
            "subtitles": plan.subtitles,
            "output_name": Path(plan.output_path).name,
            "backup_name": Path(plan.backup_path).name,
            "evidence_name": Path(plan.evidence_path).name,
            "nfo_name": Path(plan.nfo_path).name if plan.nfo_path else None,
            "warnings": plan.warnings,
            "required_tools": plan.required_tools,
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        username = os.getenv("STREAMKEEPER_USER") or os.getenv("PLEX_CONVERT_USER", "")
        password = os.getenv("STREAMKEEPER_PASSWORD") or os.getenv("PLEX_CONVERT_PASSWORD", "")
        if bool(username) != bool(password):
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Authentication configuration is incomplete",
            )
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(authorize)])
    @app.get("/library", response_class=HTMLResponse, dependencies=[Depends(authorize)])
    def library_page(request: Request, assessment: str | None = None, library_id: int | None = None):
        if library_id is not None and not database.library(library_id):
            raise HTTPException(404, "Library not found")
        if assessment not in {None, "compatible", "warning", "action"}:
            assessment = None
        return page(
            request, "library", assets=database.assets(library_id, assessment),
            summary_counts=database.asset_counts(library_id),
            active_filter=assessment or "all", selected_library_id=library_id,
        )

    @app.get("/scans", response_class=HTMLResponse, dependencies=[Depends(authorize)])
    def scans_page(request: Request, selected: int | None = None):
        scans = database.scans()
        selected_scan = database.scan(selected) if selected else (scans[0] if scans else None)
        failures = database.scan_failures(selected_scan["id"]) if selected_scan else []
        events = database.scan_events(selected_scan["id"]) if selected_scan else []
        exclusions = database.scan_exclusions(selected_scan["id"]) if selected_scan else []
        return page(
            request, "scans", scans=scans, selected_scan=selected_scan,
            scan_failures=failures, scan_events=events, scan_exclusions=exclusions,
        )

    @app.get("/findings", response_class=HTMLResponse, dependencies=[Depends(authorize)])
    def findings_page(
        request: Request, status_filter: str = "open", selected: int | None = None,
        library_id: int | None = None,
    ):
        if library_id is not None and not database.library(library_id):
            raise HTTPException(404, "Library not found")
        if status_filter not in {"open", "new", "ignored", "resolved", "all"}:
            status_filter = "open"
        status_value = None if status_filter == "all" else status_filter
        findings = database.findings(status_value, library_id=library_id)
        selected_finding = next((item for item in findings if item["id"] == selected), findings[0] if findings else None)
        selected_asset = database.asset(selected_finding["asset_id"]) if selected_finding else None
        selected_probe_data = database.probe(selected_finding["asset_id"]) if selected_finding else None
        selected_probe = probe_presentation(selected_probe_data)
        selected_plan = None
        selected_plan_json = None
        if selected_asset and selected_probe_data and not selected_probe_data.get("error"):
            snapshot = ProbeSnapshot(**selected_probe_data)
            settings = database.settings()
            plan = build_plan(
                snapshot,
                media_kind=selected_asset["media_kind"],
                extra_type=selected_asset["extra_type"],
                fallback_language=settings["fallback_language"],
                network_ceiling_bps=int(settings["network_ceiling_bps"]),
            )
            selected_plan = plan_presentation(plan)
            selected_plan_json = json.dumps(plan.to_dict(), indent=2, ensure_ascii=False)
        return page(
            request, "findings", findings=findings, selected_finding=selected_finding,
            active_status=status_filter, selected_library_id=library_id,
            status_counts=database.finding_counts(library_id),
            selected_probe=selected_probe,
            selected_probe_json=json.dumps(selected_probe_data, indent=2, ensure_ascii=False) if selected_probe_data else None,
            selected_plan=selected_plan, selected_plan_json=selected_plan_json,
        )

    @app.get("/reports", response_class=HTMLResponse, dependencies=[Depends(authorize)])
    def reports_page(request: Request, report_kind: str = "compatibility", library_id: int | None = None):
        if library_id is not None and not database.library(library_id):
            raise HTTPException(404, "Library not found")
        if report_kind not in {"compatibility", "video", "network"}:
            report_kind = "compatibility"
        return page(
            request, "reports", report=database.report(library_id), report_kind=report_kind,
            selected_library_id=library_id,
        )

    @app.get("/settings", response_class=HTMLResponse, dependencies=[Depends(authorize)])
    def settings_page(request: Request, saved: int = 0):
        health = tool_health()
        versions = {
            name: details["version"] if details["available"] else None
            for name, details in health["tools"].items()
        }
        versions["Dolby Vision signaling"] = (
            "FFmpeg dovi_rpu filter" if health["dovi_rpu_ready"] else None
        )
        return page(
            request, "settings", settings=database.settings(), time_zones=time_zones,
            tool_versions=versions, saved=bool(saved),
        )

    @app.post("/settings", dependencies=[Depends(authorize)])
    def save_settings(
        network_ceiling_mbps: int = Form(900), retention_days: int = Form(90),
        fallback_language: str = Form("eng"), schedule: str = Form("manual"),
        time_zone: str = Form("local"),
        excluded_directories: str = Form(""), excluded_files: str = Form(""),
    ):
        updates = validated_settings({
            "network_ceiling_bps": network_ceiling_mbps * 1_000_000,
            "retention_days": retention_days,
            "fallback_language": fallback_language,
            "schedule": schedule,
            "time_zone": time_zone,
            "excluded_directories": excluded_directories,
            "excluded_files": excluded_files,
        })
        database.set_settings(updates)
        database.apply_retention(updates["retention_days"])
        return RedirectResponse("/settings?saved=1", status_code=303)

    @app.get("/api/libraries", dependencies=[Depends(authorize)])
    def api_libraries():
        return database.list_libraries()

    @app.post("/api/libraries", status_code=201, dependencies=[Depends(authorize)])
    async def api_add_library(request: Request):
        payload = await request.json()
        try:
            library_type = LibraryType(payload.get("library_type", "mixed"))
            path = str(payload["path"]).strip()
            if not path:
                raise ValueError("Library path is required")
            directory_patterns = normalize_patterns(
                payload.get("excluded_directories", []), "excluded_directories",
            )
            file_patterns = normalize_patterns(payload.get("excluded_files", []), "excluded_files")
            library_id = database.add_library(str(payload.get("name") or ""), path, library_type.value)
            database.update_library(
                library_id,
                enabled=1 if bool(payload.get("enabled", True)) else 0,
                excluded_directories=directory_patterns,
                excluded_files=file_patterns,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "That library path is already configured") from exc
        return database.library(library_id)

    @app.patch("/api/libraries/{library_id}", dependencies=[Depends(authorize)])
    async def api_update_library(library_id: int, request: Request):
        payload = await request.json()
        if "library_type" in payload:
            try:
                payload["library_type"] = LibraryType(payload["library_type"]).value
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        if "path" in payload and not str(payload["path"]).strip():
            raise HTTPException(422, "Library path is required")
        for key in ("excluded_directories", "excluded_files"):
            if key in payload:
                payload[key] = normalize_patterns(payload[key], key)
        try:
            library = database.update_library(library_id, **payload)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "That library path is already configured") from exc
        if not library:
            raise HTTPException(404, "Library not found")
        return library

    @app.get("/api/scans", dependencies=[Depends(authorize)])
    def api_scans():
        return database.scans()

    @app.post("/api/scans", status_code=202, dependencies=[Depends(authorize)])
    async def api_start_scan(request: Request):
        payload = await request.json()
        library_id = int(payload["library_id"])
        library = database.library(library_id)
        if not library:
            raise HTTPException(404, "Library not found")
        active_worker: ScanWorker | None = getattr(app.state, "worker", None)
        if active_worker is None:
            raise HTTPException(503, "Scanner is not running")
        scan_id = active_worker.enqueue(
            library_id=library_id,
            path=library["path"],
            library_type=LibraryType(library["library_type"]),
            deep=bool(payload.get("deep", False)),
        )
        scan = database.scan(scan_id)
        return {"id": scan_id, "status": scan["status"] if scan else "queued"}

    @app.get("/api/scans/{scan_id}", dependencies=[Depends(authorize)])
    def api_scan(scan_id: int):
        scan = database.scan(scan_id)
        if not scan:
            raise HTTPException(404, "Scan not found")
        scan["failures"] = database.scan_failures(scan_id)
        scan["events"] = database.scan_events(scan_id)
        scan["exclusions"] = database.scan_exclusions(scan_id)
        return scan

    @app.post("/api/scans/{scan_id}/cancel", dependencies=[Depends(authorize)])
    def api_cancel_scan(scan_id: int):
        active_worker: ScanWorker | None = getattr(app.state, "worker", None)
        if active_worker is None:
            raise HTTPException(503, "Scanner is not running")
        try:
            return active_worker.cancel(scan_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/scans/{scan_id}/snapshot.json", dependencies=[Depends(authorize)])
    def api_scan_snapshot(scan_id: int):
        scan = database.scan(scan_id)
        if not scan:
            raise HTTPException(404, "Scan not found")
        if scan["status"] not in {"completed", "completed_with_errors"}:
            raise HTTPException(409, "Only completed scans can be exported")
        try:
            rows = database.scan_snapshot(scan_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        filename = f"streamkeeper-scan-{scan_id}.json"
        return JSONResponse(
            rows,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/findings", dependencies=[Depends(authorize)])
    def api_findings(status_filter: str | None = None, library_id: int | None = None):
        if library_id is not None and not database.library(library_id):
            raise HTTPException(404, "Library not found")
        return database.findings(status_filter, library_id=library_id)

    @app.patch("/api/findings/{finding_id}", dependencies=[Depends(authorize)])
    async def api_update_finding(finding_id: int, request: Request):
        payload = await request.json()
        try:
            finding = database.set_finding_status(finding_id, payload.get("status", ""))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not finding:
            raise HTTPException(404, "Finding not found")
        return finding

    @app.get("/api/reports/compatibility", dependencies=[Depends(authorize)])
    def api_report(library_id: int | None = None):
        if library_id is not None and not database.library(library_id):
            raise HTTPException(404, "Library not found")
        return database.report(library_id)

    @app.get("/api/reports/compatibility.csv", dependencies=[Depends(authorize)])
    def api_report_csv(library_id: int | None = None):
        if library_id is not None and not database.library(library_id):
            raise HTTPException(404, "Library not found")
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["title", "relative_path", "video_codec", "audio", "peak_bitrate_bps", "assessment"])
        for asset in database.assets(library_id=library_id, limit=100000):
            writer.writerow([asset["title"], asset["relative_path"], asset["video_codec"], asset["audio_summary"], asset["peak_bitrate_bps"] or "", asset["assessment"]])
        return StreamingResponse(iter([stream.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=streamkeeper-compatibility.csv"})

    @app.get("/api/settings", dependencies=[Depends(authorize)])
    def api_settings():
        return database.settings()

    @app.get("/api/tools", dependencies=[Depends(authorize)])
    def api_tools():
        return tool_health()

    @app.put("/api/settings", dependencies=[Depends(authorize)])
    async def api_update_settings(request: Request):
        payload = await request.json()
        updates = validated_settings(payload)
        database.set_settings(updates)
        if "retention_days" in updates:
            database.apply_retention(updates["retention_days"])
        return database.settings()

    @app.get("/api/assets/{asset_id}/probe", dependencies=[Depends(authorize)])
    def api_probe(asset_id: int):
        snapshot = database.probe(asset_id)
        if not snapshot:
            raise HTTPException(404, "Probe snapshot not found")
        return snapshot

    @app.get("/api/assets/{asset_id}/plan", dependencies=[Depends(authorize)])
    def api_plan(asset_id: int):
        asset = database.asset(asset_id)
        snapshot_data = database.probe(asset_id)
        if not asset or not snapshot_data:
            raise HTTPException(404, "Asset or probe snapshot not found")
        if snapshot_data.get("error"):
            raise HTTPException(409, "A dry-run plan requires a successful probe")
        snapshot = ProbeSnapshot(**snapshot_data)
        settings = database.settings()
        plan = build_plan(
            snapshot, media_kind=asset["media_kind"], extra_type=asset["extra_type"],
            fallback_language=settings["fallback_language"],
            network_ceiling_bps=int(settings["network_ceiling_bps"]),
        )
        return JSONResponse(plan.to_dict())

    return app


app = create_app()


def run() -> None:
    uvicorn.run("streamkeeper.web:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")), reload=False)
