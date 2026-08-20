from __future__ import annotations

from PySide6.QtCore import Slot

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import WorkspaceWorkflowScope


class WorkspaceWorkflowController(ControllerFacet[WorkspaceWorkflowScope]):
    @Slot(str, str)
    @report_ui_errors
    def continueWorkflow(self, run_id: str, target_language: str = "") -> None:
        self._session._require_writable()
        self._session._apply_workflow_update(
            self._session.state.binding.require_current().continue_workflow(
                run_id,
                target_language=target_language,
            )
        )

    @Slot(str)
    @report_ui_errors
    def skipWorkflow(self, run_id: str) -> None:
        self._session._require_writable()
        self._session._apply_workflow_update(
            self._session.state.binding.require_current().skip_workflow(run_id)
        )

    @Slot(str)
    @report_ui_errors
    def cancelWorkflow(self, run_id: str) -> None:
        self._session._require_writable()
        self._session._apply_workflow_update(
            self._session.state.binding.require_current().cancel_workflow(run_id)
        )
        self._session.updates.commit(workflow=True)
