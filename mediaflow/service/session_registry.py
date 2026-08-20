from __future__ import annotations

import math
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mediaflow.application.project_command_queue import ProjectCommandQueue
from mediaflow.application.project_revision_policy import resolve_project_revision
from mediaflow.automation.contracts import AutomationRequest
from mediaflow.composition import EditorApplication, EditorProject
from mediaflow.domain.collaboration import ProjectChangeEvent
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.storage_names import (
    PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT,
    PROJECT_ROOT_PATH_UTF16_LIMIT,
    safe_child_path,
)
from mediaflow.infrastructure.project_migration_runner import ProjectUpgradeRequiredError
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.storage_paths import default_project_root

from .codec import decode_transport
from .events import EventHub, ServiceEvent


def project_path(value: str | None) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError("project is required")
    path = Path(text).expanduser().resolve()
    return path.parent if path.name == "project.mfp" else path


@dataclass(slots=True)
class ProjectSession:
    project: EditorProject
    lifetime_condition: threading.Condition
    write_lock: ProjectCommandQueue
    task_subscription: int
    active_calls: int = 0


class ProjectSessionRegistry:
    """Owns project lifetimes, writer locks, and desktop client leases."""

    def __init__(self, application: EditorApplication, events: EventHub):
        self.application = application
        self.events = events
        self._sessions: dict[Path, ProjectSession] = {}
        self._desktop_clients: dict[Path, set[str]] = {}
        self._sessions_lock = threading.RLock()

    @contextmanager
    def leased_session(
        self,
        path: Path,
        *,
        allow_upgrade: bool = False,
        require_writable: bool = True,
    ) -> Iterator[ProjectSession]:
        # Session lookup and lifetime acquisition are one boundary. The
        # lifetime lease prevents release without forcing long-running task
        # admission handlers to hold the foreground project write gate.
        with self._sessions_lock:
            session = self.open_session(
                path,
                allow_upgrade=allow_upgrade,
                require_writable=require_writable,
            )
            with session.lifetime_condition:
                session.active_calls += 1
        try:
            yield session
        finally:
            with session.lifetime_condition:
                session.active_calls -= 1
                if session.active_calls == 0:
                    session.lifetime_condition.notify_all()

    @contextmanager
    def leased_created_session(
        self,
        envelope: AutomationRequest,
    ) -> Iterator[ProjectSession]:
        with self._sessions_lock:
            session = self._create_session(envelope)
            with session.lifetime_condition:
                session.active_calls += 1
        try:
            yield session
        finally:
            with session.lifetime_condition:
                session.active_calls -= 1
                if session.active_calls == 0:
                    session.lifetime_condition.notify_all()

    @contextmanager
    def locked_session(
        self,
        path: Path,
        *,
        allow_upgrade: bool = False,
        require_writable: bool = True,
    ) -> Iterator[ProjectSession]:
        with self.leased_session(
            path,
            allow_upgrade=allow_upgrade,
            require_writable=require_writable,
        ) as session:
            with session.write_lock:
                yield session

    def open_session(
        self,
        path: Path,
        *,
        allow_upgrade: bool = False,
        require_writable: bool = True,
    ) -> ProjectSession:
        root = path.expanduser().resolve()
        with self._sessions_lock:
            session = self._sessions.get(root)
            if session is not None:
                if not require_writable or session.project.owns_project_writer:
                    return session
                self._sessions.pop(root)
                self._close_bound_session(session)
            if not allow_upgrade:
                try:
                    with ProjectRepository.open(root, writable=False):
                        pass
                except ProjectUpgradeRequiredError:
                    raise
            project = self.application.open_project(root, writable=True)
            if not project.owns_project_writer:
                if require_writable:
                    project.close(timeout=0)
                    raise RuntimeError(f"Editor Service could not own the project writer lock: {root}")
            session = self._bind_session(project)
            self._sessions[root] = session
            return session

    def create_desktop_project(
        self,
        path: Path,
        name: str,
        profile_value: Any,
        profile_confirmed: bool,
        client_id: str,
    ) -> dict[str, Any]:
        root = path.expanduser().resolve()
        client_id = _required_desktop_client_id(client_id)
        profile = decode_transport(profile_value)
        if not isinstance(profile, ProjectProfile):
            profile = ProjectProfile.model_validate(profile)
        with self._sessions_lock:
            session = self._sessions.get(root)
            if session is None:
                if (root / "project.mfp").is_file():
                    project = self.application.open_project(root, writable=True)
                    document = project.get_project()
                    main_sequence = project.get_sequence(document.main_sequence_id)
                    if (
                        document.name != name
                        or main_sequence.profile != profile
                        or main_sequence.profile_confirmed != profile_confirmed
                    ):
                        project.close(timeout=0)
                        raise FileExistsError(f"A different project already exists: {root}")
                else:
                    project = self.application.create_project(
                        root,
                        name,
                        profile if profile_confirmed else None,
                    )
                session = self._bind_session(project)
                self._sessions[root] = session
            self._desktop_clients.setdefault(root, set()).add(client_id)
            with session.write_lock:
                return self._desktop_descriptor(session)

    def open_desktop_project(self, path: Path, client_id: str) -> dict[str, Any]:
        root = path.expanduser().resolve()
        client_id = _required_desktop_client_id(client_id)
        with self._sessions_lock:
            session = self.open_session(
                root,
                allow_upgrade=True,
                require_writable=False,
            )
            self._desktop_clients.setdefault(root, set()).add(client_id)
            with session.write_lock:
                return self._desktop_descriptor(session)

    def release_desktop_project(
        self,
        path: Path,
        client_id: str,
        timeout_seconds: float = 5.0,
    ) -> dict[str, bool]:
        root = path.expanduser().resolve()
        client_id = _required_desktop_client_id(client_id)
        if not math.isfinite(timeout_seconds) or not 0 <= timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 0 and 60")
        with self._sessions_lock:
            clients = self._desktop_clients.get(root)
            if clients is None or client_id not in clients:
                return {"released": False, "retained_for_tasks": False}
            clients.remove(client_id)
            if clients:
                return {"released": True, "retained_for_tasks": False}
            self._desktop_clients.pop(root, None)
            session = self._sessions.get(root)
            if session is None:
                return {"released": True, "retained_for_tasks": False}
            self._wait_for_session_calls(session)
            with session.write_lock:
                if self._session_has_active_tasks(session):
                    return {"released": True, "retained_for_tasks": True}
                self._sessions.pop(root, None)
                self._close_bound_session(session, timeout_seconds=timeout_seconds)
            return {"released": True, "retained_for_tasks": False}

    def service_status(self) -> dict[str, Any]:
        with self._sessions_lock:
            sessions = tuple(self._sessions.items())
            desktop_client_count = sum(len(clients) for clients in self._desktop_clients.values())
        active_tasks: list[dict[str, str]] = []
        for path, session in sessions:
            with session.write_lock:
                active_tasks.extend(
                    {
                        "project": str(path),
                        "task_id": task.id,
                        "status": task.status.value,
                    }
                    for task in session.project.list_tasks()
                    if task.status.is_active
                )
        return {
            "project_session_count": len(sessions),
            "desktop_client_count": desktop_client_count,
            "active_task_count": len(active_tasks),
            "active_tasks": active_tasks,
        }

    def has_session(self, path: Path) -> bool:
        with self._sessions_lock:
            return path.expanduser().resolve() in self._sessions

    def cancel_all_tasks(self) -> int:
        cancelled = 0
        with self._sessions_lock:
            sessions = tuple(self._sessions.values())
        for session in sessions:
            with session.write_lock:
                active = [task for task in session.project.list_tasks() if task.status.is_active]
                if active:
                    session.project.cancel_all_tasks()
                    cancelled += len(active)
        return cancelled

    def update_project_settings(self) -> None:
        with self._sessions_lock:
            sessions = tuple(self._sessions.values())
        for session in sessions:
            with session.write_lock:
                session.project.update_settings(self.application.service_settings)

    def close(self) -> None:
        with self._sessions_lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
            self._desktop_clients.clear()
            for session in sessions:
                self._wait_for_session_calls(session)
                with session.write_lock:
                    self._close_bound_session(session)

    @staticmethod
    def _desktop_descriptor(session: ProjectSession) -> dict[str, Any]:
        project = session.project
        return {
            "project": str(project.project_dir),
            "project_id": project.get_project().id,
            "project_revision": project.content_revision(),
            "project_event_cursor": project.project_event_cursor(),
            "read_only": project.read_only,
            "owns_project_writer": project.owns_project_writer,
        }

    def _create_session(self, envelope: AutomationRequest) -> ProjectSession:
        if str(envelope.project or "").strip():
            raise ValueError(
                "project.create does not accept project; MediaFlow Pro owns the default project root"
            )
        root = safe_child_path(
            default_project_root(),
            str(envelope.arguments["directory_name"]),
            max_path_utf16_units=PROJECT_ROOT_PATH_UTF16_LIMIT,
            max_component_utf16_units=PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT,
        ).resolve()
        with self._sessions_lock:
            existing = self._sessions.get(root)
            if existing is not None:
                if existing.project.owns_project_writer:
                    return existing
                self._sessions.pop(root)
                self._close_bound_session(existing)
            if (root / "project.mfp").is_file():
                project = self.application.open_project(root, writable=True)
                existing_project = project.get_project()
                requested_profile = ProjectProfile.model_validate(envelope.arguments["profile"])
                existing_profile = project.get_sequence(existing_project.main_sequence_id).profile
                if (
                    existing_project.name != str(envelope.arguments["name"])
                    or existing_profile != requested_profile
                ):
                    project.close(timeout=0)
                    raise ValueError("Existing project does not match the retried project.create request")
            else:
                project = self.application.create_project(
                    root,
                    str(envelope.arguments["name"]),
                    ProjectProfile.model_validate(envelope.arguments["profile"]),
                )
            session = self._bind_session(project)
            self._sessions[root] = session
            return session

    def _bind_session(self, project: EditorProject) -> ProjectSession:
        project_path_value = str(project.project_dir)
        project.observe_implicit_project_events(self.publish_project_event)

        def observe_task(event) -> None:
            self.events.publish_from_worker(
                ServiceEvent(
                    "task.changed",
                    self.task_event_document(project_path_value, event),
                )
            )

        subscription = project.subscribe_task_events(
            observe_task,
            include_snapshot=False,
        )
        return ProjectSession(
            project=project,
            lifetime_condition=threading.Condition(),
            write_lock=project.write_gate,
            task_subscription=subscription,
        )

    @staticmethod
    def _wait_for_session_calls(session: ProjectSession) -> None:
        with session.lifetime_condition:
            while session.active_calls:
                session.lifetime_condition.wait()

    @staticmethod
    def _close_bound_session(
        session: ProjectSession,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        session.project.unsubscribe_task_events(session.task_subscription)
        session.project.close(timeout=timeout_seconds)

    @staticmethod
    def _session_has_active_tasks(session: ProjectSession) -> bool:
        return any(task.status.is_active for task in session.project.list_tasks())

    @staticmethod
    def task_event_document(project_path_value: str, event) -> dict[str, Any]:
        document = asdict(event)
        document["task_revision"] = document.pop("revision")
        document["project_path"] = project_path_value
        return document

    def publish_project_event(self, event: ProjectChangeEvent) -> None:
        self.events.publish_from_worker(ServiceEvent("project.changed", event.model_dump(mode="json")))

    @staticmethod
    def resolve_revision(
        project: EditorProject,
        envelope: AutomationRequest,
        write_set: list[str],
    ) -> tuple[AutomationRequest, int | None]:
        current = project.content_revision()
        resolution = resolve_project_revision(
            base_revision=envelope.base_revision,
            current_revision=current,
            write_set=write_set,
            events=(
                project.list_project_events_after_revision(envelope.base_revision)
                if envelope.base_revision is not None and envelope.base_revision < current
                else []
            ),
            conflict_reason="one or more fields changed after the requested base revision",
        )
        return (
            envelope.model_copy(update={"base_revision": resolution.effective_revision}),
            resolution.rebased_from,
        )


def _required_desktop_client_id(value: str) -> str:
    client_id = value.strip()
    if not client_id:
        raise ValueError("client_id is required for desktop project sessions")
    return client_id
