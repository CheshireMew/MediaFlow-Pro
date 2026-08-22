"""Resident MediaFlow Editor Service and its public local client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import EditorServiceClient
    from .discovery import ServiceDiscovery, ServicePaths

__all__ = ["EditorServiceClient", "ServiceDiscovery", "ServicePaths"]


def __getattr__(name: str) -> Any:
    if name == "EditorServiceClient":
        from .client import EditorServiceClient

        return EditorServiceClient
    if name in {"ServiceDiscovery", "ServicePaths"}:
        from .discovery import ServiceDiscovery, ServicePaths

        return {"ServiceDiscovery": ServiceDiscovery, "ServicePaths": ServicePaths}[name]
    raise AttributeError(name)
