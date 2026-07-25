"""表示用メタデータから、ファイル検索に必要な索引を作る。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from metadata.display_service import DisplayDataError, build_display_data


@dataclass(frozen=True)
class SearchRecord:
    path: Path
    filename: str
    format_name: str
    user_title: str = ""
    tags: str = ""
    memo: str = ""
    prompt: str = ""
    model: str = ""
    seed: str = ""

    def matches(self, query: str, target: str, format_name: str) -> bool:
        if format_name != "all":
            if format_name == "jpeg":
                if self.format_name not in {"jpg", "jpeg"}:
                    return False
            elif self.format_name != format_name:
                return False
        normalized = query.casefold().strip()
        if not normalized:
            return True
        values = {
            "filename": self.filename,
            "title": self.user_title,
            "tags": self.tags,
            "memo": self.memo,
            "prompt": self.prompt,
            "model": self.model,
            "seed": self.seed,
        }
        if target == "all":
            haystack = "\n".join(values.values())
        else:
            haystack = values.get(target, "")
        return normalized in haystack.casefold()


def build_search_record(
    path: Path,
    user_title: str = "",
    tags: list[str] | None = None,
    memo: str = "",
) -> SearchRecord:
    """読取不能・メタデータなしでも安全な検索索引を返す。"""
    values: dict[str, str] = {}
    try:
        data = build_display_data(path)
    except DisplayDataError:
        data = None
    if data is not None:
        values = dict(data.generation_info)
    prompts = [
        values.get("jp_prompt", ""),
        values.get("Prompt", ""),
        values.get("Negative Prompt", ""),
    ]
    return SearchRecord(
        path=path,
        filename=path.name,
        format_name=path.suffix.removeprefix(".").casefold(),
        user_title=user_title,
        tags="\n".join(tags or []),
        memo=memo,
        prompt="\n".join(value for value in prompts if value != "情報なし"),
        model=_without_placeholder(values.get("Model", "")),
        seed=_without_placeholder(values.get("Seed", "")),
    )


def _without_placeholder(value: str) -> str:
    return "" if value == "情報なし" else value
