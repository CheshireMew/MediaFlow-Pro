from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from pathlib import Path


class LocalStructuredFileReader:
    """Read user-selected JSON and CSV files at the local filesystem boundary."""

    def resolve_file(self, source: str | Path) -> Path:
        path = Path(source).expanduser().resolve(strict=True)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def read_json(self, source: Path) -> object:
        return json.loads(source.read_text(encoding="utf-8-sig"))

    def read_csv(self, source: Path) -> list[Mapping[str, object]]:
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            return [dict(record) for record in csv.DictReader(stream)]
