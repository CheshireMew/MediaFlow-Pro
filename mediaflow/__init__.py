"""MediaFlow Pro desktop application package."""

from typing import TYPE_CHECKING, Any

from .environment import load_project_environment

load_project_environment()

if TYPE_CHECKING:
    from .domain.project import Project, ProjectProfile

__all__ = ["Project", "ProjectProfile"]

__version__ = "2.0.0"


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # The launcher must be able to start the resident service before either
    # process loads the project model. Preserve the public package exports.
    from .domain.project import Project, ProjectProfile

    globals().update(Project=Project, ProjectProfile=ProjectProfile)
    return globals()[name]


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
