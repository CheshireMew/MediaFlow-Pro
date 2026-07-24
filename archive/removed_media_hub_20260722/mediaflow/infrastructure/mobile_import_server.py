from __future__ import annotations

import html
import re
import secrets
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import qrcode
import qrcode.image.svg


@dataclass(frozen=True, slots=True)
class MobileImportSession:
    url: str
    qr_path: Path
    token: str


class MobileImportServer:
    """Project-scoped LAN upload endpoint used by the mobile import dialog."""

    def __init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(
        self,
        project_dir: Path,
        on_file: Callable[[Path], None],
    ) -> MobileImportSession:
        self.stop()
        token = secrets.token_urlsafe(18)
        destination = project_dir / "sources" / "mobile"
        destination.mkdir(parents=True, exist_ok=True)
        handler = self._handler(token, destination, on_file)
        server = ThreadingHTTPServer(("0.0.0.0", 0), handler)
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="mediaflow-mobile-import",
            daemon=True,
        )
        self._thread.start()
        host = self._lan_address()
        url = f"http://{host}:{server.server_port}/{token}"
        qr_path = project_dir / "cache" / "mobile-import" / f"{token}.svg"
        qr_path.parent.mkdir(parents=True, exist_ok=True)
        image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
        image.save(qr_path)
        return MobileImportSession(url=url, qr_path=qr_path, token=token)

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    @staticmethod
    def _lan_address() -> str:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("1.1.1.1", 80))
            return str(probe.getsockname()[0])
        except OSError:
            try:
                return socket.gethostbyname(socket.gethostname())
            except OSError:
                return "127.0.0.1"
        finally:
            probe.close()

    @classmethod
    def _handler(
        cls,
        token: str,
        destination: Path,
        on_file: Callable[[Path], None],
    ) -> type[BaseHTTPRequestHandler]:
        page_path = f"/{token}"
        upload_path = f"/{token}/upload"

        class UploadHandler(BaseHTTPRequestHandler):
            server_version = "MediaFlowMobileImport/1"

            def do_GET(self) -> None:  # noqa: N802
                if self.path.rstrip("/") != page_path:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                body = cls._upload_page(upload_path).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                if self.path != upload_path:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    self.send_error(HTTPStatus.BAD_REQUEST, "empty upload")
                    return
                payload = self.rfile.read(length)
                files = cls._decode_upload(
                    self.headers.get("Content-Type", "application/octet-stream"),
                    self.headers.get("X-Filename", "mobile-upload.bin"),
                    payload,
                )
                written: list[Path] = []
                for filename, content in files:
                    target = cls._unique_destination(destination, filename)
                    target.write_bytes(content)
                    written.append(target)
                    on_file(target)
                response = ("已发送 " + str(len(written)) + " 个文件，可以返回 MediaFlow Pro。\n").encode(
                    "utf-8"
                )
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        return UploadHandler

    @staticmethod
    def _decode_upload(
        content_type: str,
        fallback_name: str,
        payload: bytes,
    ) -> list[tuple[str, bytes]]:
        if not content_type.casefold().startswith("multipart/form-data"):
            return [(fallback_name, payload)]
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: "
            + content_type.encode("utf-8")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + payload
        )
        files = [
            (part.get_filename() or fallback_name, part.get_payload(decode=True) or b"")
            for part in message.iter_parts()
            if part.get_content_disposition() == "form-data" and part.get_filename()
        ]
        if not files:
            raise ValueError("上传请求没有文件")
        return files

    @staticmethod
    def _unique_destination(directory: Path, filename: str) -> Path:
        safe_name = Path(filename).name.strip() or "mobile-upload.bin"
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", safe_name)
        candidate = directory / safe_name
        index = 2
        while candidate.exists():
            candidate = directory / f"{Path(safe_name).stem} ({index}){Path(safe_name).suffix}"
            index += 1
        return candidate

    @staticmethod
    def _upload_page(upload_path: str) -> str:
        action = html.escape(upload_path, quote=True)
        return f"""<!doctype html>
<html lang="zh-CN"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>发送到 MediaFlow Pro</title>
<style>body{{font:16px system-ui;background:#111827;color:#f8fafc;max-width:540px;margin:0 auto;padding:32px}}
form{{background:#1f2937;padding:24px;border-radius:14px}}input,button{{box-sizing:border-box;width:100%;padding:14px;margin-top:14px}}
button{{background:#38bdf8;border:0;border-radius:8px;font-weight:700}}</style>
<h1>发送到 MediaFlow Pro</h1><p>选择手机里的视频、录音或图片。</p>
<form method="post" action="{action}" enctype="multipart/form-data">
<input name="files" type="file" accept="video/*,audio/*,image/*" multiple required>
<button type="submit">发送文件</button></form></html>"""
