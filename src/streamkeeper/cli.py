from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .discovery import classify, discover
from .executor import ParityGateError, execute
from .models import LibraryType
from .planner import build_plan, render_text
from .policy import findings_for
from .probe import ProbeError, probe_file
from .shadow import compare_scans, load_scan, render_comparison


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="streamkeeper", description="Scan and plan Plex media compatibility work.")
    subcommands = command.add_subparsers(dest="command", required=True)

    scan = subcommands.add_parser("scan", help="Scan a file or directory without modifying it")
    scan.add_argument("--path", required=True)
    scan.add_argument("--library-type", choices=[item.value for item in LibraryType], default="mixed")
    scan.add_argument("--deep", action="store_true", help="Measure packet-level peak bitrate")
    scan.add_argument("--format", choices=["text", "json"], default="text")

    plan = subcommands.add_parser("plan", help="Show the conversion plan for one media file")
    plan.add_argument("--path", required=True)
    plan.add_argument("--library-type", choices=[item.value for item in LibraryType], default="mixed")
    plan.add_argument("--format", choices=["text", "json"], default="text")

    convert = subcommands.add_parser("convert", help="Dry-run conversion plans; execution remains parity-gated")
    convert.add_argument("--path", required=True)
    convert.add_argument("--library-type", choices=[item.value for item in LibraryType], default="mixed")
    convert.add_argument("-d", "--dry-run", action="store_true")
    convert.add_argument("--format", choices=["text", "json"], default="text")

    compare = subcommands.add_parser("compare-scans", help="Compare two read-only scan JSON snapshots")
    compare.add_argument("--reference", required=True, help="Previously captured scan JSON")
    compare.add_argument("--candidate", required=True, help="Candidate scan JSON")
    compare.add_argument("--format", choices=["text", "json"], default="text")
    return command


def make_plan(path: Path, root: Path, library_type: LibraryType):
    media_kind, extra_type = classify(path, root, library_type)
    return build_plan(probe_file(path), media_kind=media_kind, extra_type=extra_type)


def run_scan(args: argparse.Namespace) -> int:
    library_type = LibraryType(args.library_type)
    result = []
    failed = 0
    for asset in discover(args.path, library_type):
        try:
            snapshot = probe_file(asset.path, deep=args.deep)
            findings = findings_for(snapshot)
            row = {"asset": asset.to_dict(), "probe": snapshot.to_dict(), "findings": [item.to_dict() for item in findings]}
            result.append(row)
            if args.format == "text":
                assessment = "compatible" if not findings else f"{len(findings)} finding(s)"
                print(f"{asset.relative_path}: {assessment}")
        except (ProbeError, OSError, ValueError) as exc:
            failed += 1
            result.append({"asset": asset.to_dict(), "error": str(exc)})
            if args.format == "text":
                print(f"{asset.relative_path}: probe failed: {exc}", file=sys.stderr)
    if args.format == "json":
        print(json.dumps(result, indent=2))
    if not result:
        print("No supported media files found.", file=sys.stderr)
        return 1
    return 1 if failed else 0


def plans_for(args: argparse.Namespace):
    target = Path(args.path).expanduser().resolve()
    library_type = LibraryType(args.library_type)
    root = target.parent if target.is_file() else target
    for asset in discover(target, library_type):
        yield make_plan(Path(asset.path), root, library_type)


def emit_plans(plans: list, output_format: str) -> None:
    if output_format == "json":
        payload = plans[0].to_dict() if len(plans) == 1 else [item.to_dict() for item in plans]
        print(json.dumps(payload, indent=2))
    else:
        print("\n\n".join(render_text(item) for item in plans))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "scan":
            return run_scan(args)
        if args.command == "compare-scans":
            comparison = compare_scans(load_scan(args.reference), load_scan(args.candidate))
            if args.format == "json":
                print(json.dumps(comparison, indent=2))
            else:
                print(render_comparison(comparison))
            return 0 if comparison["matches"] else 1
        plans = list(plans_for(args))
        if not plans:
            print("No supported media files found.", file=sys.stderr)
            return 1
        if args.command == "plan" or args.dry_run:
            emit_plans(plans, args.format)
            return 0
        for plan in plans:
            execute(plan)
        return 0
    except (OSError, ProbeError, ParityGateError, ValueError) as exc:
        print(f"streamkeeper: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
