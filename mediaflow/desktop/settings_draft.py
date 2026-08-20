from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from mediaflow.application.settings_form import SettingsForm

_FORM_ALIASES = frozenset(
    field.alias or name for name, field in SettingsForm.model_fields.items()
)


class SettingsDraft(QObject):
    """Typed, merge-aware settings draft with backend-owned persistence timing."""

    changed = Signal()

    def __init__(
        self,
        parent: QObject,
        *,
        read_current: Callable[[], SettingsForm],
        commit: Callable[[SettingsForm], None],
        report_error: Callable[[str], None],
        save_delay_ms: int = 450,
    ) -> None:
        super().__init__(parent)
        self._read_current = read_current
        self._commit = commit
        self._report_error = report_error
        self._active = False
        self._form = read_current()
        self._baseline = self._form
        self._dirty_fields: set[str] = set()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(save_delay_ms)
        self._save_timer.timeout.connect(self.flush)

    @Property(dict, notify=changed)
    def data(self) -> dict[str, object]:
        return self._form.model_dump(mode="json", by_alias=True)

    @Property(bool, notify=changed)
    def dirty(self) -> bool:
        return bool(self._dirty_fields)

    @Slot()
    def begin(self) -> None:
        self._save_timer.stop()
        self._active = True
        self._form = self._read_current()
        self._baseline = self._form
        self._dirty_fields.clear()
        self.changed.emit()

    @Slot(str, "QVariant")
    def update(self, field_alias: str, value: object) -> None:
        if field_alias not in _FORM_ALIASES:
            self._report_error(f"未知设置字段：{field_alias}")
            return
        if not self._active:
            self.begin()
        candidate_data = self._form.model_dump(mode="python", by_alias=True)
        candidate_data[field_alias] = value
        try:
            candidate = SettingsForm.model_validate(candidate_data)
        except (TypeError, ValueError) as error:
            self._report_error(str(error))
            self.changed.emit()
            return
        self._form = candidate
        baseline_data = self._baseline.model_dump(mode="python", by_alias=True)
        if candidate.model_dump(mode="python", by_alias=True)[field_alias] == baseline_data[field_alias]:
            self._dirty_fields.discard(field_alias)
        else:
            self._dirty_fields.add(field_alias)
        self.changed.emit()
        if self._dirty_fields:
            self._save_timer.start()
        else:
            self._save_timer.stop()

    @Slot()
    def refresh(self) -> None:
        incoming = self._read_current()
        if self._active and self._dirty_fields:
            merged = incoming.model_dump(mode="python", by_alias=True)
            draft = self._form.model_dump(mode="python", by_alias=True)
            for field_alias in self._dirty_fields:
                merged[field_alias] = draft[field_alias]
            self._form = SettingsForm.model_validate(merged)
        else:
            self._form = incoming
            self._dirty_fields.clear()
        self._baseline = incoming
        self.changed.emit()

    @Slot()
    def flush(self) -> None:
        self._save_timer.stop()
        if not self._dirty_fields:
            return
        pending_fields = set(self._dirty_fields)
        self._dirty_fields.clear()
        try:
            self._commit(self._form)
        except Exception as error:
            self._dirty_fields.update(pending_fields)
            self._report_error(str(error))
            self.changed.emit()

    @Slot()
    def finish(self) -> None:
        self.flush()
        self._active = False
