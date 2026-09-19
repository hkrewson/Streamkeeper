from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .models import ConversionPlan, ProbeSnapshot


def render_evidence(plan: ConversionPlan, source: ProbeSnapshot, output: ProbeSnapshot | None, commands: list[list[str]], versions: dict[str, str], validation: dict[str, Any]) -> str:
    record = {
        "schema": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tools": versions,
        "plan": plan.to_dict(),
        "commands": commands,
        "source_probe": source.to_dict(),
        "output_probe": output.to_dict() if output else None,
        "validation": validation,
    }
    return json.dumps(record, indent=2, ensure_ascii=False) + "\n"
