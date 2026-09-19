from pathlib import Path

import pytest

from streamkeeper.evidence import render_evidence
from streamkeeper.models import ProbeSnapshot
from streamkeeper.nfo import validate_nfo
from streamkeeper.planner import build_plan
from streamkeeper.transaction import InstallArtifacts, InstallError, install_artifacts, install_validated
from streamkeeper.validation import validate_output


def basic_snapshot(path: str, *, audio_count: int = 1, duration: str = "10.0") -> ProbeSnapshot:
    streams = [{"codec_type": "video", "codec_name": "h264", "color_transfer": "bt709", "disposition": {}}]
    streams.extend({"codec_type": "audio", "codec_name": "aac", "channels": 2, "disposition": {}} for _ in range(audio_count))
    return ProbeSnapshot(path, "2026-01-01T00:00:00Z", {"duration": duration}, streams)


def test_validation_rejects_missing_audio_and_timing_drift():
    source = basic_snapshot("/media/source.mkv", audio_count=2)
    output = basic_snapshot("/media/output.mkv", audio_count=1, duration="10.2")
    result = validate_output(source, output)
    assert result["passed"] is False
    assert len(result["errors"]) == 2


def test_transaction_refuses_existing_backup_without_source_mutation(tmp_path: Path):
    source = tmp_path / "Movie.mkv"
    staged = tmp_path / ".Movie.partial.mkv"
    backup = tmp_path / "Movie Original.mkv"
    source.write_bytes(b"source")
    staged.write_bytes(b"output")
    backup.write_bytes(b"existing")
    with pytest.raises(InstallError):
        install_validated(source, staged, backup)
    assert source.read_bytes() == b"source"
    assert staged.read_bytes() == b"output"


def test_evidence_contains_plan_probes_commands_and_versions():
    source = basic_snapshot("/media/Movie.mkv")
    plan = build_plan(source)
    rendered = render_evidence(plan, source, source, plan.normalized_commands, {"ffmpeg": "8.0"}, {"passed": True})
    assert '"source_probe"' in rendered
    assert '"output_probe"' in rendered
    assert '"commands"' in rendered
    assert '"ffmpeg": "8.0"' in rendered


def artifact_set(tmp_path: Path) -> InstallArtifacts:
    source = tmp_path / "Movie.mkv"
    staged_output = tmp_path / ".Movie.output.mkv"
    staged_evidence = tmp_path / ".Movie.evidence.txt"
    nfo = tmp_path / "Movie.nfo"
    staged_nfo = tmp_path / ".Movie.nfo.new"
    source.write_bytes(b"original media")
    staged_output.write_bytes(b"validated output")
    staged_evidence.write_text("evidence", encoding="utf-8")
    nfo.write_text("<movie><title>Original</title></movie>", encoding="utf-8")
    staged_nfo.write_text("<movie><title>Updated</title></movie>", encoding="utf-8")
    return InstallArtifacts(
        source=source,
        staged_output=staged_output,
        output=source,
        backup=tmp_path / "Movie Original.mkv",
        staged_evidence=staged_evidence,
        evidence=tmp_path / "Movie.conversion.txt",
        staged_nfo=staged_nfo,
        nfo=nfo,
    )


@pytest.mark.parametrize(
    "failure_step",
    ["original_renamed", "output_installed", "evidence_installed", "nfo_installed"],
)
def test_artifact_install_rolls_back_every_failure_boundary(tmp_path: Path, failure_step: str):
    artifacts = artifact_set(tmp_path)

    def fail_at(step: str) -> None:
        if step == failure_step:
            raise RuntimeError(f"injected failure after {step}")

    with pytest.raises(InstallError, match="was rolled back"):
        install_artifacts(artifacts, checkpoint=fail_at)

    assert artifacts.source.read_bytes() == b"original media"
    assert not artifacts.backup.exists()
    assert artifacts.staged_output.read_bytes() == b"validated output"
    assert not artifacts.evidence.exists()
    assert artifacts.staged_evidence.read_text(encoding="utf-8") == "evidence"
    assert artifacts.nfo.read_text(encoding="utf-8") == "<movie><title>Original</title></movie>"
    assert artifacts.staged_nfo.read_text(encoding="utf-8") == "<movie><title>Updated</title></movie>"


def test_artifact_install_commits_validated_media_evidence_and_nfo(tmp_path: Path):
    artifacts = artifact_set(tmp_path)
    install_artifacts(artifacts)

    assert artifacts.source.read_bytes() == b"validated output"
    assert artifacts.backup.read_bytes() == b"original media"
    assert artifacts.evidence.read_text(encoding="utf-8") == "evidence"
    assert artifacts.nfo.read_text(encoding="utf-8") == "<movie><title>Updated</title></movie>"
    assert not artifacts.staged_output.exists()
    assert not artifacts.staged_evidence.exists()
    assert not artifacts.staged_nfo.exists()


@pytest.mark.parametrize(
    "interrupt_step",
    ["original_renamed", "output_installed", "evidence_installed", "nfo_installed"],
)
def test_artifact_install_rolls_back_keyboard_interrupt_at_every_boundary(
    tmp_path: Path,
    interrupt_step: str,
):
    artifacts = artifact_set(tmp_path)

    def interrupt_at(step: str) -> None:
        if step == interrupt_step:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        install_artifacts(artifacts, checkpoint=interrupt_at)

    assert artifacts.source.read_bytes() == b"original media"
    assert not artifacts.backup.exists()
    assert artifacts.staged_output.read_bytes() == b"validated output"
    assert artifacts.staged_evidence.read_text(encoding="utf-8") == "evidence"
    assert artifacts.nfo.read_text(encoding="utf-8") == "<movie><title>Original</title></movie>"
    assert artifacts.staged_nfo.read_text(encoding="utf-8") == "<movie><title>Updated</title></movie>"
