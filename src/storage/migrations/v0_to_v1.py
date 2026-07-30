"""現行METAMI DBへスキーマ1を登録するマイグレーション。"""

from __future__ import annotations

import sqlite3


def migrate_v0_to_v1(connection: sqlite3.Connection) -> None:
    """構造やデータを変えず、user_versionだけを1へ更新する。"""
    connection.execute("PRAGMA user_version = 1")
