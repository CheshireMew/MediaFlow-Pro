from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psutil
from pydantic import Field

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.model_base import DomainModel
from mediaflow.environment import SERVICE_STATE_DIRECTORY_VARIABLE, configured_path

SERVICE_PROTOCOL = "mediaflow-editor"
SERVICE_PROTOCOL_VERSION = 4


@dataclass(frozen=True, slots=True)
class ServicePaths:
    """Small per-user coordination files; media and caches never live here."""

    root: Path
    lock: Path
    discovery: Path
    log: Path

    @classmethod
    def discover(cls) -> ServicePaths:
        configured = configured_path(SERVICE_STATE_DIRECTORY_VARIABLE)
        if configured is not None:
            root = configured
        elif sys.platform == "win32":
            local = os.environ.get("LOCALAPPDATA", "").strip()
            if not local:
                raise RuntimeError("LOCALAPPDATA is required for Editor Service discovery")
            root = Path(local) / "MediaFlow Pro" / "service"
        elif sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support" / "MediaFlow Pro" / "service"
        else:
            runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
            state = os.environ.get("XDG_STATE_HOME", "").strip()
            root = (
                Path(runtime) / "mediaflow-pro"
                if runtime
                else (Path(state) if state else Path.home() / ".local" / "state")
                / "mediaflow-pro"
                / "service"
            )
        root = root.expanduser().resolve()
        return cls(
            root=root,
            lock=root / "service.lock",
            discovery=root / "discovery.json",
            log=root / "service.log",
        )

    def prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            self.root.chmod(0o700)

    def archive_discovery(self, *, expected_pid: int | None = None) -> Path | None:
        if not self.discovery.is_file():
            return None
        if expected_pid is not None:
            try:
                current = ServiceDiscovery.read(self.discovery)
            except (OSError, ValueError, json.JSONDecodeError):
                return None
            if current.pid != expected_pid:
                return None
        archive = self.root / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        destination = archive / f"discovery-{time.time_ns()}.json"
        self.discovery.replace(destination)
        if sys.platform != "win32":
            destination.chmod(0o600)
        return destination


class ServiceDiscovery(DomainModel):
    schema_version: int = Field(default=2, ge=2, le=2)
    protocol: str = SERVICE_PROTOCOL
    protocol_version: int = Field(default=SERVICE_PROTOCOL_VERSION, ge=1)
    pid: int = Field(gt=0)
    process_started_at: float = Field(gt=0)
    started_at: float = Field(gt=0)
    host: str = "127.0.0.1"
    port: int = Field(gt=0, le=65535)
    token: str = Field(min_length=32)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def websocket_url(self) -> str:
        return f"ws://{self.host}:{self.port}/events"

    def belongs_to_live_process(self) -> bool:
        try:
            process = psutil.Process(self.pid)
            return (
                process.is_running()
                and process.status() not in {psutil.STATUS_DEAD, psutil.STATUS_ZOMBIE}
                and abs(process.create_time() - self.process_started_at) < 0.01
            )
        except (psutil.Error, OSError):
            return False

    def write(self, path: Path) -> None:
        atomic_write_text(
            path,
            self.model_dump_json(indent=2) + "\n",
            durable=True,
            mode=0o600 if sys.platform != "win32" else None,
        )

    @classmethod
    def read(cls, path: Path) -> ServiceDiscovery:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Editor Service discovery root must be an object")
        return cls.model_validate(value)
