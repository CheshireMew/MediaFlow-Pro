from __future__ import annotations


def parse_download_entry_selection(spec: str, available: set[int]) -> list[int]:
    selected: set[int] = set()
    for raw_token in spec.split(","):
        token = raw_token.strip()
        if not token:
            continue
        if "-" in token:
            parts = token.split("-", maxsplit=1)
            if len(parts) != 2 or not all(part.strip().isdigit() for part in parts):
                raise ValueError(f"下载项目范围无效：{token}")
            start, end = (int(part.strip()) for part in parts)
            if start > end:
                raise ValueError(f"下载项目范围无效：{token}")
            selected.update(index for index in available if start <= index <= end)
        elif token.isdigit():
            selected.add(int(token))
        else:
            raise ValueError(f"下载项目编号无效：{token}")
    unavailable = selected - available
    if unavailable:
        values = ", ".join(str(index) for index in sorted(unavailable))
        raise ValueError(f"不存在或不可下载的项目：{values}")
    return sorted(selected)
