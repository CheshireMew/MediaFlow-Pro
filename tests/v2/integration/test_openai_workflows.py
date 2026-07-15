from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.translation_service import TranslationService
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.models import SubtitleDocument, SubtitleSegment
from mediaflow.domain.settings import LlmProviderSettings
from mediaflow.infrastructure.project_repository import ProjectRepository


class OpenAICompatibleHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        user_payload = json.loads(request["messages"][1]["content"])
        if "target_language" in user_payload:
            content = {
                "segments": [
                    {"id": segment["id"], "text": f"译文：{segment['text']}"}
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
    server = ThreadingHTTPServer(("127.0.0.1", 0), OpenAICompatibleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        media = tmp_path / "source.mp4"
        media.write_bytes(b"media")
        with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
            asset = repository.import_external_asset(media, AssetKind.VIDEO)
            project = repository.get_project()
            source = SubtitleDocument(project_id=project.id, asset_id=asset.id, language="en")
            segments = [
                SubtitleSegment(document_id=source.id, start_frame=0, end_frame=30, text="Hello"),
                SubtitleSegment(document_id=source.id, start_frame=31, end_frame=60, text="World"),
            ]
            repository.create_subtitle_document(source, segments)
            provider = LlmProviderSettings(
                name="Local HTTP provider",
                base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
                api_key="test-key",
                model="test-model",
            )

            translated = TranslationService(repository).translate_document(
                source.id,
                target_language="zh_CN",
                provider=provider,
            )
            translated_segments = repository.list_subtitle_segments(translated.id)
            assert [item.text for item in translated_segments] == ["译文：Hello", "译文：World"]
            assert [item.source_segment_id for item in translated_segments] == [
                segments[0].id,
                segments[1].id,
            ]

            candidates = HighlightService(repository).analyze_document(
                translated.id,
                provider=provider,
            )
            sequence = HighlightService(repository).create_short_sequence(candidates[0].id)
            timeline = repository.load_timeline(sequence.id)
            assert timeline.clips[0].asset_id == asset.id
            assert timeline.clips[0].duration == 60
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
