from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import scripts.sync_visual_multimedia_fixture as fixture_sync


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_fixture_sync_requires_a_committed_producer_revision(tmp_path: Path) -> None:
    producer = tmp_path / "producer"
    producer.mkdir()
    _git(producer, "init")
    source = producer / "source.json"
    source.write_text('{"version":1}\n', encoding="utf-8")
    _git(producer, "add", "source.json")
    _git(
        producer,
        "-c",
        "user.name=MediaFlow Test",
        "-c",
        "user.email=mediaflow@example.invalid",
        "commit",
        "-m",
        "Add producer source",
    )

    revision = fixture_sync.clean_producer_revision(producer)

    assert len(revision) == 40
    assert all(character in "0123456789abcdef" for character in revision)
    source.write_text('{"version":2}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="uncommitted visual-multimedia worktree"):
        fixture_sync.clean_producer_revision(producer)


def test_fixture_sync_repairs_payload_when_origin_metadata_is_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "producer"
    source.mkdir()
    payload = source / "payload.json"
    payload.write_text('{"value":"producer"}\n', encoding="utf-8")
    destination = tmp_path / "consumer"
    monkeypatch.setattr(fixture_sync, "test_run_root", lambda: tmp_path / "runs")

    expected = fixture_sync._sync_package_files(
        source,
        destination,
        files=(payload,),
        origin_fields={"producer": "test"},
    )
    (destination / "payload.json").write_text(
        '{"value":"modified"}\n',
        encoding="utf-8",
    )

    actual = fixture_sync._sync_package_files(
        source,
        destination,
        files=(payload,),
        origin_fields={"producer": "test"},
    )

    assert actual == expected
    assert (destination / "payload.json").read_bytes() == payload.read_bytes()
    origin_bytes = (destination / "fixture-origin.json").read_bytes()
    assert b"\r\n" not in origin_bytes
    assert json.loads(origin_bytes) == expected
