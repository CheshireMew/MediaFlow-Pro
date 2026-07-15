from __future__ import annotations

from mediaflow.desktop.models import SequenceListModel


def test_dict_list_model_applies_structural_and_value_changes_incrementally() -> None:
    model = SequenceListModel()
    resets: list[None] = []
    inserts: list[tuple[int, int]] = []
    moves: list[tuple[int, int]] = []
    changes: list[tuple[int, int]] = []
    model.modelReset.connect(lambda: resets.append(None))
    model.rowsInserted.connect(
        lambda _parent, first, last: inserts.append((first, last))
    )
    model.rowsMoved.connect(
        lambda _source_parent, first, _last, _destination_parent, destination: moves.append(
            (first, destination)
        )
    )
    model.dataChanged.connect(
        lambda first, last, _roles: changes.append((first.row(), last.row()))
    )

    model.set_items(
        [
            {"sequenceId": "main", "name": "Main"},
            {"sequenceId": "short-a", "name": "A"},
        ]
    )
    model.set_items(
        [
            {"sequenceId": "short-a", "name": "A revised"},
            {"sequenceId": "main", "name": "Main"},
            {"sequenceId": "short-b", "name": "B"},
        ]
    )

    assert resets == []
    assert inserts == [(0, 1), (2, 2)]
    assert moves == [(1, 0)]
    assert changes == [(0, 0)]
    assert [model.get(row)["sequenceId"] for row in range(model.rowCount())] == [
        "short-a",
        "main",
        "short-b",
    ]
