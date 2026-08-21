from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, WSMsgType

from mediaflow.domain.collaboration import ActorIdentity
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.runtime import DesktopRuntimeDescriptor
from mediaflow.domain.settings import DesktopSettings, ServiceSettings
from mediaflow.infrastructure.font_assets import subtitle_font_options
from mediaflow.infrastructure.settings_repository import DesktopSettingsRepository

from .client import (
    EditorServiceClient,
    EditorServiceRpcError,
    call_sync,
    close_sync_transport,
)
from .codec import decode_transport, encode_transport
from .remote_project import RemoteEditorProject

logger = logging.getLogger(__name__)


class DesktopEditorApplication:
    """Desktop-local rendering facilities with service-owned persistent projects."""

    def __init__(self):
        self._actor = ActorIdentity(
            kind="human",
            id=f"desktop-{uuid.uuid4().hex}",
            name="MediaFlow Pro desktop",
        )
        descriptor_value = call_sync("system.runtime.inspect")
        if not isinstance(descriptor_value, dict):
            raise RuntimeError("Editor Service returned an invalid runtime descriptor")
        self.runtime_descriptor = DesktopRuntimeDescriptor.model_validate(descriptor_value)
        workspace = call_sync(
            "workspace.attach",
            {"client_id": self._actor.id},
        )
        if not isinstance(workspace, dict) or not workspace.get("workspace_session_id"):
            raise RuntimeError("Editor Service returned an invalid workspace session")
        self.workspace_session_id = str(workspace["workspace_session_id"])
        settings = decode_transport(call_sync("desktop.application.settings"))
        if not isinstance(settings, ServiceSettings):
            raise RuntimeError("Editor Service returned invalid application settings")
        self._service_settings = settings
        self._desktop_settings_repository = DesktopSettingsRepository()
        self._desktop_settings = self._desktop_settings_repository.load()
        self.cookies = _RemoteCookieStore()

    @property
    def service_settings(self) -> ServiceSettings:
        return self._service_settings

    @property
    def desktop_settings(self) -> DesktopSettings:
        return self._desktop_settings

    @property
    def native_qml_root(self) -> Path | None:
        path = Path(self.runtime_descriptor.native_qml)
        return path if path.is_dir() else None

    @property
    def mlt_runtime_root(self) -> str:
        return self.runtime_descriptor.mlt_root

    @property
    def mlt_library_path(self) -> str:
        return self.runtime_descriptor.mlt_library

    @property
    def mlt_repository_path(self) -> str:
        return self.runtime_descriptor.mlt_repository

    @property
    def mlt_preview_repository_path(self) -> str:
        return self.runtime_descriptor.mlt_preview_repository

    @property
    def mlt_data_path(self) -> str:
        return self.runtime_descriptor.mlt_data

    def replace_service_settings(self, settings: ServiceSettings) -> None:
        value = call_sync(
            "desktop.application.settings.replace",
            {"settings": encode_transport(settings)},
        )
        accepted = decode_transport(value)
        if not isinstance(accepted, ServiceSettings):
            raise RuntimeError("Editor Service returned invalid application settings")
        self._service_settings = accepted

    def replace_desktop_settings(self, settings: DesktopSettings) -> None:
        self._desktop_settings_repository.save(settings)
        self._desktop_settings = settings.model_copy(deep=True)

    def close_client_transport(self) -> None:
        try:
            call_sync(
                "workspace.detach",
                {
                    "workspace_session_id": self.workspace_session_id,
                    "client_id": self._actor.id,
                },
                start_if_needed=False,
            )
        except (EditorServiceRpcError, RuntimeError):
            logger.exception("Failed to detach the desktop workspace session")
        finally:
            close_sync_transport()

    def discover_encoder_policy_options(self) -> list[dict]:
        return self._application_call("discover_encoder_policy_options")

    def search_media_resources(self, **kwargs: Any) -> dict[str, Any]:
        value = self._application_call("search_media_resources", **kwargs)
        if not isinstance(value, dict):
            raise RuntimeError("Editor Service returned an invalid media resource catalog")
        return value

    @property
    def default_media_directory(self) -> str:
        return self._application_call("default_media_directory")

    def subtitle_font_options(self) -> list[dict]:
        # Font availability is a GUI-process capability. The resident service
        # must stay headless and never initialize QtGui just to inspect fonts.
        return subtitle_font_options()

    def run_runtime_tool(
        self,
        operation: str,
        arguments: dict | None = None,
        *,
        progress: Callable[[Any], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Any:
        if check_cancelled is not None:
            check_cancelled()
        result = asyncio.run(
            self._run_runtime_tool_with_events(
                operation,
                arguments or {},
                progress=progress,
            )
        )
        if check_cancelled is not None:
            check_cancelled()
        return result

    @staticmethod
    async def _run_runtime_tool_with_events(
        operation: str,
        arguments: dict[str, Any],
        *,
        progress: Callable[[Any], None] | None,
    ) -> Any:
        from mediaflow.domain.progress import OperationProgress

        client = await EditorServiceClient.connect()
        timeout = ClientTimeout(total=None, connect=5, sock_read=None)
        async with ClientSession(timeout=timeout) as session:
            async with session.ws_connect(
                client.discovery.websocket_url,
                headers={
                    "Authorization": f"Bearer {client.discovery.token}",
                },
                heartbeat=20,
            ) as websocket:
                ready = await websocket.receive_json()
                if not isinstance(ready, dict) or ready.get("type") != "service.ready":
                    raise RuntimeError("Editor Service event stream did not become ready")
                await websocket.send_json({"type": "service.subscribe"})
                subscribed = await websocket.receive_json()
                if not isinstance(subscribed, dict) or subscribed.get("type") != "service.subscribed":
                    raise RuntimeError("Editor Service event subscription was not acknowledged")

                async def consume_progress() -> None:
                    async for message in websocket:
                        if message.type != WSMsgType.TEXT:
                            if message.type in {WSMsgType.CLOSE, WSMsgType.ERROR}:
                                return
                            continue
                        value = json.loads(message.data)
                        if not isinstance(value, dict) or value.get("type") != "runtime.changed":
                            continue
                        payload = value.get("payload")
                        if (
                            not isinstance(payload, dict)
                            or payload.get("operation") != operation
                            or not isinstance(payload.get("progress"), dict)
                            or progress is None
                        ):
                            continue
                        progress(OperationProgress.model_validate(payload["progress"]))

                consumer = asyncio.create_task(consume_progress())
                try:
                    value = await client.call(
                        "desktop.application.call",
                        {
                            "command": "run_runtime_tool",
                            "args": encode_transport([operation]),
                            "kwargs": encode_transport({"arguments": arguments}),
                        },
                        session=session,
                    )
                    return decode_transport(value)
                finally:
                    consumer.cancel()
                    await asyncio.gather(consumer, return_exceptions=True)

    def cancel_runtime_tool(self) -> dict[str, Any]:
        value = self._application_call("cancel_runtime_tool")
        if not isinstance(value, dict):
            raise RuntimeError("Editor Service returned an invalid runtime cancellation result")
        return value

    def analyze_download_url(
        self,
        url: str,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ):
        if check_cancelled is not None:
            check_cancelled()
        return self._application_call("analyze_download_url", url)

    def test_llm_provider(self, provider) -> None:
        self._application_call("test_llm_provider", provider)

    def runtime_tool_status(self) -> dict:
        return self._application_call("runtime_tool_status")

    def installed_asr_models(self) -> frozenset[str]:
        return self._application_call("installed_asr_models")

    def recent_projects(self, paths: list[str]):
        return self._application_call("recent_projects", paths)

    def asset_thumbnail_paths(self, project_dir: str | Path, **kwargs: Any):
        return self._application_call("asset_thumbnail_paths", project_dir, **kwargs)

    def timeline_filmstrip_paths(
        self,
        project_dir: str | Path,
        sequence_id: str,
        **kwargs: Any,
    ):
        return self._application_call(
            "timeline_filmstrip_paths",
            project_dir,
            sequence_id,
            **kwargs,
        )

    def cancel_timeline_filmstrip_requests(
        self,
        project_dir: str | Path,
        **kwargs: Any,
    ) -> None:
        self._application_call(
            "cancel_timeline_filmstrip_requests",
            project_dir,
            **kwargs,
        )

    def write_preview_snapshot(self, project_dir: str | Path, state, **kwargs: Any):
        return self._application_call("write_preview_snapshot", project_dir, state, **kwargs)

    def write_asset_preview_snapshot(
        self,
        project_dir: str | Path,
        sequence_id: str,
        asset_id: str,
    ):
        return self._application_call(
            "write_asset_preview_snapshot",
            project_dir,
            sequence_id,
            asset_id,
        )

    @staticmethod
    def _application_call(command: str, *args: Any, **kwargs: Any) -> Any:
        value = call_sync(
            "desktop.application.call",
            {
                "command": command,
                "args": encode_transport(list(args)),
                "kwargs": encode_transport(kwargs),
            },
        )
        return decode_transport(value)

    def create_project(
        self,
        root: str | Path,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> RemoteEditorProject:
        path = Path(root).expanduser().resolve()
        profile_confirmed = profile is not None
        descriptor = call_sync(
            "project.create",
            {
                "project": str(path),
                "name": name,
                "profile": encode_transport(profile or ProjectProfile()),
                "profile_confirmed": profile_confirmed,
                "client_id": self._actor.id,
            },
        )
        if not isinstance(descriptor, dict):
            raise RuntimeError("Editor Service returned an invalid project descriptor")
        self._attach_workspace(path)
        return RemoteEditorProject(
            descriptor,
            actor=self._actor,
            workspace_session_id=self.workspace_session_id,
        )

    def open_project(
        self,
        root: str | Path,
        *,
        writable: bool = True,
    ) -> RemoteEditorProject:
        if not writable:
            raise ValueError("Desktop service sessions are writable single-writer sessions")
        descriptor = call_sync(
            "project.open",
            {
                "project": str(Path(root).expanduser().resolve()),
                "client_id": self._actor.id,
            },
        )
        if not isinstance(descriptor, dict):
            raise RuntimeError("Editor Service returned an invalid project descriptor")
        self._attach_workspace(Path(str(descriptor["project"])))
        return RemoteEditorProject(
            descriptor,
            actor=self._actor,
            workspace_session_id=self.workspace_session_id,
        )

    def _attach_workspace(self, project: Path) -> None:
        call_sync(
            "workspace.attach",
            {
                "workspace_session_id": self.workspace_session_id,
                "client_id": self._actor.id,
                "project": str(project.resolve()),
            },
        )


def create_desktop_editor_application() -> DesktopEditorApplication:
    started_at = time.monotonic()
    application = DesktopEditorApplication()
    logger.info("Desktop Editor Service bridge ready in %.3fs", time.monotonic() - started_at)
    return application


class _RemoteCookieStore:
    def status(self, *args: Any) -> Any:
        return self._call("status", *args)

    def save(self, *args: Any) -> Any:
        return self._call("save", *args)

    def clear(self, *args: Any) -> Any:
        return self._call("clear", *args)

    @staticmethod
    def _call(command: str, *args: Any) -> Any:
        value = call_sync(
            "desktop.application.cookies",
            {
                "command": command,
                "args": encode_transport(list(args)),
            },
        )
        return decode_transport(value)
