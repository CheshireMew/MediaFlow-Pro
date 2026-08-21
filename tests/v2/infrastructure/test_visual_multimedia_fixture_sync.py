from __future__ import annotations

import json
from pathlib import Path

import scripts.sync_visual_multimedia_fixture as fixture_sync


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
