from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from mediaflow.application.asset_service import AssetService
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.composition import EditorProject
from mediaflow.domain.enums import TaskStatus, TrackKind
from mediaflow.domain.settings import GlobalSettings
from mediaflow.domain.task_commands import (
    AnalyzeScenesCommand,
    ImportStockMediaCommand,
    TrackSubjectCommand,
)
from mediaflow.infrastructure.fcpxml_export import FcpxmlExportService
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt import TimelineCompiler
from mediaflow.infrastructure.mobile_import_server import MobileImportServer
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.stock_media import StockMediaService
from tests.v2.infrastructure.test_media_pipeline import (
    generate_black_intro_video,
    generate_real_media,
)


class _FixtureHandler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[str, bytes]] = {}

    def do_GET(self) -> None:  # noqa: N802
        route = self.routes.get(self.path.split("?", 1)[0])
        if route is None:
            self.send_error(404)
            return
        content_type, body = route
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _fixture_server(routes: dict[str, tuple[str, bytes]]):
    handler = type("FixtureHandler", (_FixtureHandler,), {"routes": routes})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"


def test_scene_and_subject_tasks_write_observable_timeline_results(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "scene-source.mp4"
    generate_black_intro_video(source, paths)
    repository = ProjectRepository.create(tmp_path / "Visual Project", "Visual Project")
    assets = AssetService(repository, MediaProbe(paths))
    asset = assets.import_external(source)
    asset = assets.adopt_main_profile_from_video(asset.id)
    project = EditorProject(repository, settings=GlobalSettings(), paths=paths)
    try:
        sequence_id = repository.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=asset.metadata.duration_frames,
        )
        scene_task = project.start_task(
            AnalyzeScenesCommand(sequence_id=sequence_id, clip_id=clip.id, threshold=0.1),
            [asset.id],
            sequence_id=sequence_id,
        )
        completed_scene = project.tasks.wait(scene_task.id, timeout=60)
        assert completed_scene.status == TaskStatus.COMPLETED, completed_scene.error
        scene_artifact = repository.project_dir / completed_scene.artifacts[0]
        scene_payload = json.loads(scene_artifact.read_text(encoding="utf-8"))
        state = repository.load_timeline(sequence_id)
        assert scene_payload["frames"]
        assert [marker.frame for marker in state.markers] == scene_payload["frames"]
        assert all(marker.name.startswith(f"场景切点 · {clip.id[:8]}") for marker in state.markers)

        tracking_task = project.start_task(
            TrackSubjectCommand(
                sequence_id=sequence_id,
                clip_id=clip.id,
                mode="auto_reframe",
            ),
            [asset.id],
            sequence_id=sequence_id,
        )
        completed_tracking = project.tasks.wait(tracking_task.id, timeout=60)
        assert completed_tracking.status == TaskStatus.COMPLETED, completed_tracking.error
        state = repository.load_timeline(sequence_id)
        tracked = next(item for item in state.clips if item.id == clip.id)
        assert len(tracked.transform_keyframes) >= 2
        assert all(item.source == "auto_reframe" for item in tracked.transform_keyframes)
        xml = TimelineCompiler(repository).compile(state).xml
        transform_filter = ET.fromstring(xml).find(f".//filter[@id='transform_{clip.id}']")
        assert transform_filter is not None
        rect = next(
            item.text
            for item in transform_filter.findall("property")
            if item.attrib.get("name") == "rect"
        )
        assert rect is not None and ";" in rect and "=" in rect
    finally:
        project.close()


