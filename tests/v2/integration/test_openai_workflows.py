import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mediaflow.application.asset_service import AssetService
from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.translation_service import TranslationService
from mediaflow.composition import EditorProject
from mediaflow.domain.enums import AssetKind, TaskStatus
from mediaflow.domain.settings import (
    GlossaryTermSettings,
    LlmProviderSettings,
    ServiceSettings,
)
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.task_commands import TranslateSegmentsCommand
from mediaflow.infrastructure.llm_client import OpenAIJsonClient
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.subtitle_file_store import LocalSubtitleFileStore
from mediaflow.infrastructure.subtitle_publication_storage import (
    LocalSubtitlePublicationStorage,
)
from mediaflow.infrastructure.translation_cache import TranslationCache

pytestmark = pytest.mark.integration


def _translation_service(repository: ProjectRepository) -> TranslationService:
    publication = SubtitlePublicationService(repository, LocalSubtitlePublicationStorage())
    return TranslationService(
        repository,
        OpenAIJsonClient,
        TranslationCache(
            RuntimeContext.discover().paths.project_cache_dir(
                repository.project_dir
            )
            / "translations"
        ),
        publication,
    )


class OpenAICompatibleHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    activity_lock = threading.Lock()
    active_requests = 0
    maximum_active_requests = 0

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        system_prompt = request["messages"][0]["content"]
        user_payload = json.loads(request["messages"][1]["content"])
        self.__class__.requests.append(user_payload)
        if "target_language" in user_payload:
            source_texts = [str(segment.get("source_text") or "") for segment in user_payload["segments"]]
            if any(text.startswith("__slow__") for text in source_texts):
                with self.__class__.activity_lock:
                    self.__class__.active_requests += 1
                    self.__class__.maximum_active_requests = max(
                        self.__class__.maximum_active_requests,
                        self.__class__.active_requests,
                    )
                time.sleep(0.15)
                with self.__class__.activity_lock:
                    self.__class__.active_requests -= 1
            if user_payload.get("mode") == "intelligent" and "in-place edit" not in system_prompt:
                content = {
                    "segments": [
                        {"text": "智能合并译文", "time_percentage": 1.0},
                    ]
                }
            elif len(source_texts) > 1 and any(
                text.startswith("__force_fallback__") for text in source_texts
            ):
                content = {"segments": [{"id": "invalid", "text": "invalid"}]}
            else:
                prefix = "校对：" if user_payload.get("mode") == "proofread" else "译文："
                content = {
                    "segments": [
                        {"id": segment["id"], "text": f"{prefix}{segment['source_text']}"}
                        for segment in user_payload["segments"]
                    ]
                }
        else:
            segments = user_payload["segments"]
            content = {
                "candidates": [
                    {
                        "start_id": segments[0]["id"],
                        "end_id": segments[-1]["id"],
                        "title": "完整观点",
                        "reason": "内容完整",
                        "score": 0.9,
                    }
                ]
            }
        response = {
            "id": "local-real-http-response",
            "object": "chat.completion",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        return


def test_openai_protocol_translation_and_highlight_become_project_data(tmp_path: Path) -> None:
    OpenAICompatibleHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), OpenAICompatibleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        media = tmp_path / "source.mp4"
        media.write_bytes(b"media")
        subtitle = tmp_path / "source.en.srt"
        subtitle.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n2\n00:00:01,033 --> 00:00:02,000\nWorld\n",
            encoding="utf-8",
        )
        with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
            asset = repository.assets.import_external_asset(media, AssetKind.VIDEO)
            publication = SubtitlePublicationService(
                repository,
                LocalSubtitlePublicationStorage(),
            )
            source = SubtitleAcquisitionService(
                repository,
                publication,
                LocalSubtitleFileStore(),
            ).import_subtitle_file(
                subtitle,
                AssetService(
                    repository,
                    MediaProbe(RuntimeContext.discover().paths),
                ),
            )
            segments = repository.subtitles.list_subtitle_segments(source.id)
            assert repository.assets.get_asset(source.asset_id).kind == AssetKind.SUBTITLE
            assert source.media_asset_id == asset.id
            assert [item.text for item in segments] == ["Hello", "World"]
            provider = LlmProviderSettings(
                name="Local HTTP provider",
                base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
                api_key="test-key",
                model="test-model",
            )

            translated = _translation_service(repository).translate_document(
                source.id,
                target_language="fr",
                provider=provider,
                glossary=[
                    GlossaryTermSettings(source="Hello", target="你好", category="greeting"),
                    GlossaryTermSettings(source="Unused", target="未使用"),
                ],
            )
            translated_segments = repository.subtitles.list_subtitle_segments(translated.id)
            assert translated.language == "fr"
            assert translated.media_asset_id == asset.id
            assert [item.text for item in translated_segments] == ["译文：Hello", "译文：World"]
            assert [item.source_segment_id for item in translated_segments] == [
                segments[0].id,
                segments[1].id,
            ]
            translation_request = OpenAICompatibleHandler.requests[-1]
            assert translation_request["target_language"] == "fr"
            assert translation_request["glossary"] == [
                {"source": "Hello", "target": "你好", "note": "", "category": "greeting"}
            ]
            request_count = len(OpenAICompatibleHandler.requests)
            cached = _translation_service(repository).translate_document(
                source.id,
                target_language="fr",
                provider=provider,
                glossary=[
                    GlossaryTermSettings(source="Hello", target="你好", category="greeting"),
                    GlossaryTermSettings(source="Unused", target="未使用"),
                ],
            )
            assert len(OpenAICompatibleHandler.requests) == request_count
            assert [item.text for item in repository.subtitles.list_subtitle_segments(cached.id)] == [
                "译文：Hello",
                "译文：World",
            ]

            proofread = _translation_service(repository).translate_document(
                source.id,
                target_language="zh_CN",
                provider=provider,
                mode="proofread",
            )
            assert proofread.language == "en"
            assert [item.text for item in repository.subtitles.list_subtitle_segments(proofread.id)] == [
                "校对：Hello",
                "校对：World",
            ]

            intelligent = _translation_service(repository).translate_document(
                source.id,
                target_language="zh_CN",
                provider=provider,
                mode="intelligent",
            )
            intelligent_segments = repository.subtitles.list_subtitle_segments(intelligent.id)
            assert [(item.start_frame, item.end_frame, item.text) for item in intelligent_segments] == [
                (0, 60, "智能合并译文")
            ]
            assert intelligent_segments[0].source_segment_id is None

            repository.subtitles.save_subtitle_segments(
                translated.id,
                [
                    translated_segments[0].model_copy(update={"text": "用户旧译文"}),
                    translated_segments[1],
                ],
            )
            _translation_service(repository).translate_selected_to_document(
                source.id,
                translated.id,
                [segments[0].id],
                target_language="fr",
                provider=provider,
            )
            assert [item.text for item in repository.subtitles.list_subtitle_segments(source.id)] == [
                "Hello",
                "World",
            ]
            assert [
                item.text for item in repository.subtitles.list_subtitle_segments(translated.id)
            ] == ["译文：Hello", "译文：World"]

            _translation_service(repository).translate_selected_to_document(
                source.id,
                intelligent.id,
                [segment.id for segment in segments],
                target_language="zh_CN",
                provider=provider,
                mode="standard",
            )
            intelligent_retranslated = repository.subtitles.list_subtitle_segments(intelligent.id)
            assert [item.text for item in intelligent_retranslated] == [
                "译文：Hello",
                "译文：World",
            ]
            assert [item.source_segment_id for item in intelligent_retranslated] == [
                segments[0].id,
                segments[1].id,
            ]

            highlight_service = HighlightService(repository, OpenAIJsonClient)
            candidates = highlight_service.analyze_document(
                translated.id,
                provider=provider,
            )
            highlight_service.update_candidate(
                candidates[0].id,
                start_frame=candidates[0].start_frame,
                end_frame=candidates[0].end_frame,
                title="用户修改的标题",
            )
            highlight_service.set_selected(candidates[0].id, False)
            highlight_service.analyze_document(translated.id, provider=provider)
            persisted_candidates = repository.highlights.list_highlights(asset.id)
            assert len(persisted_candidates) == 1
            assert persisted_candidates[0].title == "用户修改的标题"
            assert persisted_candidates[0].selected is False
            highlight_service.set_selected(candidates[0].id, True)
            sequence = highlight_service.create_short_sequence(candidates[0].id)
            timeline = repository.timeline.load_timeline(sequence.id)
            assert timeline.clips[0].asset_id == asset.id
            assert timeline.clips[0].duration == 60

            intelligently_edited = _translation_service(repository).translate_selected_in_document(
                source.id,
                [segments[1].id],
                target_language="zh_CN",
                provider=provider,
                mode="intelligent",
            )
            assert [item.id for item in intelligently_edited] == [segments[1].id]
            assert [item.text for item in repository.subtitles.list_subtitle_segments(source.id)] == [
                "Hello",
                "译文：World",
            ]

            project_app = EditorProject(
                repository,
                settings=ServiceSettings(
                    llm_providers=[provider],
                    active_llm_provider_id=provider.id,
                ),
                paths=RuntimeContext.discover().paths,
            )
            try:
                task = project_app.start_task(
                    TranslateSegmentsCommand(
                        document_id=source.id,
                        segment_ids=[segments[0].id],
                        target_language="zh_CN",
                        mode="standard",
                    ),
                )
                completed = project_app.wait_for_task(task.id, timeout=10)
                assert completed.status == TaskStatus.COMPLETED
                assert completed.artifacts
                assert completed.artifacts[0].resolve(repository.project_dir).is_file()
                assert [item.text for item in repository.subtitles.list_subtitle_segments(source.id)] == [
                    "译文：Hello",
                    "译文：World",
                ]
            finally:
                project_app.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_translation_batches_use_concurrency_context_and_single_line_fallback(
    tmp_path: Path,
) -> None:
    OpenAICompatibleHandler.requests = []
    OpenAICompatibleHandler.active_requests = 0
    OpenAICompatibleHandler.maximum_active_requests = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), OpenAICompatibleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        media = tmp_path / "source.mp4"
        media.write_bytes(b"media")
        with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
            asset = repository.assets.import_external_asset(media, AssetKind.VIDEO)
            project = repository.projects.get_project()
            source = SubtitleDocument(project_id=project.id, asset_id=asset.id, language="en")
            segments = [
                SubtitleSegment(
                    document_id=source.id,
                    start_frame=index * 30,
                    end_frame=(index + 1) * 30,
                    text=f"__slow__ line {index}",
                )
                for index in range(21)
            ]
            repository.subtitles.create_subtitle_document(source, segments)
            provider = LlmProviderSettings(
                name="Local HTTP provider",
                base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
                api_key="test-key",
                model="test-model",
            )
            translated = _translation_service(repository).translate_document(
                source.id,
                target_language="de",
                provider=provider,
            )
            assert OpenAICompatibleHandler.maximum_active_requests >= 2
            assert [item.text for item in repository.subtitles.list_subtitle_segments(translated.id)] == [
                f"译文：__slow__ line {index}" for index in range(21)
            ]
            second_batch = next(
                request
                for request in OpenAICompatibleHandler.requests
                if request["segments"][0]["source_text"] == "__slow__ line 10"
            )
            assert [item["source_text"] for item in second_batch["context_before"]] == [
                "__slow__ line 7",
                "__slow__ line 8",
                "__slow__ line 9",
            ]

            fallback_source = SubtitleDocument(
                project_id=project.id,
                asset_id=asset.id,
                language="en",
            )
            fallback_segments = [
                SubtitleSegment(
                    document_id=fallback_source.id,
                    start_frame=index * 30,
                    end_frame=(index + 1) * 30,
                    text=f"__force_fallback__ {index}",
                )
                for index in range(2)
            ]
            repository.subtitles.create_subtitle_document(fallback_source, fallback_segments)
            before = len(OpenAICompatibleHandler.requests)
            fallback_document = _translation_service(repository).translate_document(
                fallback_source.id,
                target_language="de",
                provider=provider,
            )
            assert len(OpenAICompatibleHandler.requests) - before == 3
            assert [
                item.text
                for item in repository.subtitles.list_subtitle_segments(fallback_document.id)
            ] == [
                "译文：__force_fallback__ 0",
                "译文：__force_fallback__ 1",
            ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
