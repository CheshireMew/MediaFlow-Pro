from __future__ import annotations

import math
import os

import psutil


def storage_process_owner() -> dict[str, int | float]:
    return {"pid": os.getpid(), "process_started": psutil.Process(os.getpid()).create_time()}


def storage_owner_is_active(owner: object) -> bool:
    if not isinstance(owner, dict):
        return True
    pid = owner.get("pid")
    process_started = owner.get("process_started")
    if (
        type(pid) is not int
        or pid <= 0
        or not isinstance(process_started, int | float)
        or not math.isfinite(process_started)
    ):
        return True
    try:
        process = psutil.Process(pid)
        return process.is_running() and abs(process.create_time() - float(process_started)) < 0.001
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except (psutil.AccessDenied, OSError):
        return True
