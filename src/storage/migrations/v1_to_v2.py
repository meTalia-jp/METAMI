"""ファイルハッシュ保存列を追加するスキーマ1→2更新。"""

from __future__ import annotations

import sqlite3

from storage.file_hash import HashStatus


HASH_COLUMNS: dict[str, str] = {
    "content_hash": "TEXT",
    "hash_algorithm": "TEXT",
    "hash_status": (
        "TEXT NOT NULL DEFAULT 'not_calculated' "
        "CHECK (hash_status IN "
        "('not_calculated', 'calculated', 'stale', 'failed', 'missing'))"
    ),
    "hash_calculated_at": "TEXT",
    "hashed_file_size": "INTEGER",
    "hashed_modified_ns": "INTEGER",
    "hash_error": "TEXT",
}


def ensure_hash_columns(connection: sqlite3.Connection) -> None:
    """新規DBと移行DBへ同じハッシュ列定義を適用する。"""
    existing = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(files)")
    }
    for name, definition in HASH_COLUMNS.items():
        if name not in existing:
            connection.execute(f'ALTER TABLE files ADD COLUMN "{name}" {definition}')
    connection.execute(
        "UPDATE files SET hash_status = ? WHERE hash_status IS NULL OR hash_status = ''",
        (HashStatus.NOT_CALCULATED.value,),
    )


def migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """列を追加し、既存行を未計算として、user_versionを2へ進める。"""
    ensure_hash_columns(connection)
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(files)")
    }
    if not set(HASH_COLUMNS).issubset(columns):
        raise sqlite3.DatabaseError("ハッシュ関連列を確認できません。")
    connection.execute("PRAGMA user_version = 2")
