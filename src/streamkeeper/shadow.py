from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_scan(path: str | Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid scan JSON in {path}: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError(f"Scan JSON in {path} must contain a list")
    if not all(isinstance(row, dict) and isinstance(row.get("asset"), dict) for row in payload):
        raise ValueError(f"Scan JSON in {path} contains an invalid row")
    return payload


def _stream_signature(stream: dict[str, Any]) -> dict[str, Any]:
    tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
    signature: dict[str, Any] = {
        "codec": stream.get("codec_name"),
        "language": tags.get("language"),
    }
    if stream.get("codec_type") == "video":
        signature.update({
            "profile": stream.get("profile"),
            "width": stream.get("width"),
            "height": stream.get("height"),
            "pixel_format": stream.get("pix_fmt"),
            "transfer": stream.get("color_transfer"),
        })
    elif stream.get("codec_type") == "audio":
        signature.update({
            "channels": stream.get("channels"),
            "layout": stream.get("channel_layout"),
        })
    return signature


def normalize_scan(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Reduce scan output to stable, decision-relevant fields keyed by relative path."""
    inventory: dict[str, dict[str, Any]] = {}
    for row in rows:
        asset = row["asset"]
        relative_path = asset.get("relative_path")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("Scan row is missing asset.relative_path")
        if relative_path in inventory:
            raise ValueError(f"Scan contains duplicate relative path: {relative_path}")
        probe = row.get("probe") if isinstance(row.get("probe"), dict) else {}
        streams = probe.get("streams") if isinstance(probe.get("streams"), list) else []
        by_type: dict[str, list[dict[str, Any]]] = {"video": [], "audio": [], "subtitle": []}
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            stream_type = stream.get("codec_type")
            if stream_type in by_type:
                by_type[stream_type].append(_stream_signature(stream))
        findings = row.get("findings") if isinstance(row.get("findings"), list) else []
        finding_rules = sorted(
            str(finding.get("rule_id"))
            for finding in findings
            if isinstance(finding, dict) and finding.get("rule_id")
        )
        inventory[relative_path] = {
            "title": asset.get("title"),
            "media_kind": asset.get("media_kind"),
            "extra_type": asset.get("extra_type"),
            "video": by_type["video"],
            "audio": by_type["audio"],
            "subtitles": by_type["subtitle"],
            "finding_rules": finding_rules,
            "probe_failed": bool(row.get("error") or probe.get("error")),
        }
    return inventory


def compare_scans(reference: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> dict[str, Any]:
    before = normalize_scan(reference)
    after = normalize_scan(candidate)
    before_paths = set(before)
    after_paths = set(after)
    changed: dict[str, dict[str, Any]] = {}
    for path in sorted(before_paths & after_paths, key=str.casefold):
        differences = {
            field: {"reference": before[path][field], "candidate": after[path][field]}
            for field in before[path]
            if before[path][field] != after[path][field]
        }
        if differences:
            changed[path] = differences
    missing = sorted(before_paths - after_paths, key=str.casefold)
    added = sorted(after_paths - before_paths, key=str.casefold)
    return {
        "matches": not (missing or added or changed),
        "summary": {
            "reference_files": len(before),
            "candidate_files": len(after),
            "missing_files": len(missing),
            "added_files": len(added),
            "changed_files": len(changed),
        },
        "missing": missing,
        "added": added,
        "changed": changed,
    }


def render_comparison(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "Shadow scan comparison",
        f"Reference: {summary['reference_files']} files",
        f"Candidate: {summary['candidate_files']} files",
        (f"Differences: {summary['missing_files']} missing, {summary['added_files']} added, "
         f"{summary['changed_files']} changed"),
    ]
    if result["matches"]:
        lines.append("Result: match")
        return "\n".join(lines)
    lines.append("Result: review required")
    for path in result["missing"]:
        lines.append(f"Missing: {path}")
    for path in result["added"]:
        lines.append(f"Added: {path}")
    for path, fields in result["changed"].items():
        lines.append(f"Changed: {path} ({', '.join(fields)})")
    return "\n".join(lines)
