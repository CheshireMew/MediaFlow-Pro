from __future__ import annotations

from pathlib import Path

from mediaflow.atomic_file import atomic_write_text, unique_temporary_sibling


def test_atomic_text_write_uses_a_short_sibling_for_a_long_destination(
    tmp_path: Path,
) -> None:
    parent = tmp_path
    destination_name = f"{'preview-graph-' * 7}snapshot.mlt"
    padding_length = 250 - len(str(parent / destination_name)) - 1
    assert padding_length > 0
    parent /= "d" * padding_length
    parent.mkdir(parents=True)
    destination = parent / destination_name
    assert len(str(destination)) == 250

    temporary = unique_temporary_sibling(destination, label="preview")

    assert temporary.parent == destination.parent
    assert temporary.suffix == destination.suffix
    assert destination.stem not in temporary.name
    assert len(str(temporary)) < len(str(destination))

    atomic_write_text(destination, "<mlt />")

    assert destination.read_text(encoding="utf-8") == "<mlt />"
    assert not list(parent.glob(".mf-*.tmp.mlt"))
