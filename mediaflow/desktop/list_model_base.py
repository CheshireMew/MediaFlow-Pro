from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    QTimer,
    Slot,
)

_INVALID_MODEL_INDEX = QModelIndex()


class DictListModel(QAbstractListModel):
    def __init__(self, roles: list[str], parent: QObject | None = None):
        super().__init__(parent)
        if not roles or len(set(roles)) != len(roles):
            raise ValueError("Model roles must be non-empty and unique")
        self._roles = roles
        self._role_numbers = {
            int(Qt.ItemDataRole.UserRole) + index + 1: role for index, role in enumerate(roles)
        }
        self._items: list[dict[str, Any]] = []
        self._key_rows: dict[str, int] = {}
        self._deferred_role_changes: dict[int, set[int]] = {}
        self._deferred_change_scheduled = False
        self._revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    def roleNames(self) -> dict[int, QByteArray]:
        return {number: QByteArray(name.encode("utf-8")) for number, name in self._role_numbers.items()}

    def rowCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = _INVALID_MODEL_INDEX,
    ) -> int:
        return 0 if parent.isValid() else len(self._items)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        role_name = self._role_numbers.get(role)
        return self._items[index.row()].get(role_name) if role_name else None

    def set_items(self, items: list[dict[str, Any]]) -> None:
        self._validate_items(items)
        self._deferred_role_changes.clear()
        self._set_items(items)
        self._revision += 1

    def set_items_deferred(self, items: list[dict[str, Any]]) -> None:
        """Publish stable-row value changes on the next Qt event turn."""

        self._validate_items(items)
        key_role = self._roles[0]
        before_keys = [item.get(key_role) for item in self._items]
        after_keys = [item.get(key_role) for item in items]
        if before_keys != after_keys or any(key is None for key in after_keys):
            self.set_items(items)
            return

        role_by_name = {name: number for number, name in self._role_numbers.items()}
        for row, after in enumerate(items):
            before = self._items[row]
            changed_roles = {
                role_by_name[name] for name in self._roles if before.get(name) != after.get(name)
            }
            if changed_roles:
                self._items[row] = after
                self._deferred_role_changes.setdefault(row, set()).update(changed_roles)
        if self._deferred_role_changes and not self._deferred_change_scheduled:
            self._deferred_change_scheduled = True
            QTimer.singleShot(0, self, self._publish_deferred_changes)
        if self._deferred_role_changes:
            self._revision += 1

    def update_items_by_key(
        self,
        items: list[dict[str, Any]],
        *,
        deferred: bool = False,
    ) -> bool:
        """Update existing rows without scanning or rebuilding the complete model.

        Returns ``False`` when a key is not already present so callers can fall
        back to a structural refresh. This path is intended for ordinary clip
        property edits where row membership and ordering do not change.
        """

        self._validate_items(items)
        key_role = self._roles[0]
        role_by_name = {name: number for number, name in self._role_numbers.items()}
        keyed_rows: list[tuple[int, dict[str, Any]]] = []
        for item in items:
            key = item.get(key_role)
            row = self._key_rows.get(str(key), -1)
            if key is None or row < 0:
                return False
            keyed_rows.append((row, item))

        updated = False
        for row, after in keyed_rows:
            before = self._items[row]
            changed_roles = {
                role_by_name[name] for name in self._roles if before.get(name) != after.get(name)
            }
            if not changed_roles:
                continue
            updated = True
            self._items[row] = after
            if deferred:
                self._deferred_role_changes.setdefault(row, set()).update(changed_roles)
            else:
                index = self.index(row, 0)
                self.dataChanged.emit(index, index, sorted(changed_roles))
        if deferred and self._deferred_role_changes and not self._deferred_change_scheduled:
            self._deferred_change_scheduled = True
            QTimer.singleShot(0, self, self._publish_deferred_changes)
        if updated:
            self._revision += 1
        return True

    def patch_items_by_key(
        self,
        items: list[dict[str, Any]],
        *,
        removed_keys: set[str],
        ordered_keys: list[str],
    ) -> bool:
        """Apply a small membership delta while retaining unchanged row projections."""

        self._validate_items(items)
        key_role = self._roles[0]
        replacements = {str(item[key_role]): item for item in items}
        if len(replacements) != len(items) or removed_keys & set(replacements):
            return False
        projected = {
            str(item[key_role]): item
            for item in self._items
            if str(item[key_role]) not in removed_keys
        }
        projected.update(replacements)
        if len(ordered_keys) != len(set(ordered_keys)) or set(projected) != set(ordered_keys):
            return False
        self._deferred_role_changes.clear()
        self._set_items(
            [projected[key] for key in ordered_keys],
            changed_keys=set(replacements),
        )
        self._revision += 1
        return True

    def _validate_items(self, items: list[dict[str, Any]]) -> None:
        expected = set(self._roles)
        for row, item in enumerate(items):
            actual = set(item)
            if actual != expected:
                missing = sorted(expected - actual)
                unexpected = sorted(actual - expected)
                raise ValueError(
                    f"Model row {row} does not match its declared roles; "
                    f"missing={missing}, unexpected={unexpected}"
                )

    def _publish_deferred_changes(self) -> None:
        changes = self._deferred_role_changes
        self._deferred_role_changes = {}
        self._deferred_change_scheduled = False
        for row, roles in sorted(changes.items()):
            if row >= len(self._items):
                continue
            index = self.index(row, 0)
            self.dataChanged.emit(index, index, sorted(roles))

    def _set_items(
        self,
        items: list[dict[str, Any]],
        *,
        changed_keys: set[str] | None = None,
    ) -> None:
        key_role = self._roles[0]
        before_keys = [item.get(key_role) for item in self._items]
        after_keys = [item.get(key_role) for item in items]
        if (
            any(key is None for key in after_keys)
            or len(set(after_keys)) != len(after_keys)
            or len(set(before_keys)) != len(before_keys)
        ):
            self._reset_items(items)
            return
        if not self._items:
            if items:
                self.beginInsertRows(QModelIndex(), 0, len(items) - 1)
                self._items = list(items)
                self._rebuild_key_rows()
                self.endInsertRows()
            return
        if not set(before_keys).intersection(after_keys):
            self._reset_items(items)
            return

        self._key_rows = {}
        wanted = set(after_keys)
        for row in range(len(self._items) - 1, -1, -1):
            if self._items[row].get(key_role) not in wanted:
                self.beginRemoveRows(QModelIndex(), row, row)
                self._items.pop(row)
                self.endRemoveRows()

        for destination, key in enumerate(after_keys):
            if destination < len(self._items) and self._items[destination].get(key_role) == key:
                continue
            source = next(
                (
                    row
                    for row in range(destination + 1, len(self._items))
                    if self._items[row].get(key_role) == key
                ),
                None,
            )
            if source is None:
                self.beginInsertRows(QModelIndex(), destination, destination)
                self._items.insert(destination, items[destination])
                self.endInsertRows()
            else:
                self.beginMoveRows(
                    QModelIndex(),
                    source,
                    source,
                    QModelIndex(),
                    destination,
                )
                self._items.insert(destination, self._items.pop(source))
                self.endMoveRows()

        self._rebuild_key_rows()
        role_by_name = {name: number for number, name in self._role_numbers.items()}
        for row, after in enumerate(items):
            if (
                changed_keys is not None
                and str(after.get(key_role)) not in changed_keys
            ):
                continue
            before = self._items[row]
            changed_roles = [
                role_by_name[name] for name in self._roles if before.get(name) != after.get(name)
            ]
            if changed_roles:
                self._items[row] = after
                index = self.index(row, 0)
                self.dataChanged.emit(index, index, changed_roles)

    def _reset_items(self, items: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self._rebuild_key_rows()
        self.endResetModel()

    def _rebuild_key_rows(self) -> None:
        key_role = self._roles[0]
        self._key_rows = {str(item[key_role]): row for row, item in enumerate(self._items)}

    @Slot(int, result="QVariantMap")
    def get(self, row: int) -> dict[str, Any]:
        return dict(self._items[row]) if 0 <= row < len(self._items) else {}

    @Slot(result="QVariantList")
    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._items]

    @Slot(str, str, result=int)
    def findRow(self, role: str, value: str) -> int:
        if role == self._roles[0] and self._key_rows:
            return self._key_rows.get(str(value), -1)
        for index, item in enumerate(self._items):
            if str(item.get(role)) == value:
                return index
        return -1
