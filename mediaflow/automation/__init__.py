"""Versioned JSON automation contract for the MediaFlow Pro CLI."""

from .contracts import AutomationRequest, describe_contract
from .dispatcher import execute_request

__all__ = ["AutomationRequest", "describe_contract", "execute_request"]
