from __future__ import annotations

from mediaflow.desktop.session_state import DesktopSessionState


def workspace_action_capabilities(state: DesktopSessionState) -> dict[str, bool]:
    project = state.binding.current
    release_pending = state.requests.closing_project is not None
    closing = state.requests.project_close_future is not None
    close_failed = bool(release_pending and not closing and state.requests.closing_project_error)
    writable = bool(project and not project.read_only)
    return {
        "canEdit": writable,
        "canImport": writable,
        "canStartTasks": writable,
        "canManageTasks": writable,
        "canManageWorkflow": writable,
        "canOpenProject": not release_pending,
        "canCreateProject": not release_pending,
        "canCloseProject": bool(project) and not release_pending,
        "canRetryProjectClose": close_failed,
        "projectClosing": closing,
        "projectReleasePending": release_pending,
    }
