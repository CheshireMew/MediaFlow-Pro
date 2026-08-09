from __future__ import annotations

import pytest

from mediaflow.domain.collaboration import ProjectWritePath, project_write_paths_overlap


@pytest.mark.parametrize(
    ("left", "right"),
    (
        ("/", "/sequences/main"),
        ("/sequences/main", "/sequences/main"),
        ("/sequences/main/", "/sequences/main/clips/clip-1"),
        ("/subtitles/documents/doc-1/text", "/subtitles/documents"),
    ),
)
def test_project_write_paths_overlap_for_root_identity_and_ancestors(
    left: str,
    right: str,
) -> None:
    assert project_write_paths_overlap(left, right) is True
    assert project_write_paths_overlap(right, left) is True


def test_project_write_path_siblings_do_not_overlap() -> None:
    assert project_write_paths_overlap(
        "/sequences/main/clips/clip-1",
        "/sequences/main/clips/clip-2",
    ) is False


@pytest.mark.parametrize("value", ("", "relative/path", "/double//segment", "/parent/../child"))
def test_project_write_path_rejects_noncanonical_values(value: str) -> None:
    with pytest.raises(ValueError):
        ProjectWritePath.parse(value)
