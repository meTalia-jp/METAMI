"""中央METAMIデータのバックアップ・復元・情報取得。"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


REQUIRED_METAMI_TABLES = frozenset(
    {"files", "user_info", "tags", "file_tags"}
)


class DataManagementError(RuntimeError):
    """安全に完了できないデータ管理処理をGUIへ通知する。"""


@dataclass(frozen=True)
class DatabaseInfo:
    """情報ダイアログへ渡す読み取り専用のDB情報。"""

    file_name: str
    directory: str
    size_bytes: int
    modified_at: datetime
    registered_files: int
    titled_files: int
    tagged_files: int
    memo_files: int
    rated_files: int
    sqlite_version: str
    integrity_ok: bool
    integrity_details: str


@dataclass(frozen=True)
class RestoreResult:
    """復元元と復元前退避の確定パス。"""

    source_path: Path
    before_restore_path: Path


def timestamped_database_name(
    prefix: str = "metami_backup",
    now: datetime | None = None,
) -> str:
    """ローカル日時を含むバックアップ候補名を返す。"""
    moment = now or datetime.now()
    return f"{prefix}_{moment:%Y%m%d_%H%M%S}.db"


def with_database_suffix(path: Path) -> Path:
    """拡張子を省略した保存先だけへ.dbを補完する。"""
    return path if path.suffix else path.with_suffix(".db")


def validate_metami_database(path: Path) -> str:
    """読み取り専用でSQLite整合性とMETAMI主要テーブルを確認する。"""
    candidate = Path(path)
    if not candidate.exists():
        raise DataManagementError("選択したファイルが見つかりません。")
    if not candidate.is_file():
        raise DataManagementError("選択した項目は通常のファイルではありません。")
    try:
        if candidate.stat().st_size <= 0:
            raise DataManagementError("選択したファイルは空です。")
    except OSError as error:
        raise DataManagementError(
            f"選択したファイルの情報を確認できません: {error}"
        ) from error
    if not os.access(candidate, os.R_OK):
        raise DataManagementError("選択したファイルを読み取れません。")

    try:
        with _read_only_connection(candidate) as connection:
            integrity = _quick_check(connection)
            if integrity.casefold() != "ok":
                raise DataManagementError(
                    "METAMIデータの整合性確認に失敗しました。\n"
                    f"詳細: {integrity}"
                )
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
    except DataManagementError:
        raise
    except sqlite3.Error as error:
        raise DataManagementError(
            f"SQLiteデータベースとして開けません: {error}"
        ) from error

    missing = REQUIRED_METAMI_TABLES - tables
    if missing:
        raise DataManagementError(
            "選択したファイルは、METAMIデータとして確認できませんでした。"
        )
    return integrity


def validate_restore_source(source_path: Path, current_path: Path) -> str:
    """復元元の内容と、使用中DBとは別実体であることを確認する。"""
    integrity = validate_metami_database(source_path)
    _ensure_distinct_paths(Path(source_path), Path(current_path))
    return integrity


def backup_database(source_path: Path, destination_path: Path) -> Path:
    """使用中DBをSQLiteバックアップAPIで原子的に保存する。"""
    source = Path(source_path)
    destination = with_database_suffix(Path(destination_path))
    validate_metami_database(source)
    _ensure_distinct_paths(source, destination)
    _ensure_writable_destination(destination)
    return _atomic_sqlite_backup(source, destination)


def restore_database(
    source_path: Path,
    current_path: Path,
    *,
    backup_directory: Path | None = None,
    now: datetime | None = None,
) -> RestoreResult:
    """検証済みDBを復元し、失敗時は復元前退避から自動回復する。"""
    source = Path(source_path)
    current = Path(current_path)
    validate_restore_source(source, current)
    validate_metami_database(current)

    backups = (
        Path(backup_directory)
        if backup_directory is not None
        else current.parent / "backups"
    )
    try:
        backups.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise DataManagementError(
            f"復元前データの保存場所を作成できません: {error}"
        ) from error
    before_restore = _available_backup_path(
        backups,
        timestamped_database_name("metami_before_restore", now),
    )
    try:
        backup_database(current, before_restore)
    except DataManagementError as error:
        raise DataManagementError(
            f"復元前のMETAMIデータを退避できませんでした: {error}"
        ) from error

    try:
        _backup_into_database(source, current)
        validate_metami_database(current)
    except Exception as restore_error:
        try:
            _backup_into_database(before_restore, current)
            validate_metami_database(current)
        except Exception as recovery_error:
            raise DataManagementError(
                "復元と自動復旧の両方に失敗しました。\n"
                f"復元前データ: {before_restore}\n"
                f"復元エラー: {restore_error}\n"
                f"自動復旧エラー: {recovery_error}"
            ) from recovery_error
        raise DataManagementError(
            "復元には失敗しましたが、復元前のMETAMIデータへ戻しました。\n"
            f"詳細: {restore_error}"
        ) from restore_error

    return RestoreResult(source.resolve(), before_restore.resolve())


def get_database_info(path: Path) -> DatabaseInfo:
    """DBを変更せず、利用者向け件数と整合性情報を集計する。"""
    database_path = Path(path)
    integrity = validate_metami_database(database_path)
    try:
        stat = database_path.stat()
        with _read_only_connection(database_path) as connection:
            registered_files = _count(connection, "SELECT COUNT(*) FROM files")
            titled_files = _count(
                connection,
                "SELECT COUNT(*) FROM user_info WHERE TRIM(title) <> ''",
            )
            tagged_files = _count(
                connection,
                "SELECT COUNT(DISTINCT file_id) FROM file_tags",
            )
            memo_files = _count(
                connection,
                "SELECT COUNT(*) FROM user_info WHERE TRIM(memo) <> ''",
            )
            rated_files = _count(
                connection,
                "SELECT COUNT(*) FROM user_info WHERE rating > 0",
            )
            sqlite_version = str(
                connection.execute("SELECT sqlite_version()").fetchone()[0]
            )
    except (OSError, sqlite3.Error) as error:
        raise DataManagementError(
            f"データベース情報を取得できません: {error}"
        ) from error
    return DatabaseInfo(
        file_name=database_path.name,
        directory=str(database_path.resolve().parent),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime),
        registered_files=registered_files,
        titled_files=titled_files,
        tagged_files=tagged_files,
        memo_files=memo_files,
        rated_files=rated_files,
        sqlite_version=sqlite_version,
        integrity_ok=integrity.casefold() == "ok",
        integrity_details=integrity,
    )


def _atomic_sqlite_backup(source: Path, destination: Path) -> Path:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.stem}_",
            suffix=".tmp.db",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        _backup_into_database(source, temporary_path)
        validate_metami_database(temporary_path)
        os.replace(temporary_path, destination)
        temporary_path = None
        return destination.resolve()
    except DataManagementError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise DataManagementError(
            f"バックアップファイルを作成できません: {error}"
        ) from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _backup_into_database(source: Path, destination: Path) -> None:
    try:
        with _read_only_connection(source) as source_connection:
            with closing(
                sqlite3.connect(destination, timeout=10)
            ) as destination_connection:
                source_connection.backup(destination_connection)
                destination_connection.commit()
                integrity = _quick_check(destination_connection)
                if integrity.casefold() != "ok":
                    raise DataManagementError(
                        "コピー後のMETAMIデータの整合性確認に失敗しました。\n"
                        f"詳細: {integrity}"
                    )
    except DataManagementError:
        raise
    except sqlite3.Error as error:
        raise DataManagementError(
            f"SQLiteバックアップ処理に失敗しました: {error}"
        ) from error


def _read_only_connection(path: Path):
    uri = path.resolve().as_uri() + "?mode=ro"
    return closing(sqlite3.connect(uri, uri=True, timeout=10))


def _quick_check(connection: sqlite3.Connection) -> str:
    rows = connection.execute("PRAGMA quick_check").fetchall()
    return "\n".join(str(row[0]) for row in rows) if rows else "結果なし"


def _count(connection: sqlite3.Connection, statement: str) -> int:
    row = connection.execute(statement).fetchone()
    return int(row[0]) if row is not None else 0


def _ensure_distinct_paths(first: Path, second: Path) -> None:
    first_resolved = first.resolve()
    second_resolved = second.resolve()
    if first_resolved == second_resolved:
        raise DataManagementError(
            "現在使用中のMETAMIデータと同じ場所は選択できません。"
        )
    if second.exists():
        try:
            if os.path.samefile(first, second):
                raise DataManagementError(
                    "現在使用中のMETAMIデータと同じ実体は選択できません。"
                )
        except OSError:
            pass


def _ensure_writable_destination(destination: Path) -> None:
    parent = destination.parent
    if not parent.exists() or not parent.is_dir():
        raise DataManagementError("保存先フォルダが見つかりません。")
    if not os.access(parent, os.W_OK):
        raise DataManagementError("保存先フォルダへ書き込めません。")
    if destination.exists() and not os.access(destination, os.W_OK):
        raise DataManagementError("選択した保存先へ書き込めません。")


def _available_backup_path(directory: Path, file_name: str) -> Path:
    candidate = directory / file_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    for index in range(1, 1000):
        numbered = candidate.with_name(f"{stem}_{index:02d}.db")
        if not numbered.exists():
            return numbered
    raise DataManagementError("復元前退避ファイル名を確保できません。")
