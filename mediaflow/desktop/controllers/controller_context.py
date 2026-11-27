from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject

from mediaflow.desktop.session_updates import SessionUpdates
from mediaflow.service.client import EditorServiceRpcError


@dataclass(frozen=True, slots=True)
class ControllerScope:
    parent: QObject
    updates: SessionUpdates
    _present_collaboration_conflict: Callable[[EditorServiceRpcError], None]
