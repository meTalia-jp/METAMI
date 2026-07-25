"""GUIへ渡す、形式に依存しない表示データ。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DisplayData:
    """右ペインの各タブへ表示する情報。"""

    basic_info: tuple[tuple[str, str], ...]
    generation_info: tuple[tuple[str, str], ...]
    workflow_text: str
    json_text: str
    metadata_count: int
    status: str