def test_fcpxml_exports_real_media_timing_markers_and_captions(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    subtitle = tmp_path / "source.zh.srt"
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:00,800\n城市夜景\n",
        encoding="utf-8",
    )
    with ProjectRepository.create(tmp_path / "FCPXML Project", "FCPXML Project") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        asset = assets.import_external(source)
        asset = assets.adopt_main_profile_from_video(asset.id)
        sequence_id = repository.get_project().main_sequence_id
        editor = TimelineEditor(repository, sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        subtitle_track = editor.add_track(TrackKind.SUBTITLE)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=5,
            source_in=2,
            duration=20,
        )
        editor.add_marker(10, "重点")
        publication = SubtitlePublicationService(repository)
        document = SubtitleAcquisitionService(repository, publication).import_subtitle_file(
            subtitle,
            assets,
            media_asset_id=asset.id,
        )
        repository.place_subtitle_document(
            document.id,
            subtitle_track.id,
            offset_frames=5,
            follow_clips=False,
        )
        output = FcpxmlExportService(repository).export(
            repository.load_timeline(sequence_id),
            tmp_path / "handoff.fcpxml",
        )
        root = ET.parse(output).getroot()
        resource = root.find("./resources/asset")
        exported_clip = root.find(".//asset-clip")
        marker = root.find(".//marker")
        caption = root.find(".//caption/text/text-style")
        assert root.attrib["version"] == "1.11"
        assert resource is not None and resource.attrib["src"] == source.resolve().as_uri()
        assert exported_clip is not None
        assert exported_clip.attrib["name"] == asset.name
        assert exported_clip.attrib["offset"] == "1/5s"
        assert exported_clip.attrib["start"] == "2/25s"
        assert exported_clip.attrib["duration"] == "4/5s"
        assert marker is not None and marker.attrib["value"] == "重点"
        assert caption is not None and caption.text == "城市夜景"


