from __future__ import annotations

import json
from pathlib import Path

import pytest

from mediaflow.automation.request_factory import AutomationRequestFactory
from mediaflow.domain.collaboration import ActorIdentity


def test_request_factory_validates_and_serializes_one_canonical_v4_request(
    tmp_path: Path,
) -> None:
    factory = AutomationRequestFactory()
    actor = ActorIdentity(kind="human", id="desktop-user", name="Editor")
    arguments = {
        "output_path": str(tmp_path / "diagnostics.zip"),
        "task_ids": [],
        "overwrite": False,
    }

    first = factory.create(
        "diagnostics.bundle.create",
        arguments,
        project_path=tmp_path,
        content_revision=12,
        actor=actor,
        client_id="mediaflow-desktop:desktop-user",
    )
    second = factory.create(
        "diagnostics.bundle.create",
        arguments,
        project_path=tmp_path,
        content_revision=12,
        actor=actor,
        client_id="mediaflow-desktop:desktop-user",
    )
    payload = json.loads(factory.canonical_json(first))

    assert first.request_id == second.request_id
    assert payload["protocol"] == "mediaflow-editor"
    assert payload["version"] == 4
    assert payload["project"] == str(tmp_path.resolve())
    assert payload["base_revision"] == 12
    assert payload["arguments"] == arguments


def test_request_factory_rejects_invalid_arguments_before_copy(tmp_path: Path) -> None:
    factory = AutomationRequestFactory()
    actor = ActorIdentity(kind="human", id="desktop-user")

    with pytest.raises(ValueError, match="absolute"):
        factory.create(
            "diagnostics.bundle.create",
            {"output_path": "relative.zip"},
            project_path=tmp_path,
            content_revision=0,
            actor=actor,
            client_id="desktop",
        )
