#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from streamkeeper.models import ProbeSnapshot
from streamkeeper.parity import compare_decisions, normalize_python_plan, parse_legacy_dry_run
from streamkeeper.planner import build_plan


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare a sanitized ffprobe snapshot with frozen-shell dry-run output."
    )
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--legacy-output", type=Path, required=True)
    arguments = parser.parse_args()

    snapshot = ProbeSnapshot(**json.loads(arguments.probe.read_text()))
    reference = parse_legacy_dry_run(arguments.legacy_output.read_text())
    candidate = normalize_python_plan(build_plan(snapshot))
    differences = compare_decisions(reference, candidate)
    result = {
        "status": "match" if not differences else "different",
        "differences": differences,
        "reference": reference.to_dict(),
        "candidate": candidate.to_dict(),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if differences else 0


if __name__ == "__main__":
    raise SystemExit(main())
