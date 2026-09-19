from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .evidence import render_evidence
from .models import ConversionPlan, ConversionReceipt
from .nfo import update_nfo, validate_nfo
from .probe import probe_file, require_tool, tool_version
from .transaction import InstallArtifacts, install_artifacts
from .validation import validate_copied_stream_hashes, validate_output


class ParityGateError(RuntimeError):
    pass


class ConversionExecutionError(RuntimeError):
    pass


def execute(_plan: ConversionPlan) -> None:
    raise ParityGateError(
        "Conversion execution is locked until the shell-to-Python parity and controlled-conversion gates are accepted."
    )


def execute_controlled(
    plan: ConversionPlan,
    *,
    checkpoint: Callable[[str], None] | None = None,
    command_runner: Callable[[list[str]], None] | None = None,
    minimum_free_bytes: int | None = None,
) -> ConversionReceipt:
    """Exercise the complete pipeline in controlled tests while the public gate stays locked."""
    checkpoint = checkpoint or (lambda _step: None)
    command_runner = command_runner or _run_command
    source = Path(plan.source_path)
    output = Path(plan.output_path)
    backup = Path(plan.backup_path)
    evidence = Path(plan.evidence_path)
    nfo = Path(plan.nfo_path) if plan.nfo_path else None
    lock = source.with_name(f".{source.name}.streamkeeper.lock")
    staged_output = Path(plan.normalized_commands[-1][-1])
    staged_evidence = evidence.with_name(f".{evidence.name}.staged")
    staged_nfo = nfo.with_name(f".{nfo.name}.staged") if nfo else None
    temporary_paths = _command_temporary_paths(plan.normalized_commands, source.parent)
    temporary_paths.update({staged_output, staged_evidence})
    if staged_nfo:
        temporary_paths.add(staged_nfo)
    preexisting_temporary_paths = {path for path in temporary_paths if path.exists()}

    started_at = _now()
    source_hash_before: str | None = None
    lock_fd: int | None = None
    try:
        _preflight(
            plan,
            source,
            output,
            backup,
            evidence,
            nfo,
            temporary_paths,
        )
        lock_fd = _create_lock(lock)
        for tool in plan.required_tools:
            require_tool(tool)
        source_hash_before = file_sha256(source)
        source_probe = probe_file(source)
        checkpoint("probed")

        required_free = minimum_free_bytes if minimum_free_bytes is not None else source.stat().st_size * 3 + 64 * 1024 * 1024
        available = shutil.disk_usage(source.parent).free
        if available < required_free:
            raise ConversionExecutionError(
                f"insufficient free space: {available} bytes available; {required_free} required"
            )

        for command in plan.normalized_commands:
            command_runner(command)
        if not staged_output.is_file():
            raise ConversionExecutionError("planned commands did not create the staged output")
        checkpoint("encoded")

        output_probe = probe_file(staged_output)
        validation = validate_output(source_probe, output_probe, plan)
        copied_hashes = validate_copied_stream_hashes(source, staged_output, source_probe, plan)
        validation["copied_stream_hashes"] = copied_hashes
        if not validation["passed"] or not copied_hashes["passed"]:
            errors = [*validation["errors"], *copied_hashes["errors"]]
            raise ConversionExecutionError(f"output validation failed: {'; '.join(errors)}")
        checkpoint("validated")

        versions = {tool: tool_version(tool) for tool in plan.required_tools}
        staged_evidence.write_text(
            render_evidence(
                plan,
                source_probe,
                output_probe,
                plan.normalized_commands,
                versions,
                validation,
            ),
            encoding="utf-8",
        )
        os.chmod(staged_evidence, 0o644)
        if nfo and staged_nfo:
            nfo_data = update_nfo(
                nfo,
                evidence.name,
                f"Streamkeeper validated {plan.video_action}; evidence={evidence.name}",
            )
            validate_nfo(nfo_data)
            staged_nfo.write_bytes(nfo_data)
        checkpoint("evidence_prepared")

        artifacts = InstallArtifacts(
            source=source,
            staged_output=staged_output,
            output=output,
            backup=backup,
            staged_evidence=staged_evidence,
            evidence=evidence,
            staged_nfo=staged_nfo,
            nfo=nfo,
        )
        install_artifacts(
            artifacts,
            checkpoint=lambda step: checkpoint(f"install_{step}"),
        )
        return ConversionReceipt(
            source_path=str(source),
            output_path=str(output),
            status="validated",
            started_at=started_at,
            finished_at=_now(),
            source_sha256_before=source_hash_before,
            source_sha256_after=file_sha256(output),
            plan=plan,
            validation=validation,
        )
    except (ParityGateError, ConversionExecutionError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ConversionExecutionError(str(exc)) from exc
    finally:
        for path in temporary_paths:
            if path not in preexisting_temporary_paths and path.is_file():
                path.unlink()
        if lock_fd is not None:
            os.close(lock_fd)
            if lock.exists():
                lock.unlink()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_command(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise ConversionExecutionError(detail[-4000:])


def _preflight(
    plan: ConversionPlan,
    source: Path,
    output: Path,
    backup: Path,
    evidence: Path,
    nfo: Path | None,
    temporary_paths: set[Path],
) -> None:
    if not source.is_file():
        raise ConversionExecutionError(f"source does not exist: {source}")
    if not plan.normalized_commands:
        raise ConversionExecutionError("conversion plan has no command vectors")
    if backup.exists():
        raise ConversionExecutionError(f"backup already exists: {backup}")
    if output != source and output.exists():
        raise ConversionExecutionError(f"output already exists: {output}")
    if evidence.exists():
        raise ConversionExecutionError(f"evidence already exists: {evidence}")
    existing_temporary = next((path for path in temporary_paths if path.exists()), None)
    if existing_temporary:
        raise ConversionExecutionError(f"staged artifact already exists: {existing_temporary}")
    if nfo and not nfo.is_file():
        raise ConversionExecutionError(f"NFO does not exist: {nfo}")


def _create_lock(path: Path) -> int:
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ConversionExecutionError(f"another conversion owns the lock: {path}") from exc
    os.write(descriptor, f"{os.getpid()}\n".encode())
    return descriptor


def _command_temporary_paths(commands: list[list[str]], directory: Path) -> set[Path]:
    paths: set[Path] = set()
    for command in commands:
        if command and command[0] == "ffmpeg":
            candidate = Path(command[-1])
            if candidate.parent == directory and candidate.name.startswith("."):
                paths.add(candidate)
        for index, argument in enumerate(command[:-1]):
            if argument in {"-o", "-attach"}:
                candidate = Path(command[index + 1])
                if candidate.parent == directory and candidate.name.startswith("."):
                    paths.add(candidate)
    return paths


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
