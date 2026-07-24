from __future__ import annotations

from pathlib import Path

import pytest

from mediaflow.composition import EditorApplication
from mediaflow.desktop.controllers import EditorControllers
from mediaflow.desktop.models import AssetFilterModel, AssetListModel, SequenceListModel
from mediaflow.domain.downloads import DownloadRequest
from mediaflow.domain.task_commands import ExportSequenceCommand
from mediaflow.infrastructure.platform_media import PlatformMediaResolver
from mediaflow.infrastructure.settings_repository import SettingsRepository
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService


def test_dict_list_model_applies_structural_and_value_changes_incrementally() -> None:
    model = SequenceListModel()
    resets: list[None] = []
    inserts: list[tuple[int, int]] = []
    moves: list[tuple[int, int]] = []
    changes: list[tuple[int, int]] = []
    model.modelReset.connect(lambda: resets.append(None))
    model.rowsInserted.connect(lambda _parent, first, last: inserts.append((first, last)))
    model.rowsMoved.connect(
        lambda _source_parent, first, _last, _destination_parent, destination: moves.append(
            (first, destination)
        )
    )
    model.dataChanged.connect(lambda first, last, _roles: changes.append((first.row(), last.row())))

    model.set_items(
        [
            {
                "sequenceId": "main",
                "name": "Main",
                "kind": "main",
                "profile": "1920×1080",
                "colorMode": "sdr_bt709",
            },
            {
                "sequenceId": "short-a",
                "name": "A",
                "kind": "short",
                "profile": "1080×1920",
                "colorMode": "sdr_bt709",
            },
        ]
    )
    model.set_items(
        [
            {
                "sequenceId": "short-a",
                "name": "A revised",
                "kind": "short",
                "profile": "1080×1920",
                "colorMode": "sdr_bt709",
            },
            {
                "sequenceId": "main",
                "name": "Main",
                "kind": "main",
                "profile": "1920×1080",
                "colorMode": "sdr_bt709",
            },
            {
                "sequenceId": "short-b",
                "name": "B",
                "kind": "short",
                "profile": "1080×1920",
                "colorMode": "sdr_bt709",
            },
        ]
    )

    assert resets == []
    assert inserts == [(0, 1), (2, 2)]
    assert moves == [(1, 0)]
    assert changes == [(0, 0)]
    assert [model.get(row)["sequenceId"] for row in range(model.rowCount())] == [
        "short-a",
        "main",
        "short-b",
    ]


def test_dict_list_model_rejects_rows_that_do_not_match_qml_roles() -> None:
    model = SequenceListModel()
    with pytest.raises(ValueError, match="missing=.*colorMode"):
        model.set_items([{"sequenceId": "main", "name": "Main"}])


def test_asset_filter_model_is_the_shared_search_boundary_for_all_views() -> None:
    assets = AssetListModel()
    assets.set_items(
        [
            {
                "assetId": asset_id,
                "name": name,
                "kind": "video",
                "path": f"D:/{name}",
                "status": "online",
                "managed": False,
                "durationFrames": 25,
                "width": 640,
                "height": 360,
                "previewUrl": "",
                "proxyReady": False,
                    "waveformReady": False,
                    "searchText": f"{name} video".casefold(),
            }
            for asset_id, name in (("one", "First Video.mp4"), ("two", "Overlay.png"))
        ]
    )
    filtered = AssetFilterModel(assets)

    assert filtered.rowCount() == 2
    filtered.setSearchText(" overlay ")
    assert filtered.rowCount() == 1
    assert assets.get(filtered.mapToSource(filtered.index(0, 0)).row())["assetId"] == "two"
    filtered.setSearchText("")
    assert filtered.rowCount() == 2


