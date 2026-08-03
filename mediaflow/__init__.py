"""MediaFlow Pro desktop application package."""

from .environment import load_project_environment

load_project_environment()

from .domain.project import Project, ProjectProfile  # noqa: E402

__all__ = ["Project", "ProjectProfile"]

__version__ = "2.0.0"
