from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class InstallError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class InstallArtifacts:
    source: Path
    staged_output: Path
    output: Path
    backup: Path
    staged_evidence: Path
    evidence: Path
    staged_nfo: Path | None = None
    nfo: Path | None = None


def install_validated(source: Path, staged_output: Path, backup: Path) -> None:
    """Install a validated result, restoring the source name if installation fails."""
    if not source.is_file() or not staged_output.is_file():
        raise InstallError("source and validated staged output must both exist")
    if backup.exists():
        raise InstallError(f"backup already exists: {backup}")
    output = source.with_suffix(".mkv")
    source_mode = source.stat().st_mode
    source.rename(backup)
    installed = False
    try:
        staged_output.rename(output)
        installed = True
        os.chmod(output, source_mode)
    except Exception as exc:
        if installed and output.exists() and not staged_output.exists():
            output.rename(staged_output)
        if not source.exists() and backup.exists():
            backup.rename(source)
        raise InstallError(f"installation failed and was rolled back: {exc}") from exc


def install_artifacts(
    artifacts: InstallArtifacts,
    *,
    checkpoint: Callable[[str], None] | None = None,
) -> None:
    """Install validated media and evidence, restoring every original on failure."""
    checkpoint = checkpoint or (lambda _step: None)
    _preflight(artifacts)
    source_mode = artifacts.source.stat().st_mode
    nfo_rollback = artifacts.nfo.with_name(f".{artifacts.nfo.name}.streamkeeper.rollback") if artifacts.nfo else None
    source_moved = output_installed = evidence_installed = nfo_moved = nfo_installed = False
    try:
        artifacts.source.rename(artifacts.backup)
        source_moved = True
        checkpoint("original_renamed")

        artifacts.staged_output.rename(artifacts.output)
        output_installed = True
        os.chmod(artifacts.output, source_mode)
        checkpoint("output_installed")

        artifacts.staged_evidence.rename(artifacts.evidence)
        evidence_installed = True
        checkpoint("evidence_installed")

        if artifacts.nfo and artifacts.staged_nfo and nfo_rollback:
            artifacts.nfo.rename(nfo_rollback)
            nfo_moved = True
            artifacts.staged_nfo.rename(artifacts.nfo)
            nfo_installed = True
            checkpoint("nfo_installed")
            nfo_rollback.unlink()
    except BaseException as exc:
        rollback_errors = _rollback(
            artifacts,
            nfo_rollback=nfo_rollback,
            source_moved=source_moved,
            output_installed=output_installed,
            evidence_installed=evidence_installed,
            nfo_moved=nfo_moved,
            nfo_installed=nfo_installed,
        )
        if isinstance(exc, (KeyboardInterrupt, SystemExit)) and not rollback_errors:
            raise
        detail = f"installation failed and was rolled back: {exc}"
        if rollback_errors:
            detail += f"; rollback errors: {'; '.join(rollback_errors)}"
        raise InstallError(detail) from exc


def _preflight(artifacts: InstallArtifacts) -> None:
    required = [artifacts.source, artifacts.staged_output, artifacts.staged_evidence]
    if artifacts.staged_nfo:
        required.append(artifacts.staged_nfo)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise InstallError(f"required staged artifact is missing: {', '.join(missing)}")
    if artifacts.backup.exists():
        raise InstallError(f"backup already exists: {artifacts.backup}")
    for target in (artifacts.output, artifacts.evidence):
        if target != artifacts.source and target.exists():
            raise InstallError(f"installation target already exists: {target}")
    if bool(artifacts.nfo) != bool(artifacts.staged_nfo):
        raise InstallError("NFO source and staged replacement must be supplied together")
    if artifacts.nfo and not artifacts.nfo.is_file():
        raise InstallError(f"NFO source does not exist: {artifacts.nfo}")
    if artifacts.nfo:
        rollback = artifacts.nfo.with_name(f".{artifacts.nfo.name}.streamkeeper.rollback")
        if rollback.exists():
            raise InstallError(f"NFO rollback path already exists: {rollback}")


def _rollback(
    artifacts: InstallArtifacts,
    *,
    nfo_rollback: Path | None,
    source_moved: bool,
    output_installed: bool,
    evidence_installed: bool,
    nfo_moved: bool,
    nfo_installed: bool,
) -> list[str]:
    errors: list[str] = []

    def attempt(label: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except Exception as exc:  # pragma: no cover - catastrophic filesystem failure
            errors.append(f"{label}: {exc}")

    if nfo_installed and artifacts.nfo and artifacts.staged_nfo and artifacts.nfo.exists():
        attempt("remove replacement NFO", lambda: artifacts.nfo.rename(artifacts.staged_nfo))
    if nfo_moved and nfo_rollback and artifacts.nfo and nfo_rollback.exists():
        attempt("restore original NFO", lambda: nfo_rollback.rename(artifacts.nfo))
    if evidence_installed and artifacts.evidence.exists() and not artifacts.staged_evidence.exists():
        attempt("uninstall evidence", lambda: artifacts.evidence.rename(artifacts.staged_evidence))
    if output_installed and artifacts.output.exists() and not artifacts.staged_output.exists():
        attempt("uninstall output", lambda: artifacts.output.rename(artifacts.staged_output))
    if source_moved and artifacts.backup.exists() and not artifacts.source.exists():
        attempt("restore original media", lambda: artifacts.backup.rename(artifacts.source))
    return errors