def test_download_plan_queues_selected_entries_as_typed_requests(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controllers = EditorControllers()
    session = controllers.session
    captured: list[DownloadRequest] = []
    monkeypatch.setattr(session, "_require_writable", lambda: None)
    monkeypatch.setattr(
        session,
        "_start_download_workflow",
        captured.extend,
    )
    plan = PlatformMediaResolver._bilibili_plan(
        "https://www.bilibili.com/video/BV1234567890",
        {
            "bvid": "BV1234567890",
            "title": "Course",
            "owner": {"name": "Teacher"},
            "ugc_season": {
                "title": "Complete Course",
                "sections": [
                    {
                        "episodes": [
                            {"bvid": "BV0000000001", "title": "One"},
                            {"bvid": "BV0000000002", "title": "Two"},
                            {"bvid": "BV0000000003", "title": "Three"},
                        ]
                    }
                ],
            },
        },
    )
    assert plan is not None
    session._set_download_plan(plan)
    controllers.settings.setLastDownloadUrl("https://example.com/remembered-video")
    controllers.settings.setDefaultProjectDirectory(str(tmp_path))
    selected_directory = tmp_path / "Selected Downloads"
    controllers.settings.setDefaultDownloadDirectory(str(selected_directory))

    controllers.tasks.submitDownloadPlan(
        "1080p",
        "1,3",
        False,
        "best",
        "Prefix",
    )
    controllers.settings.resetDefaultDownloadDirectory()

    assert [request.entry.download_url for request in captured] == [
        "https://www.bilibili.com/video/BV0000000001",
        "https://www.bilibili.com/video/BV0000000003",
    ]
    assert {request.collection_title for request in captured} == {"Complete Course"}
    assert {request.filename_prefix for request in captured} == {"Prefix"}
    assert {request.output_directory for request in captured} == {
        str(selected_directory.resolve())
    }
    assert controllers.settings.settingsData["downloadResolution"] == "1080p"
    assert controllers.settings.settingsData["downloadSubtitles"] is False
    assert controllers.settings.settingsData["downloadCodec"] == "best"
    assert (
        controllers.settings.settingsData["downloadDirectory"]
        == controllers.settings.builtInMediaDirectory
    )
    persisted = SettingsRepository().load()
    assert persisted.download.last_url == "https://example.com/remembered-video"
    assert persisted.ui.default_project_directory == str(tmp_path.resolve())
    assert persisted.download.resolution == "1080p"


def test_download_entry_model_exposes_unavailable_collection_slots() -> None:
    controllers = EditorControllers()
    plan = YtDlpDownloadService._plan_from_info(
        {
            "_type": "playlist",
            "id": "course",
            "title": "Course",
            "extractor_key": "YoutubeTab",
            "entries": [
                {
                    "id": "one",
                    "title": "One",
                    "webpage_url": "https://www.youtube.com/watch?v=one",
                },
                None,
            ],
        },
        "https://www.youtube.com/playlist?list=course",
    )

    controllers.session._set_download_plan(plan)

    model = controllers.tasks.downloadEntriesModel
    assert model.rowCount() == 2
    assert model.get(0)["selected"] is True
    assert model.get(1)["available"] is False
    assert model.get(1)["selected"] is False
    assert "无权访问" in model.get(1)["unavailableReason"]


def test_download_plan_preserves_source_profile_and_real_available_heights() -> None:
    plan = YtDlpDownloadService._plan_from_info(
        {
            "id": "profiled-video",
            "title": "Profiled Video",
            "extractor_key": "Fixture",
            "width": 3840,
            "height": 2160,
            "fps": 29.97002997,
            "formats": [
                {"height": 720, "vcodec": "h264"},
                {"height": 2160, "vcodec": "vp9"},
                {"height": 1080, "vcodec": "h264"},
                {"height": 1080, "vcodec": "av1"},
                {"height": None, "vcodec": "none"},
            ],
        },
        "https://example.com/profiled-video",
    )

    assert (plan.width, plan.height) == (3840, 2160)
    assert plan.fps == pytest.approx(29.97002997)
    assert plan.available_heights == [2160, 1080, 720]


def test_project_switch_rolls_back_to_live_previous_session_when_binding_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    application = EditorApplication()
    controllers = EditorControllers(application=application)
    session = controllers.session
    try:
        first = application.create_project(tmp_path / "First", "First")
        session._replace_project(first)
        first_id = session._documents.get_project().id
        second = application.create_project(tmp_path / "Second", "Second")
        original_refresh = session._projector.refresh_all
        attempts = 0

        def fail_candidate_refresh_once() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("binding failed")
            original_refresh()

        monkeypatch.setattr(session._projector, "refresh_all", fail_candidate_refresh_once)
        with pytest.raises(RuntimeError, match="binding failed"):
            session._replace_project(second)

        assert session._documents.get_project().id == first_id
        assert session._project.project_dir == (tmp_path / "First").resolve()
        assert controllers.workspace.sequencesModel.rowCount() == 1
        with application.open_project(tmp_path / "Second", writable=True) as reopened:
            assert reopened.documents.get_project().name == "Second"
    finally:
        controllers.shutdown()


def test_default_export_uses_project_folder_and_avoids_existing_names(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    application = EditorApplication()
    controllers = EditorControllers(application=application)
    captured: list[ExportSequenceCommand] = []

    def capture_task(command, *, sequence_id=None):
        del sequence_id
        captured.append(command)
        return None

    try:
        controllers.workspace.createProject(
            str(tmp_path),
            "Export Defaults",
        )
        monkeypatch.setattr(controllers.session, "_start_task", capture_task)

        controllers.export.exportSequenceToDefaultLocation("h264", "mp4", {})
        first_output = Path(captured[-1].output_path)
        assert first_output == tmp_path / "Export Defaults" / "exports" / "主序列.mp4"
        first_output.touch()

        controllers.export.exportSequenceToDefaultLocation("h264", "mp4", {})
        assert Path(captured[-1].output_path) == (
            tmp_path / "Export Defaults" / "exports" / "主序列 (2).mp4"
        )
    finally:
        controllers.shutdown()
