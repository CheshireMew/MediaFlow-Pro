from __future__ import annotations

from pathlib import Path

import pytest

import mediaflow.application.subtitle_publication as subtitle_publication_module
from mediaflow.composition import EditorApplication
from mediaflow.domain.collaboration import ActorIdentity
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment


def _add_subtitle_document(
    project,
    source: Path,
    text: str,
) -> tuple[SubtitleDocument, SubtitleSegment, Path]:
    source.write_text(
        (
            "1\n"
            "00:00:00,000 --> 00:00:01,000\n"
            f"{text}\n"
        ),
        encoding="utf-8-sig",
    )
    repository = project._repository
    catalog = repository.catalog
    project_record = catalog.get_project()
    asset = catalog.import_external_asset(
        source,
        AssetKind.SUBTITLE,
    )
    document = SubtitleDocument(
        project_id=project_record.id,
        asset_id=asset.id,
        sequence_id=project_record.main_sequence_id,
        language="en",
    )
    segment = SubtitleSegment(
        document_id=document.id,
        start_frame=0,
        end_frame=25,
        text=text,
    )
    repository.subtitles.create_subtitle_document(
        document,
        [segment],
    )
    output = project.write_subtitle_srt(document.id)
    return document, segment, output


def test_atomic_automation_receipt_failure_rolls_back_database_srt_and_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = EditorApplication()
    with application.create_project(
        tmp_path / "Subtitle Automation Rollback",
        "Subtitle Automation Rollback",
    ) as project:
        document, segment, output = _add_subtitle_document(
            project,
            tmp_path / "source.srt",
            "Original",
        )
        original_bytes = output.read_bytes()
        original_revision = project.content_revision()
        history_checkpoint = project._history.checkpoint()

        def reject_receipt(*_args, **_kwargs):
            raise OSError("injected automation receipt failure")

        monkeypatch.setattr(
            project._repository,
            "save_automation_result",
            reject_receipt,
        )

        def update(_retrying: bool) -> dict:
            changed = project.update_subtitle_segment(
                document.id,
                segment.id,
                start_frame=0,
                end_frame=25,
                text="Must roll back",
            )
            return {"segment": changed.model_dump(mode="json")}

        with pytest.raises(
            OSError,
            match="automation receipt failure",
        ):
            project.execute_automation_request(
                "subtitle-update-rollback",
                "subtitle.segment.update",
                {
                    "document_id": document.id,
                    "segment_id": segment.id,
                    "start_frame": 0,
                    "end_frame": 25,
                    "text": "Must roll back",
                },
                    update,
                    atomic=True,
                    base_revision=original_revision,
                    actor=ActorIdentity(kind="system", id="commit-boundary-test"),
                    write_set=[f"/subtitles/segments/{segment.id}"],
            )

        persisted = project.list_subtitle_segments(document.id)[0]
        assert persisted.text == "Original"
        assert output.read_bytes() == original_bytes
        assert project.content_revision() == original_revision
        assert project._history.checkpoint() == history_checkpoint
        assert project._repository._fetchone(
            "SELECT request_id FROM automation_request WHERE request_id=?",
            ("subtitle-update-rollback",),
        ) is None
        archived = list(
            (
                project.project_dir
                / "archive"
                / "subtitle-publications"
            ).rglob("*.srt")
        )
        assert len(archived) == 1
        assert "Must roll back" in archived[0].read_text(
            encoding="utf-8-sig"
        )


