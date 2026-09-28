from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from pydantic import JsonValue

from mediaflow.application.presentation_models import RecentProjectSnapshot
from mediaflow.domain.downloads import DownloadPlan
from mediaflow.domain.runtime_capabilities import RuntimeComponentInstallResult
from mediaflow.domain.settings import LlmProviderSettings
from mediaflow.domain.timeline import TimelineState

from .commands import (
    DesktopCommand,
    DesktopCommandRequest,
    DesktopCommandResult,
    command_models,
    command_signature,
)
from .execution import ServiceWorkload


@dataclass(frozen=True, slots=True)
class ApplicationCommand:
    name: str
    workload: ServiceWorkload
    handler: Callable[..., object]
    signature: inspect.Signature
    request_model: type[DesktopCommandRequest]
    result_model: type[DesktopCommandResult]

    def arguments_from_call(self, args: tuple[object, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        bound = self.signature.bind(*args, **kwargs)
        request = self.request_model.model_validate(dict(bound.arguments))
        return DesktopCommand.request_arguments(request)

    def invoke(self, runtime: object, arguments: object) -> object:
        request = self.request_model.model_validate(arguments)
        value = self.handler(runtime, **DesktopCommand.request_arguments(request))
        return self.validate_result(value)

    def validate_result(self, value: object) -> object:
        return self.result_model.model_validate({"value": value}).value


# These adapters are the service-owned public surface: callbacks and concrete
# services never cross the wire. Each handler declares its complete arguments
# and result, and is bound directly rather than resolved from client strings.
def analyze_download_url(self: Any, url: str) -> DownloadPlan:
    return self.application.analyze_download_url(url)


def discover_encoder_policy_options(self: Any) -> list[dict[str, JsonValue]]:
    return self.application.discover_encoder_policy_options()


def installed_asr_models(self: Any) -> frozenset[str]:
    return self.application.installed_asr_models()


def recent_projects(self: Any, paths: list[str]) -> RecentProjectSnapshot:
    return self.application.recent_projects(paths)


def search_media_resources(
    self: Any,
    *,
    color_mode: str = "sdr_bt709",
    category: str | None = None,
    query: str = "",
    tags: list[str] | None = None,
    capabilities: list[str] | None = None,
) -> dict[str, JsonValue]:
    return self.application.search_media_resources(
        color_mode=color_mode, category=category, query=query, tags=tags, capabilities=capabilities,
    )


def runtime_tool_status(self: Any) -> dict[str, JsonValue]:
    return self.application.runtime_tool_status()


def test_llm_provider(self: Any, provider: LlmProviderSettings) -> None:
    self.application.test_llm_provider(provider)


def run_runtime_tool(
    self: Any,
    operation: Literal[
        "inspect", "update_ytdlp", "install_components", "install_speaker_clustering", "prewarm_asr_cli"
    ],
    *,
    arguments: dict[str, JsonValue] | None = None,
) -> dict[str, RuntimeComponentInstallResult] | dict[str, JsonValue] | str:
    return self._run_runtime_tool(operation, arguments or {})


def cancel_runtime_tool(self: Any) -> dict[str, JsonValue]:
    return self.cancel_runtime_tool()


def asset_thumbnail_paths(
    self: Any, project_dir: str | Path, *, width: int = 160, height: int = 90,
) -> dict[str, str]:
    return self.application.asset_thumbnail_paths(project_dir, width=width, height=height)


def timeline_filmstrip_paths(
    self: Any,
    project_dir: str | Path,
    sequence_id: str,
    *,
    visible_start_frame: int,
    visible_end_frame: int,
    pixels_per_frame: float,
    height: int = 46,
    request_owner: str | None = None,
    request_generation: int | None = None,
) -> list[dict[str, JsonValue]]:
    return self.application.timeline_filmstrip_paths(
        project_dir, sequence_id,
        visible_start_frame=visible_start_frame, visible_end_frame=visible_end_frame,
        pixels_per_frame=pixels_per_frame, height=height,
        request_owner=request_owner, request_generation=request_generation,
    )


def cancel_timeline_filmstrip_requests(
    self: Any, project_dir: str | Path, *, request_owner: str, request_generation: int,
) -> None:
    self.application.cancel_timeline_filmstrip_requests(
        project_dir, request_owner=request_owner, request_generation=request_generation,
    )


def write_preview_snapshot(
    self: Any,
    project_dir: str | Path,
    state: TimelineState,
    *,
    use_proxies: bool,
    prefer_sdr_preview_proxy: bool,
) -> Path:
    return self.application.write_preview_snapshot(
        project_dir, state, use_proxies=use_proxies, prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
    )


def write_asset_preview_snapshot(
    self: Any, project_dir: str | Path, sequence_id: str, asset_id: str,
) -> Path:
    return self.application.write_asset_preview_snapshot(project_dir, sequence_id, asset_id)


def cookie_status(self: Any, domain: str) -> dict[str, JsonValue]:
    return self.application.cookies.status(domain)


def cookie_save(self: Any, domain: str, cookies: list[dict[str, JsonValue]]) -> Path:
    return self.application.cookies.save(domain, cookies)


def cookie_clear(self: Any, domain: str) -> bool:
    return self.application.cookies.clear(domain)


def _registry() -> dict[str, ApplicationCommand]:
    groups: tuple[tuple[ServiceWorkload, tuple[Callable[..., object], ...]], ...] = (
        ("tool", (analyze_download_url, run_runtime_tool, test_llm_provider, cookie_status, cookie_save,
                  cookie_clear)),
        ("preview", (asset_thumbnail_paths, timeline_filmstrip_paths, write_preview_snapshot,
                     write_asset_preview_snapshot)),
        ("runtime", (discover_encoder_policy_options, installed_asr_models, recent_projects,
                     search_media_resources, runtime_tool_status)),
        ("control", (cancel_runtime_tool, cancel_timeline_filmstrip_requests)),
    )
    commands = {}
    for workload, handlers in groups:
        for handler in handlers:
            name = handler.__name__
            if name in commands:
                raise RuntimeError(f"Duplicate application command: {name}")
            signature, annotations, result = command_signature(handler)
            request_model, result_model = command_models(
                "application", name, signature, annotations, result,
            )
            commands[name] = ApplicationCommand(
                name, workload, handler, signature, request_model, result_model,
            )
    return commands


APPLICATION_COMMANDS = MappingProxyType(_registry())


def application_command(name: str) -> ApplicationCommand:
    try:
        return APPLICATION_COMMANDS[name]
    except KeyError as error:
        raise ValueError(f"Unknown desktop application command: {name}") from error
