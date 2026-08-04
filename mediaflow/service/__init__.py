"""Resident MediaFlow Editor Service and its public local client."""

from .client import EditorServiceClient
from .discovery import ServiceDiscovery, ServicePaths

__all__ = ["EditorServiceClient", "ServiceDiscovery", "ServicePaths"]