def test_named_version_restore_joins_automation_and_all_srt_publications(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = EditorApplication()
    with application.create_project(
        tmp_path / "Version Automation",
        "Version Automation",
    ) as project:
        first, first_segment, first_output = _add_subtitle_document(
            project,
            tmp_path / "first.srt",
            "Version first",
        )
        second, second_segment, second_output = _add_subtitle_document(
            project,
            tmp_path / "second.srt",
            "Version second",
        )
        version = project.create_version("字幕一致版本")
        third, _third_segment, third_output = _add_subtitle_document(
            project,
            tmp_path / "third.srt",
            "Created after version",
        )
        project.update_subtitle_segment(
            first.id,
            first_segment.id,
            start_frame=0,
            end_frame=25,
            text="Current first",
        )
        project.update_subtitle_segment(
            second.id,
            second_segment.id,
            start_frame=0,
            end_frame=25,
            text="Current second",
        )
        current_bytes = {
            first_output: first_output.read_bytes(),
            second_output: second_output.read_bytes(),
            third_output: third_output.read_bytes(),
        }
        current_revision = project.content_revision()
        history_checkpoint = project._history.checkpoint()
        original_write = (
            subtitle_publication_module._write_document_srt
        )
        writes = 0

        def fail_after_second_write(
            repository,
            document_id: str,
            output: Path,
        ) -> bool:
            nonlocal writes
            writes += 1
            changed = original_write(
                repository,
                document_id,
                output,
            )
            if writes == 2:
                raise OSError(
                    "injected second restored SRT failure"
                )
            return changed

        monkeypatch.setattr(
            subtitle_publication_module,
            "_write_document_srt",
            fail_after_second_write,
        )

        def restore(_retrying: bool) -> dict:
            restored = project.restore_version(version.id)
            return {"version": restored.model_dump(mode="json")}

        with pytest.raises(
            OSError,
            match="second restored SRT failure",
        ):
            project.execute_automation_request(
                "restore-version-with-srts",
                "project.version.restore",
                {"version_id": version.id},
                    restore,
                    atomic=True,
                    base_revision=current_revision,
                    actor=ActorIdentity(kind="system", id="commit-boundary-test"),
                    write_set=[f"/project/versions/{version.id}/restore"],
            )

        assert project.content_revision() == current_revision
        assert {
            document.id
            for document in project.list_subtitle_documents()
        } == {first.id, second.id, third.id}
        assert [
            project.list_subtitle_segments(document_id)[0].text
            for document_id in (first.id, second.id, third.id)
        ] == [
            "Current first",
            "Current second",
            "Created after version",
        ]
        assert {
            path: path.read_bytes() for path in current_bytes
        } == current_bytes
        assert project._history.checkpoint() == history_checkpoint
        assert {
            row["name"]
            for row in project._repository._connection.execute(
                "PRAGMA database_list"
            ).fetchall()
        } == {"main"}

        monkeypatch.setattr(
            subtitle_publication_module,
            "_write_document_srt",
            original_write,
        )
        restored = project.execute_automation_request(
            "restore-version-with-srts",
            "project.version.restore",
            {"version_id": version.id},
            restore,
            atomic=True,
            base_revision=current_revision,
            actor=ActorIdentity(kind="system", id="commit-boundary-test"),
            write_set=[f"/project/versions/{version.id}/restore"],
        )
        replayed = project.execute_automation_request(
            "restore-version-with-srts",
            "project.version.restore",
            {"version_id": version.id},
            restore,
            atomic=True,
            base_revision=current_revision,
            actor=ActorIdentity(kind="system", id="commit-boundary-test"),
            write_set=[f"/project/versions/{version.id}/restore"],
        )

        assert replayed == restored
        assert {
            document.id
            for document in project.list_subtitle_documents()
        } == {first.id, second.id}
        assert [
            project.list_subtitle_segments(document_id)[0].text
            for document_id in (first.id, second.id)
        ] == ["Version first", "Version second"]
        assert "Version first" in first_output.read_text(
            encoding="utf-8-sig"
        )
        assert "Version second" in second_output.read_text(
            encoding="utf-8-sig"
        )
        assert not third_output.exists()
        archived = list(
            (
                project.project_dir
                / "archive"
                / "subtitle-publications"
            ).rglob("*.srt")
        )
        assert any(
            "Created after version"
            in path.read_text(encoding="utf-8-sig")
            for path in archived
        )