def test_mobile_upload_reaches_project_sources_then_normal_import_task(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "phone.mp4"
    generate_real_media(source, paths, width=320, height=180)
    received: list[Path] = []
    arrived = threading.Event()
    server = MobileImportServer()
    project_dir = tmp_path / "Mobile Project"
    project = None
    try:
        session = server.start(
            project_dir,
            lambda path: (received.append(path), arrived.set()),
        )
        assert session.qr_path.is_file() and "<svg" in session.qr_path.read_text(encoding="utf-8")
        page = urlopen(session.url, timeout=10).read().decode("utf-8")
        assert "MediaFlow Pro" in page and "multipart/form-data" in page
        boundary = "MediaFlowBoundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="files"; filename="phone.mp4"\r\n'
            "Content-Type: video/mp4\r\n\r\n"
        ).encode() + source.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        request = Request(
            session.url + "/upload",
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert "已发送 1 个文件" in urlopen(request, timeout=20).read().decode("utf-8")
        assert arrived.wait(5) and len(received) == 1
        uploaded = received[0]
        assert uploaded.parent == project_dir / "sources" / "mobile"
        assert uploaded.read_bytes() == source.read_bytes()

        repository = ProjectRepository.create(project_dir, "Mobile Project")
        project = EditorProject(repository, settings=GlobalSettings(), paths=paths)
        task = project.import_asset(uploaded)
        completed = project.tasks.wait(task.id, timeout=60)
        result = project.consume_task_result(completed)
        assert completed.status == TaskStatus.COMPLETED, completed.error
        assert result.imported_asset_id
        asset = repository.get_asset(result.imported_asset_id)
        assert repository.resolve_asset_path(asset) == uploaded.resolve()
    finally:
        server.stop()
        if project is not None:
            project.close()


def test_stock_providers_use_http_results_and_import_downloaded_media(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "stock-source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    pexels = {
        "videos": [
            {
                "id": 101,
                "url": "https://pexels.example/video/101",
                "duration": 1,
                "width": 320,
                "height": 180,
                "user": {"name": "Pexels Author", "url": "https://pexels.example/author"},
                "video_pictures": [{"picture": "https://images.example/101.jpg"}],
                "video_files": [],
            }
        ]
    }
    pixabay = {
        "hits": [
            {
                "id": 202,
                "pageURL": "https://pixabay.example/202",
                "tags": "city, night",
                "duration": 1,
                "user": "Pixabay Author",
                "user_id": 9,
                "videos": {},
            }
        ]
    }
    unsplash = {
        "results": [
            {
                "id": "photo303",
                "width": 1200,
                "height": 800,
                "alt_description": "city at night",
                "urls": {"small": "https://images.example/small.jpg", "full": "https://images.example/full.jpg"},
                "links": {"html": "https://unsplash.example/photo", "download_location": "https://api.unsplash.example/download"},
                "user": {"name": "Unsplash Author", "links": {"html": "https://unsplash.example/author"}},
            }
        ]
    }
    routes = {
        "/media.mp4": ("video/mp4", source.read_bytes()),
        "/pexels": ("application/json", json.dumps(pexels).encode()),
        "/pixabay": ("application/json", json.dumps(pixabay).encode()),
        "/unsplash": ("application/json", json.dumps(unsplash).encode()),
    }
    fixture, thread, base = _fixture_server(routes)
    original_urls = (
        StockMediaService.PEXELS_SEARCH_URL,
        StockMediaService.PIXABAY_SEARCH_URL,
        StockMediaService.UNSPLASH_SEARCH_URL,
    )
    try:
        pexels["videos"][0]["video_files"] = [
            {"link": f"{base}/media.mp4", "width": 320, "height": 180}
        ]
        pixabay["hits"][0]["videos"] = {
            "medium": {
                "url": f"{base}/media.mp4",
                "width": 320,
                "height": 180,
                "thumbnail": "https://images.example/202.jpg",
            }
        }
        _FixtureHandler.routes = routes
        fixture.RequestHandlerClass.routes["/pexels"] = (
            "application/json",
            json.dumps(pexels).encode(),
        )
        fixture.RequestHandlerClass.routes["/pixabay"] = (
            "application/json",
            json.dumps(pixabay).encode(),
        )
        StockMediaService.PEXELS_SEARCH_URL = f"{base}/pexels"
        StockMediaService.PIXABAY_SEARCH_URL = f"{base}/pixabay"
        StockMediaService.UNSPLASH_SEARCH_URL = f"{base}/unsplash"
        pexels_items = StockMediaService.search("pexels", "city", "key")
        pixabay_items = StockMediaService.search("pixabay", "city", "key")
        unsplash_items = StockMediaService.search("unsplash", "city", "key")
        assert pexels_items[0].attribution == "Pexels Author"
        assert pixabay_items[0].title == "city, night"
        assert unsplash_items[0].tracking_url.endswith("/download")

        repository = ProjectRepository.create(tmp_path / "Stock Project", "Stock Project")
        settings = GlobalSettings()
        settings.stock_media.pixabay_api_key = "key"
        project = EditorProject(repository, settings=settings, paths=paths)
        try:
            item = pixabay_items[0].model_copy(
                update={"download_url": f"{base}/media.mp4"}
            )
            task = project.start_task(
                ImportStockMediaCommand(item=item),
                sequence_id=repository.get_project().main_sequence_id,
            )
            completed = project.tasks.wait(task.id, timeout=60)
            result = project.consume_task_result(completed)
            assert completed.status == TaskStatus.COMPLETED, completed.error
            assert result.imported_asset_id
            asset = repository.get_asset(result.imported_asset_id)
            imported = repository.resolve_asset_path(asset)
            assert imported.is_file()
            assert imported.parent == repository.project_dir / "sources" / "stock" / "pixabay"
            attribution = repository.project_dir / completed.artifacts[1]
            assert attribution.is_file() and "Pixabay Author" in attribution.read_text(encoding="utf-8")
        finally:
            project.close()
    finally:
        (
            StockMediaService.PEXELS_SEARCH_URL,
            StockMediaService.PIXABAY_SEARCH_URL,
            StockMediaService.UNSPLASH_SEARCH_URL,
        ) = original_urls
        fixture.shutdown()
        fixture.server_close()
        thread.join(timeout=2)
