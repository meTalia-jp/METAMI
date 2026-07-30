"""段階的DBマイグレーション、事前退避、自動回復の管理。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from storage import data_management
from storage.schema_version import (
    METAMI_SCHEMA_VERSION,
    SchemaVersionError,
    get_user_version,
)
from storage.migrations.v0_to_v1 import migrate_v0_to_v1


class MigrationError(RuntimeError):
    """DB更新を安全に完了できなかった場合の例外。"""

    def __init__(
        self,
        message: str,
        *,
        from_version: int,
        to_version: int,
        backup_path: Path | None = None,
        migration_error: Exception | None = None,
        recovery_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.from_version = from_version
        self.to_version = to_version
        self.backup_path = backup_path
        self.migration_error = migration_error
        self.recovery_error = recovery_error


@dataclass(frozen=True)
class MigrationResult:
    """完了した段階的更新と退避先。"""

    success: bool
    from_version: int
    to_version: int
    backup_path: Path | None
    error_details: str = ""


MigrationStep = Callable[[sqlite3.Connection], None]
MIGRATIONS: dict[int, MigrationStep] = {0: migrate_v0_to_v1}


def migrate_database(
    path: Path,
    *,
    target_version: int = METAMI_SCHEMA_VERSION,
    backup_directory: Path | None = None,
    now: datetime | None = None,
) -> MigrationResult:
    """登録済みの処理を1段階ずつ実行し、失敗時は退避から戻す。"""
    database_path = Path(path)
    data_management.validate_metami_database(database_path)
    try:
        original_version = get_user_version(database_path)
    except SchemaVersionError as error:
        raise MigrationError(
            str(error),
            from_version=-1,
            to_version=target_version,
            migration_error=error,
        ) from error
    if original_version > target_version:
        raise MigrationError(
            "このMETAMIでは新しいDBスキーマを更新できません。",
            from_version=original_version,
            to_version=target_version,
        )
    if original_version == target_version:
        return MigrationResult(
            True, original_version, target_version, None
        )

    missing_steps = [
        version
        for version in range(original_version, target_version)
        if version not in MIGRATIONS
    ]
    if missing_steps:
        raise MigrationError(
            f"未登録のDB更新処理があります: {missing_steps[0]}→"
            f"{missing_steps[0] + 1}",
            from_version=original_version,
            to_version=target_version,
        )

    backups = (
        Path(backup_directory)
        if backup_directory is not None
        else database_path.parent / "backups"
    )
    try:
        backups.mkdir(parents=True, exist_ok=True)
        name = data_management.timestamped_database_name(
            f"metami_before_migration_v{original_version}_to_v{target_version}",
            now,
        )
        backup_path = data_management.available_backup_path(backups, name)
        data_management.backup_database(database_path, backup_path)
    except (OSError, data_management.DataManagementError) as error:
        raise MigrationError(
            f"更新前のMETAMIデータをバックアップできません: {error}",
            from_version=original_version,
            to_version=target_version,
            migration_error=error,
        ) from error

    current_version = original_version
    try:
        with closing(
            sqlite3.connect(database_path, timeout=10)
        ) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                while current_version < target_version:
                    MIGRATIONS[current_version](connection)
                    next_version = current_version + 1
                    row = connection.execute("PRAGMA user_version").fetchone()
                    if row is None or int(row[0]) != next_version:
                        raise sqlite3.DatabaseError(
                            f"DBスキーマ{next_version}を確認できません。"
                        )
                    quick_check = data_management.quick_check(connection)
                    if quick_check.casefold() != "ok":
                        raise sqlite3.DatabaseError(
                            "更新後の整合性確認に失敗しました: "
                            f"{quick_check}"
                        )
                    current_version = next_version
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        data_management.validate_metami_database(database_path)
        final_version = get_user_version(database_path)
        if final_version != target_version:
            raise sqlite3.DatabaseError(
                f"更新後のDBスキーマが不正です: {final_version}"
            )
    except Exception as migration_error:
        try:
            data_management.copy_database_over(backup_path, database_path)
            data_management.validate_metami_database(database_path)
        except Exception as recovery_error:
            raise MigrationError(
                "METAMIデータの更新と自動回復の両方に失敗しました。",
                from_version=original_version,
                to_version=target_version,
                backup_path=backup_path.resolve(),
                migration_error=migration_error,
                recovery_error=recovery_error,
            ) from recovery_error
        raise MigrationError(
            "METAMIデータの更新に失敗しましたが、更新前のデータへ戻しました。",
            from_version=original_version,
            to_version=target_version,
            backup_path=backup_path.resolve(),
            migration_error=migration_error,
        ) from migration_error

    return MigrationResult(
        True,
        original_version,
        target_version,
        backup_path.resolve(),
    )
