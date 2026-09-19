from __future__ import annotations

import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import streamkeeper.executor as executor_module
from streamkeeper.executor import ConversionExecutionError, execute_controlled, file_sha256
from streamkeeper.planner import build_plan
from streamkeeper.probe import probe_file


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg integration tools are unavailable",
)


def fixture_plan(tmp_path: Path):
    source = tmp_path / "Fixture.mkv"
    nfo = tmp_path / "Fixture.nfo"
    nfo.write_text("<movie><title>Fixture</title><uniqueid>test-id</uniqueid></movie>", encoding="utf-8")
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=1",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "flac", "-ac", "2",
            "-metadata:s:a:0", "language=eng",
            "-f", "matroska", str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return source, nfo, build_plan(probe_file(source))


def test_controlled_executor_completes_evidence_nfo_and_transaction(tmp_path: Path):
    source, nfo, plan = fixture_plan(tmp_path)
    original_hash = file_sha256(source)

    receipt = execute_controlled(plan, minimum_free_bytes=0)

    assert receipt.status == "validated"
    assert receipt.source_sha256_before == original_hash
    assert source.is_file()
    assert Path(plan.backup_path).is_file()
    assert file_sha256(plan.backup_path) == original_hash
    evidence = json.loads(Path(plan.evidence_path).read_text(encoding="utf-8"))
    assert evidence["validation"]["passed"] is True
    assert evidence["validation"]["copied_stream_hashes"]["passed"] is True
    root = ET.parse(nfo).getroot()
    assert root.findtext("title") == "Fixture"
    assert root.findtext("uniqueid") == "test-id"
    assert root.find("plexconvert").attrib["evidence"] == "Fixture.conversion.txt"


@pytest.mark.parametrize("failure_step", ["probed", "encoded", "validated", "evidence_prepared"])
def test_controlled_executor_cleans_staging_and_preserves_source_before_install(
    tmp_path: Path,
    failure_step: str,
):
    source, nfo, plan = fixture_plan(tmp_path)
    original_hash = file_sha256(source)
    original_nfo = nfo.read_bytes()

    def fail_at(step: str) -> None:
        if step == failure_step:
            raise RuntimeError(f"injected failure after {step}")

    with pytest.raises(ConversionExecutionError, match="injected failure"):
        execute_controlled(plan, checkpoint=fail_at, minimum_free_bytes=0)

    assert file_sha256(source) == original_hash
    assert nfo.read_bytes() == original_nfo
    assert not Path(plan.backup_path).exists()
    assert not Path(plan.evidence_path).exists()
    assert not list(tmp_path.glob(".*streamkeeper*"))
    assert not list(tmp_path.glob("*.staged"))


def test_controlled_executor_refuses_insufficient_space_without_encoding(tmp_path: Path):
    source, _nfo, plan = fixture_plan(tmp_path)
    original_hash = file_sha256(source)
    with pytest.raises(ConversionExecutionError, match="insufficient free space"):
        execute_controlled(plan, minimum_free_bytes=10**30)
    assert file_sha256(source) == original_hash
    assert not Path(plan.backup_path).exists()


def test_controlled_executor_refuses_an_existing_lock(tmp_path: Path):
    source, _nfo, plan = fixture_plan(tmp_path)
    lock = source.with_name(f".{source.name}.streamkeeper.lock")
    lock.write_text("another process\n", encoding="utf-8")
    with pytest.raises(ConversionExecutionError, match="another conversion owns the lock"):
        execute_controlled(plan, minimum_free_bytes=0)
    assert source.is_file()
    assert lock.read_text(encoding="utf-8") == "another process\n"


def test_controlled_executor_never_removes_a_preexisting_staged_artifact(tmp_path: Path):
    source, _nfo, plan = fixture_plan(tmp_path)
    staged = Path(plan.normalized_commands[-1][-1])
    staged.write_bytes(b"belongs to another interrupted run")
    with pytest.raises(ConversionExecutionError, match="staged artifact already exists"):
        execute_controlled(plan, minimum_free_bytes=0)
    assert staged.read_bytes() == b"belongs to another interrupted run"
    assert source.is_file()


def test_controlled_executor_cleans_owned_files_after_keyboard_interrupt(tmp_path: Path):
    source, nfo, plan = fixture_plan(tmp_path)
    original_hash = file_sha256(source)
    staged = Path(plan.normalized_commands[-1][-1])

    def interrupted_runner(_command: list[str]) -> None:
        staged.write_bytes(b"partial encode")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute_controlled(plan, command_runner=interrupted_runner, minimum_free_bytes=0)

    assert file_sha256(source) == original_hash
    assert nfo.read_text(encoding="utf-8").startswith("<movie>")
    assert not staged.exists()
    assert not source.with_name(f".{source.name}.streamkeeper.lock").exists()


def test_controlled_executor_preserves_source_after_permission_failure(tmp_path: Path):
    source, _nfo, plan = fixture_plan(tmp_path)
    original_hash = file_sha256(source)

    def denied_runner(_command: list[str]) -> None:
        raise PermissionError("permission denied on media mount")

    with pytest.raises(ConversionExecutionError, match="permission denied"):
        execute_controlled(plan, command_runner=denied_runner, minimum_free_bytes=0)

    assert file_sha256(source) == original_hash
    assert not Path(plan.backup_path).exists()
    assert not Path(plan.evidence_path).exists()


def test_controlled_executor_preserves_source_when_mount_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, _nfo, plan = fixture_plan(tmp_path)
    original_hash = file_sha256(source)

    def disconnected(_path: Path):
        raise OSError("media mount disconnected")

    monkeypatch.setattr(executor_module.shutil, "disk_usage", disconnected)
    with pytest.raises(ConversionExecutionError, match="media mount disconnected"):
        execute_controlled(plan, minimum_free_bytes=0)

    assert file_sha256(source) == original_hash
    assert not Path(plan.backup_path).exists()
