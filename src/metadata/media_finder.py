"""フォルダ内の対応メディアを安全に列挙する共通処理。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SUPPORTED_SUFFIXES = frozenset({".png", ".webp", ".jpg", ".jpeg", ".mp4"})


class MediaSearchError(Exception):
    """探索を開始できない場合のエラー。"""


@dataclass(frozen=True)
class MediaSearchResult:
    paths: tuple[Path, ...]
    unreadable_directories: tuple[str, ...] = ()


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path))).casefold()


def find_media_files(
    folder_path: str | Path,
    include_subfolders: bool = False,
    supported_suffixes: Iterable[str] = SUPPORTED_SUFFIXES,
) -> MediaSearchResult:
    """対象拡張子のファイルを重複なく安定した順序で返す。

    再帰時はシンボリックリンクとジャンクションを辿らず、読み取れない
    サブフォルダがあっても残りの探索を継続する。
    """
    root = Path(folder_path)
    try:
        if not root.is_dir():
            raise MediaSearchError("指定されたフォルダを読み取れません。")
    except OSError as error:
        raise MediaSearchError("指定されたフォルダを読み取れません。") from error

    suffixes = {suffix.casefold() for suffix in supported_suffixes}
    found: dict[str, Path] = {}
    unreadable: list[str] = []
    pending = [root]
    visited: set[str] = set()

    while pending:
        directory = pending.pop()
        directory_key = _path_key(directory)
        if directory_key in visited:
            continue
        visited.add(directory_key)
        try:
            with os.scandir(directory) as iterator:
                entries = list(iterator)
        except OSError:
            unreadable.append(str(directory))
            continue
        entries.sort(key=lambda entry: entry.name.casefold(), reverse=True)
        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=False):
                    path = Path(entry.path)
                    if path.suffix.casefold() in suffixes:
                        found.setdefault(_path_key(path), path)
                    continue
                if not include_subfolders:
                    continue
                is_junction = getattr(entry, "is_junction", lambda: False)
                if (
                    entry.is_symlink()
                    or is_junction()
                    or not entry.is_dir(follow_symlinks=False)
                ):
                    continue
                pending.append(Path(entry.path))
            except OSError:
                unreadable.append(entry.path)

    def sort_key(path: Path) -> tuple[str, ...]:
        try:
            relative = path.relative_to(root)
        except ValueError:
            relative = path
        return tuple(part.casefold() for part in relative.parts)

    return MediaSearchResult(
        tuple(sorted(found.values(), key=sort_key)),
        tuple(dict.fromkeys(unreadable)),
    )
