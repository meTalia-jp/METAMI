"""SQLite標準user_versionによるMETAMI DB互換性判定。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


# 中央METAMI DB全体の互換性判定用。PRAGMA user_versionと対応する。
METAMI_SCHEMA_VERSION = 1

SCHEMA_COMPATIBLE = "正常"
SCHEMA_UPDATE_REQUIRED = "更新が必要"
SCHEMA_TOO_NEW = "このバージョンでは開けません"


class SchemaVersionError(RuntimeError):
    """DBスキーマを安全に判定できない場合の例外。"""


@dataclass(frozen=True)
class SchemaCompatibility:
    """DBとアプリが対応するスキーマの比較結果。"""

    database_version: int
    supported_version: int
    status: str


def get_user_version(path: Path) -> int:
    """DBを書き換えずPRAGMA user_versionを取得する。"""
    database_path = Path(path)
    try:
        uri = database_path.resolve().as_uri() + "?mode=ro"
        with closing(
            sqlite3.connect(uri, uri=True, timeout=10)
        ) as connection:
            row = connection.execute("PRAGMA user_version").fetchone()
    except sqlite3.Error as error:
        raise SchemaVersionError(
            f"DBスキーマバージョンを取得できません: {error}"
        ) from error
    if row is None or isinstance(row[0], bool):
        raise SchemaVersionError(
            "DBスキーマバージョンを整数として取得できません。"
        )
    try:
        version = int(row[0])
    except (TypeError, ValueError) as error:
        raise SchemaVersionError(
            "DBスキーマバージョンを整数として取得できません。"
        ) from error
    if version < 0:
        raise SchemaVersionError(
            f"不正なDBスキーマバージョンです: {version}"
        )
    return version


def check_schema_compatibility(
    database_version: int,
    supported_version: int = METAMI_SCHEMA_VERSION,
) -> SchemaCompatibility:
    """スキーマを正常・更新必要・新しすぎる状態へ分類する。"""
    if (
        isinstance(database_version, bool)
        or isinstance(supported_version, bool)
        or not isinstance(database_version, int)
        or not isinstance(supported_version, int)
        or database_version < 0
        or supported_version < 0
    ):
        raise SchemaVersionError("DBスキーマバージョンが不正です。")
    if database_version == supported_version:
        status = SCHEMA_COMPATIBLE
    elif database_version < supported_version:
        status = SCHEMA_UPDATE_REQUIRED
    else:
        status = SCHEMA_TOO_NEW
    return SchemaCompatibility(database_version, supported_version, status)
