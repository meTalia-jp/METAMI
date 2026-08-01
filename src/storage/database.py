"""SQLiteによるMETAMIユーザーデータの保存基盤。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from storage.file_hash import (
    CURRENT_HASH_ALGORITHM,
    FileHashResult,
    HashStatus,
    is_valid_hash,
)
from storage.migrations.v1_to_v2 import ensure_hash_columns
from storage.schema_version import METAMI_SCHEMA_VERSION


# 既存の内部テーブル初期化・移行処理用。中央DB全体の互換性番号とは別。
SCHEMA_VERSION = 3
DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "metami.db"


class DatabaseError(RuntimeError):
    """DB処理をGUI層へ安全に通知する例外。"""


@dataclass(frozen=True)
class MissingFileDetails:
    """見つからないファイルについて、整理画面へ渡す保存済み情報。"""

    path: str
    filename: str
    last_checked_at: str
    rating: int
    tags: tuple[str, ...]
    has_memo: bool


@dataclass(frozen=True)
class RegisteredFileRecord:
    """存在確認画面へ渡す、DBから読み取っただけの登録情報。"""

    file_id: int
    path: str
    filename: str
    format_name: str
    size_bytes: int
    created_at: str
    rating: int
    tags: tuple[str, ...]
    memo: str


@dataclass(frozen=True)
class StoredFileHash:
    """DBへ保存されたファイル識別情報。"""

    path: str
    content_hash: str | None
    algorithm: str | None
    status: HashStatus
    calculated_at: str | None
    file_size: int | None
    modified_ns: int | None
    error: str | None


class MetadataDatabase:
    """単一SQLiteファイルの初期化とファイル識別情報を管理する。"""

    def __init__(self, path: Path = DEFAULT_DATABASE_PATH) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        """DBを作成し、必要なマイグレーションを順番に適用する。"""
        database_existed = self.path.exists()
        if database_existed:
            try:
                if not self.path.is_file() or self.path.stat().st_size <= 0:
                    raise DatabaseError(
                        "既存のデータベースが空か、通常のファイルではありません。"
                    )
            except OSError as error:
                raise DatabaseError(
                    f"既存のデータベースを確認できません: {error}"
                ) from error
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                current = self._schema_version(connection)
                if current > SCHEMA_VERSION:
                    raise DatabaseError(
                        f"未対応のDBバージョンです: {current}"
                    )
                migrations: dict[int, Callable[[sqlite3.Connection], None]] = {
                    1: self._migrate_to_v1,
                    2: self._migrate_to_v2,
                    3: self._migrate_to_v3,
                }
                for version in range(current + 1, SCHEMA_VERSION + 1):
                    if not connection.in_transaction:
                        connection.execute("BEGIN IMMEDIATE")
                    migrations[version](connection)
                    connection.execute(
                        """
                        INSERT INTO schema_info (id, version, updated_at)
                        VALUES (1, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(id) DO UPDATE SET
                            version = excluded.version,
                            updated_at = excluded.updated_at
                        """,
                        (version,),
                    )
                # 同じVersion番号で先行作成されたDBも、確定構造へ
                # 安全かつ冪等に補完する。
                if current == SCHEMA_VERSION:
                    if not connection.in_transaction:
                        connection.execute("BEGIN IMMEDIATE")
                    self._migrate_to_v2(connection)
                    self._migrate_to_v3(connection)
                if not database_existed:
                    ensure_hash_columns(connection)
                    required = {"files", "user_info", "tags", "file_tags"}
                    tables = {
                        str(row[0])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        )
                    }
                    missing = required - tables
                    if missing:
                        raise sqlite3.DatabaseError(
                            "新規DBの主要テーブルを確認できません。"
                        )
                    quick_rows = connection.execute(
                        "PRAGMA quick_check"
                    ).fetchall()
                    quick_result = "\n".join(
                        str(row[0]) for row in quick_rows
                    )
                    if quick_result.casefold() != "ok":
                        raise sqlite3.DatabaseError(
                            f"新規DBの整合性確認に失敗しました: {quick_result}"
                        )
                    connection.execute(
                        f"PRAGMA user_version = {METAMI_SCHEMA_VERSION}"
                    )
                    row = connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()
                    if row is None or int(row[0]) != METAMI_SCHEMA_VERSION:
                        raise sqlite3.DatabaseError(
                            "新規DBのスキーマ情報を登録できません。"
                        )
        except DatabaseError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise DatabaseError(f"データベースを初期化できません: {error}") from error

    def register_files(self, paths: Iterable[Path]) -> None:
        """原本へ書き込まず、現在のファイル識別情報だけを保存する。"""
        records: list[tuple[str, str, str, int, int]] = []
        for path in paths:
            try:
                stat = path.stat()
                records.append(
                    (
                        str(path.resolve()),
                        path.name,
                        path.suffix.removeprefix(".").casefold(),
                        stat.st_size,
                        stat.st_mtime_ns,
                    )
                )
            except OSError:
                continue
        if not records:
            return
        try:
            with self._connect() as connection:
                connection.executemany(
                    """
                    INSERT INTO files (
                        canonical_path, filename, format_name,
                        size_bytes, modified_ns, missing, last_checked_at
                    ) VALUES (?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
                    ON CONFLICT(canonical_path) DO UPDATE SET
                        filename = excluded.filename,
                        format_name = excluded.format_name,
                        size_bytes = excluded.size_bytes,
                        modified_ns = excluded.modified_ns,
                        missing = 0,
                        last_checked_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    records,
                )
                connection.executemany(
                    """
                    INSERT INTO file_paths (
                        file_id, path, first_seen_at, last_seen_at
                    )
                    SELECT id, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM files WHERE canonical_path = ?
                    ON CONFLICT(file_id, path) DO UPDATE SET
                        last_seen_at = CURRENT_TIMESTAMP
                    """,
                    ((path, path) for path, *_rest in records),
                )
        except sqlite3.Error as error:
            raise DatabaseError(f"ファイル情報を保存できません: {error}") from error

    def check_registered_files(self) -> dict[str, bool]:
        """登録済みパスを確認し、missing状態と最終確認日時を更新する。"""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id, canonical_path FROM files"
                ).fetchall()
                states = [
                    (0 if Path(str(path)).is_file() else 1, int(file_id), str(path))
                    for file_id, path in rows
                ]
                connection.executemany(
                    """
                    UPDATE files
                    SET missing = ?, last_checked_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    ((missing, file_id) for missing, file_id, _path in states),
                )
                connection.executemany(
                    """
                    UPDATE file_paths
                    SET last_seen_at = CURRENT_TIMESTAMP
                    WHERE file_id = ? AND path = ? AND ? = 0
                    """,
                    (
                        (file_id, path, missing)
                        for missing, file_id, path in states
                    ),
                )
            return {path: bool(missing) for missing, _file_id, path in states}
        except sqlite3.Error as error:
            raise DatabaseError(
                f"ファイル状態を確認できません: {error}"
            ) from error

    def save_file_hash_result(self, result: FileHashResult) -> None:
        """単体計算結果を登録済みファイルへトランザクション保存する。"""
        path = str(result.path.resolve())
        try:
            with self._connect() as connection:
                if result.status is HashStatus.CALCULATED:
                    if (
                        not result.success
                        or result.algorithm != CURRENT_HASH_ALGORITHM
                        or not is_valid_hash(result.digest, result.algorithm)
                        or not result.calculated_at
                        or result.file_size is None
                        or result.file_size < 0
                        or result.modified_ns is None
                        or result.modified_ns < 0
                    ):
                        raise DatabaseError("保存するハッシュ計算結果が不正です。")
                    values = (
                        result.digest,
                        result.algorithm,
                        result.status.value,
                        result.calculated_at,
                        result.file_size,
                        result.modified_ns,
                        None,
                        path,
                    )
                    cursor = connection.execute(
                        """
                        UPDATE files SET
                            content_hash = ?, hash_algorithm = ?, hash_status = ?,
                            hash_calculated_at = ?, hashed_file_size = ?,
                            hashed_modified_ns = ?, hash_error = ?
                        WHERE canonical_path = ?
                        """,
                        values,
                    )
                elif result.status in {HashStatus.MISSING, HashStatus.STALE}:
                    cursor = connection.execute(
                        """
                        UPDATE files SET hash_status = ?, hash_error = ?
                        WHERE canonical_path = ?
                        """,
                        (result.status.value, result.error, path),
                    )
                else:
                    cursor = connection.execute(
                        """
                        UPDATE files SET
                            content_hash = NULL,
                            hash_algorithm = ?,
                            hash_status = ?,
                            hash_calculated_at = NULL,
                            hashed_file_size = NULL,
                            hashed_modified_ns = NULL,
                            hash_error = ?
                        WHERE canonical_path = ?
                        """,
                        (
                            result.algorithm,
                            result.status.value,
                            result.error,
                            path,
                        ),
                    )
                if cursor.rowcount != 1:
                    raise DatabaseError("ハッシュ保存対象がDBに登録されていません。")
        except DatabaseError:
            raise
        except sqlite3.Error as error:
            raise DatabaseError(f"ハッシュ情報を保存できません: {error}") from error

    def get_file_hash(self, path: Path) -> StoredFileHash | None:
        """保存済み情報を返し、不正なcalculated値はstaleとして扱う。"""
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT canonical_path, content_hash, hash_algorithm,
                           hash_status, hash_calculated_at, hashed_file_size,
                           hashed_modified_ns, hash_error
                    FROM files WHERE canonical_path = ?
                    """,
                    (str(path.resolve()),),
                ).fetchone()
        except sqlite3.Error as error:
            raise DatabaseError(f"ハッシュ情報を取得できません: {error}") from error
        if row is None:
            return None
        try:
            status = HashStatus(str(row[3]))
        except ValueError:
            status = HashStatus.STALE
        if status is HashStatus.CALCULATED and not is_valid_hash(row[1], str(row[2])):
            status = HashStatus.STALE
        return StoredFileHash(
            str(row[0]), row[1], row[2], status, row[4], row[5], row[6], row[7]
        )

    def refresh_file_hash_status(self, path: Path) -> HashStatus:
        """サイズとmtime_nsだけでmissing・stale・現在状態を判定する。"""
        stored = self.get_file_hash(path)
        if stored is None:
            raise DatabaseError("ハッシュ確認対象がDBに登録されていません。")
        candidate = Path(path)
        if not candidate.is_file():
            status = HashStatus.MISSING
        else:
            try:
                stat = candidate.stat()
            except OSError:
                status = HashStatus.MISSING
            else:
                if stored.status is HashStatus.MISSING:
                    status = (
                        HashStatus.STALE
                        if stored.content_hash is not None
                        else HashStatus.NOT_CALCULATED
                    )
                elif stored.status is HashStatus.CALCULATED and (
                    stored.file_size != stat.st_size
                    or stored.modified_ns != stat.st_mtime_ns
                    or not is_valid_hash(stored.content_hash, stored.algorithm or "")
                ):
                    status = HashStatus.STALE
                else:
                    status = stored.status
        if status is not stored.status:
            error = (
                "登録された場所にファイルが見つかりません。"
                if status is HashStatus.MISSING
                else None
            )
            try:
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE files SET hash_status = ?, hash_error = ? "
                        "WHERE canonical_path = ?",
                        (status.value, error, str(candidate.resolve())),
                    )
            except sqlite3.Error as exc:
                raise DatabaseError(f"ハッシュ状態を更新できません: {exc}") from exc
        return status

    def find_hash_matches(
        self,
        digest: str,
        *,
        algorithm: str = CURRENT_HASH_ALGORITHM,
        statuses: tuple[HashStatus, ...] = (HashStatus.CALCULATED,),
    ) -> list[str]:
        """一致候補のパスだけを返し、DBやファイルは変更しない。"""
        if not is_valid_hash(digest, algorithm) or not statuses:
            return []
        allowed = tuple(dict.fromkeys(status.value for status in statuses))
        placeholders = ",".join("?" for _status in allowed)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"""
                    SELECT canonical_path FROM files
                    WHERE content_hash = ? AND hash_algorithm = ?
                      AND hash_status IN ({placeholders})
                    ORDER BY canonical_path COLLATE NOCASE, id
                    """,
                    (digest, algorithm, *allowed),
                ).fetchall()
        except sqlite3.Error as error:
            raise DatabaseError(f"ハッシュ候補を検索できません: {error}") from error
        return [str(row[0]) for row in rows]

    def get_file_missing_states(self) -> dict[str, bool]:
        """登録済みファイルの現在パスとmissing状態を返す。"""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT canonical_path, missing FROM files"
                ).fetchall()
            return {str(path): bool(missing) for path, missing in rows}
        except sqlite3.Error as error:
            raise DatabaseError(
                f"ファイル状態を読み込めません: {error}"
            ) from error

    def get_registered_file_records(self) -> list[RegisteredFileRecord]:
        """DBを更新せず、存在確認に必要な登録情報をすべて返す。"""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT files.id, files.canonical_path, files.filename,
                           files.format_name, files.size_bytes,
                           files.created_at, COALESCE(user_info.rating, 0),
                           COALESCE(user_info.memo, '')
                    FROM files
                    LEFT JOIN user_info ON user_info.file_id = files.id
                    ORDER BY files.canonical_path COLLATE NOCASE, files.id
                    """
                ).fetchall()
                tag_rows = connection.execute(
                    """
                    SELECT file_tags.file_id, tags.name
                    FROM file_tags
                    JOIN tags ON tags.id = file_tags.tag_id
                    ORDER BY tags.name COLLATE NOCASE
                    """
                ).fetchall()
            tags_by_id: dict[int, list[str]] = {}
            for file_id, tag_name in tag_rows:
                tags_by_id.setdefault(int(file_id), []).append(str(tag_name))
            return [
                RegisteredFileRecord(
                    file_id=int(row[0]),
                    path=str(row[1] or ""),
                    filename=str(row[2] or ""),
                    format_name=str(row[3] or ""),
                    size_bytes=int(row[4] or 0),
                    created_at=str(row[5] or ""),
                    rating=int(row[6] or 0),
                    tags=tuple(tags_by_id.get(int(row[0]), [])),
                    memo=str(row[7] or ""),
                )
                for row in rows
            ]
        except sqlite3.Error as error:
            raise DatabaseError(
                f"登録ファイル情報を読み込めません: {error}"
            ) from error

    def get_missing_file_details(self, path: Path) -> MissingFileDetails:
        """missing記録の以前の識別情報とユーザー情報を返す。"""
        canonical_path = str(path.resolve())
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT files.id, files.canonical_path, files.filename,
                           COALESCE(files.last_checked_at, ''),
                           COALESCE(user_info.rating, 0),
                           CASE WHEN TRIM(COALESCE(user_info.memo, '')) <> ''
                                THEN 1 ELSE 0 END
                    FROM files
                    LEFT JOIN user_info ON user_info.file_id = files.id
                    WHERE files.canonical_path = ? AND files.missing = 1
                    """,
                    (canonical_path,),
                ).fetchone()
                if row is None:
                    raise DatabaseError(
                        "見つからないファイルの記録を確認できません。"
                    )
                tag_rows = connection.execute(
                    """
                    SELECT tags.name
                    FROM tags
                    JOIN file_tags ON file_tags.tag_id = tags.id
                    WHERE file_tags.file_id = ?
                    ORDER BY tags.name COLLATE NOCASE
                    """,
                    (int(row[0]),),
                ).fetchall()
            return MissingFileDetails(
                path=str(row[1]),
                filename=str(row[2]),
                last_checked_at=str(row[3]) or "未確認",
                rating=int(row[4]),
                tags=tuple(str(tag[0]) for tag in tag_rows),
                has_memo=bool(row[5]),
            )
        except sqlite3.Error as error:
            raise DatabaseError(
                f"見つからないファイルの詳細を読み込めません: {error}"
            ) from error

    def relink_missing_file(self, old_path: Path, new_path: Path) -> None:
        """missing記録を選択された新パスへ付け替え、ユーザー情報を維持する。"""
        try:
            stat = new_path.stat()
            if not new_path.is_file():
                raise DatabaseError("選択されたパスはファイルではありません。")
            old_canonical = str(old_path.resolve())
            new_canonical = str(new_path.resolve())
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT id FROM files
                    WHERE canonical_path = ? AND missing = 1
                    """,
                    (old_canonical,),
                ).fetchone()
                if row is None:
                    raise DatabaseError(
                        "関連付け対象のmissing記録を確認できません。"
                    )
                file_id = int(row[0])
                duplicate = connection.execute(
                    "SELECT id FROM files WHERE canonical_path = ? AND id <> ?",
                    (new_canonical, file_id),
                ).fetchone()
                if duplicate is not None:
                    raise DatabaseError(
                        "選択されたファイルは別のMETAMI記録として登録済みです。"
                    )
                connection.execute(
                    """
                    UPDATE files
                    SET canonical_path = ?, filename = ?, format_name = ?,
                        size_bytes = ?, modified_ns = ?, missing = 0,
                        last_checked_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        new_canonical,
                        new_path.name,
                        new_path.suffix.removeprefix(".").casefold(),
                        stat.st_size,
                        stat.st_mtime_ns,
                        file_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO file_paths (
                        file_id, path, first_seen_at, last_seen_at
                    ) VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(file_id, path) DO UPDATE SET
                        last_seen_at = CURRENT_TIMESTAMP
                    """,
                    (file_id, new_canonical),
                )
        except DatabaseError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise DatabaseError(
                f"新しいファイルへ関連付けできません: {error}"
            ) from error

    def delete_missing_record(self, path: Path) -> None:
        """原本へ触れず、指定missing記録とfiles配下の関連だけを削除する。"""
        canonical_path = str(path.resolve())
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT id FROM files
                    WHERE canonical_path = ? AND missing = 1
                    """,
                    (canonical_path,),
                ).fetchone()
                if row is None:
                    raise DatabaseError(
                        "削除対象のmissing記録を確認できません。"
                    )
                cursor = connection.execute(
                    "DELETE FROM files WHERE id = ?",
                    (int(row[0]),),
                )
                if cursor.rowcount != 1:
                    raise DatabaseError("METAMIの記録を削除できませんでした。")
        except DatabaseError:
            raise
        except sqlite3.Error as error:
            raise DatabaseError(
                f"METAMIの記録を削除できません: {error}"
            ) from error

    def get_registered_paths_in_directory(self, directory: Path) -> list[Path]:
        """指定フォルダ直下として登録済みの現在パスを返す。"""
        try:
            target = str(directory.resolve()).casefold()
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT canonical_path FROM files"
                ).fetchall()
            result = [
                Path(str(row[0]))
                for row in rows
                if str(Path(str(row[0])).parent.resolve()).casefold() == target
            ]
            return sorted(result, key=lambda path: path.name.casefold())
        except (OSError, sqlite3.Error) as error:
            raise DatabaseError(
                f"登録済みパスを読み込めません: {error}"
            ) from error

    def get_schema_version(self) -> int:
        try:
            with self._connect() as connection:
                return self._schema_version(connection)
        except sqlite3.Error as error:
            raise DatabaseError(f"DBバージョンを確認できません: {error}") from error

    def get_favorite_paths(self) -> set[str]:
        """お気に入りに設定されている、現在パスの集合を返す。"""
        return {
            path for path, rating in self.get_file_ratings().items() if rating > 0
        }

    def get_file_ratings(self) -> dict[str, int]:
        """星1〜3が設定されている現在パスとランクを返す。"""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT files.canonical_path, user_info.rating
                    FROM files
                    JOIN user_info ON user_info.file_id = files.id
                    WHERE user_info.rating > 0
                    """
                ).fetchall()
            return {str(row[0]): int(row[1]) for row in rows}
        except sqlite3.Error as error:
            raise DatabaseError(
                f"お気に入り情報を読み込めません: {error}"
            ) from error

    def set_favorite(self, path: Path, favorite: bool) -> None:
        """指定ファイルのお気に入り状態だけを更新する。"""
        self.set_rating(path, 1 if favorite else 0)

    def set_rating(self, path: Path, rating: int) -> None:
        """指定ファイルの星ランク（0〜3）を更新する。"""
        if rating not in range(4):
            raise ValueError("星ランクは0から3で指定してください。")
        self.register_files([path])
        canonical_path = str(path.resolve())
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO user_info (
                        file_id, favorite, rating, updated_at
                    )
                    SELECT id, ?, ?, CURRENT_TIMESTAMP
                    FROM files WHERE canonical_path = ?
                    ON CONFLICT(file_id) DO UPDATE SET
                        favorite = excluded.favorite,
                        rating = excluded.rating,
                        updated_at = excluded.updated_at
                    """,
                    (int(rating > 0), rating, canonical_path),
                )
                if cursor.rowcount == 0:
                    raise DatabaseError(
                        "お気に入り対象のファイル情報が見つかりません。"
                    )
        except sqlite3.Error as error:
            raise DatabaseError(
                f"お気に入り情報を保存できません: {error}"
            ) from error

    def get_file_tags(self, path: Path) -> list[str]:
        """指定ファイルへ付与されているタグを表示名順で返す。"""
        canonical_path = str(path.resolve())
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT tags.name
                    FROM tags
                    JOIN file_tags ON file_tags.tag_id = tags.id
                    JOIN files ON files.id = file_tags.file_id
                    WHERE files.canonical_path = ?
                    ORDER BY tags.name COLLATE NOCASE
                    """,
                    (canonical_path,),
                ).fetchall()
            return [str(row[0]) for row in rows]
        except sqlite3.Error as error:
            raise DatabaseError(f"タグ情報を読み込めません: {error}") from error

    def get_tags_by_path(self) -> dict[str, list[str]]:
        """タグが付いた全ファイルの現在パスとタグ一覧を返す。"""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT files.canonical_path, tags.name
                    FROM files
                    JOIN file_tags ON file_tags.file_id = files.id
                    JOIN tags ON tags.id = file_tags.tag_id
                    ORDER BY tags.name COLLATE NOCASE
                    """
                ).fetchall()
        except sqlite3.Error as error:
            raise DatabaseError(f"タグ情報を読み込めません: {error}") from error
        result: dict[str, list[str]] = {}
        for path, name in rows:
            result.setdefault(str(path), []).append(str(name))
        return result

    def get_all_tags(self) -> list[str]:
        """現在いずれかのファイルで使用中のタグを返す。"""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT DISTINCT tags.name
                    FROM tags
                    JOIN file_tags ON file_tags.tag_id = tags.id
                    ORDER BY tags.name COLLATE NOCASE
                    """
                ).fetchall()
            return [str(row[0]) for row in rows]
        except sqlite3.Error as error:
            raise DatabaseError(f"タグ一覧を読み込めません: {error}") from error

    def get_tag_usage_counts(self) -> dict[str, int]:
        """使用中タグとファイルへの付与回数を多い順で返す。"""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT tags.name, COUNT(file_tags.file_id) AS usage_count
                    FROM tags
                    JOIN file_tags ON file_tags.tag_id = tags.id
                    GROUP BY tags.id, tags.name
                    ORDER BY usage_count DESC, tags.name COLLATE NOCASE
                    """
                ).fetchall()
            return {str(row[0]): int(row[1]) for row in rows}
        except sqlite3.Error as error:
            raise DatabaseError(
                f"タグ使用回数を読み込めません: {error}"
            ) from error

    def add_tag(self, path: Path, name: str) -> tuple[str, bool]:
        """タグを追加し、表示名と新規関連が作られたかを返す。"""
        normalized = name.strip()
        if not normalized:
            return "", False
        self.register_files([path])
        canonical_path = str(path.resolve())
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO tags (name) VALUES (?) ON CONFLICT(name) DO NOTHING",
                    (normalized,),
                )
                tag_row = connection.execute(
                    "SELECT id, name FROM tags WHERE name = ? COLLATE NOCASE",
                    (normalized,),
                ).fetchone()
                file_row = connection.execute(
                    "SELECT id FROM files WHERE canonical_path = ?",
                    (canonical_path,),
                ).fetchone()
                if tag_row is None or file_row is None:
                    raise DatabaseError("タグの登録対象を確認できません。")
                cursor = connection.execute(
                    """
                    INSERT INTO file_tags (file_id, tag_id)
                    VALUES (?, ?) ON CONFLICT(file_id, tag_id) DO NOTHING
                    """,
                    (int(file_row[0]), int(tag_row[0])),
                )
                return str(tag_row[1]), cursor.rowcount > 0
        except sqlite3.Error as error:
            raise DatabaseError(f"タグを追加できません: {error}") from error

    def remove_tag(self, path: Path, name: str) -> bool:
        """ファイルとの関連だけを外し、未使用になったタグを整理する。"""
        normalized = name.strip()
        if not normalized:
            return False
        canonical_path = str(path.resolve())
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    DELETE FROM file_tags
                    WHERE file_id = (
                        SELECT id FROM files WHERE canonical_path = ?
                    )
                    AND tag_id = (
                        SELECT id FROM tags WHERE name = ? COLLATE NOCASE
                    )
                    """,
                    (canonical_path, normalized),
                )
                connection.execute(
                    """
                    DELETE FROM tags
                    WHERE NOT EXISTS (
                        SELECT 1 FROM file_tags WHERE file_tags.tag_id = tags.id
                    )
                    """
                )
                return cursor.rowcount > 0
        except sqlite3.Error as error:
            raise DatabaseError(f"タグを削除できません: {error}") from error

    def get_memo(self, path: Path) -> str:
        """指定ファイルの保存済みメモを返す。未保存なら空文字。"""
        canonical_path = str(path.resolve())
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT user_info.memo
                    FROM files
                    LEFT JOIN user_info ON user_info.file_id = files.id
                    WHERE files.canonical_path = ?
                    """,
                    (canonical_path,),
                ).fetchone()
            return str(row[0] or "") if row is not None else ""
        except sqlite3.Error as error:
            raise DatabaseError(f"メモを読み込めません: {error}") from error

    def get_user_title(self, path: Path) -> str:
        """指定ファイルのユーザータイトルを返す。未設定なら空文字。"""
        canonical_path = str(path.resolve())
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT user_info.title
                    FROM files
                    LEFT JOIN user_info ON user_info.file_id = files.id
                    WHERE files.canonical_path = ?
                    """,
                    (canonical_path,),
                ).fetchone()
            return str(row[0] or "") if row is not None else ""
        except sqlite3.Error as error:
            raise DatabaseError(
                f"ユーザータイトルを読み込めません: {error}"
            ) from error

    def get_user_titles_by_path(self) -> dict[str, str]:
        """設定済みユーザータイトルを現在パスごとに返す。"""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT files.canonical_path, user_info.title
                    FROM files
                    JOIN user_info ON user_info.file_id = files.id
                    WHERE TRIM(user_info.title) <> ''
                    """
                ).fetchall()
            return {str(path): str(title) for path, title in rows}
        except sqlite3.Error as error:
            raise DatabaseError(
                f"ユーザータイトル一覧を読み込めません: {error}"
            ) from error

    def set_user_title(self, path: Path, title: str) -> str:
        """ユーザータイトルだけを保存し、正規化後の値を返す。"""
        normalized = self._normalize_user_title(title)
        self.register_files([path])
        canonical_path = str(path.resolve())
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO user_info (
                        file_id, favorite, rating, title, updated_at
                    )
                    SELECT id, 0, 0, ?, CURRENT_TIMESTAMP
                    FROM files WHERE canonical_path = ?
                    ON CONFLICT(file_id) DO UPDATE SET
                        title = excluded.title,
                        updated_at = excluded.updated_at
                    """,
                    (normalized, canonical_path),
                )
                if cursor.rowcount == 0:
                    raise DatabaseError(
                        "ユーザータイトルの保存対象を確認できません。"
                    )
            return normalized
        except sqlite3.Error as error:
            raise DatabaseError(
                f"ユーザータイトルを保存できません: {error}"
            ) from error

    def set_user_details(self, path: Path, title: str, memo: str) -> str:
        """タイトルとメモを既存の整理情報を保ったまま一括保存する。"""
        normalized = self._normalize_user_title(title)
        if len(memo) > 400:
            raise ValueError("メモは400文字以内で指定してください。")
        self.register_files([path])
        canonical_path = str(path.resolve())
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO user_info (
                        file_id, favorite, rating, title, memo, updated_at
                    )
                    SELECT id, 0, 0, ?, ?, CURRENT_TIMESTAMP
                    FROM files WHERE canonical_path = ?
                    ON CONFLICT(file_id) DO UPDATE SET
                        title = excluded.title,
                        memo = excluded.memo,
                        updated_at = excluded.updated_at
                    """,
                    (normalized, memo, canonical_path),
                )
                if cursor.rowcount == 0:
                    raise DatabaseError(
                        "タイトルとメモの保存対象を確認できません。"
                    )
            return normalized
        except sqlite3.Error as error:
            raise DatabaseError(
                f"タイトルとメモを保存できません: {error}"
            ) from error

    def get_memo_paths(self) -> set[str]:
        """空白以外のメモが保存されている現在パスの集合を返す。"""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT files.canonical_path
                    FROM files
                    JOIN user_info ON user_info.file_id = files.id
                    WHERE TRIM(user_info.memo) <> ''
                    """
                ).fetchall()
            return {str(row[0]) for row in rows}
        except sqlite3.Error as error:
            raise DatabaseError(
                f"メモの有無を読み込めません: {error}"
            ) from error

    def get_memos_by_path(self) -> dict[str, str]:
        """カード抜粋用に、空でないメモを現在パスごとに返す。"""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT files.canonical_path, user_info.memo
                    FROM files
                    JOIN user_info ON user_info.file_id = files.id
                    WHERE TRIM(user_info.memo) <> ''
                    """
                ).fetchall()
            return {str(path): str(memo) for path, memo in rows}
        except sqlite3.Error as error:
            raise DatabaseError(
                f"メモ一覧を読み込めません: {error}"
            ) from error

    def set_memo(self, path: Path, memo: str) -> None:
        """指定ファイルの一般メモを保存する。原本には書き込まない。"""
        if len(memo) > 400:
            raise ValueError("メモは400文字以内で指定してください。")
        self.register_files([path])
        canonical_path = str(path.resolve())
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO user_info (
                        file_id, favorite, rating, memo, updated_at
                    )
                    SELECT id, 0, 0, ?, CURRENT_TIMESTAMP
                    FROM files WHERE canonical_path = ?
                    ON CONFLICT(file_id) DO UPDATE SET
                        memo = excluded.memo,
                        updated_at = excluded.updated_at
                    """,
                    (memo, canonical_path),
                )
                if cursor.rowcount == 0:
                    raise DatabaseError("メモの保存対象を確認できません。")
        except sqlite3.Error as error:
            raise DatabaseError(f"メモを保存できません: {error}") from error

    @staticmethod
    def _normalize_user_title(title: str) -> str:
        normalized = title.strip()
        if len(normalized) > 40:
            raise ValueError(
                "ユーザータイトルは40文字以内で指定してください。"
            )
        return normalized

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _schema_version(connection: sqlite3.Connection) -> int:
        exists = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'schema_info'
            """
        ).fetchone()
        if exists is None:
            return 0
        row = connection.execute(
            "SELECT version FROM schema_info WHERE id = 1"
        ).fetchone()
        return int(row[0]) if row is not None else 0

    @staticmethod
    def _migrate_to_v1(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_info (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                canonical_path TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                format_name TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_info (
                file_id INTEGER PRIMARY KEY,
                favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
                memo TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS file_tags (
                file_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (file_id, tag_id),
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS files_filename_index ON files(filename);
            CREATE INDEX IF NOT EXISTS files_format_index ON files(format_name);
            """
        )

    @staticmethod
    def _migrate_to_v2(connection: sqlite3.Connection) -> None:
        user_info_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(user_info)")
        }
        if "rating" not in user_info_columns:
            connection.execute(
                """
                ALTER TABLE user_info
                ADD COLUMN rating INTEGER NOT NULL DEFAULT 0
                    CHECK (rating BETWEEN 0 AND 3)
                """
            )
        connection.execute(
            "UPDATE user_info SET rating = 1 WHERE favorite = 1 AND rating = 0"
        )
        files_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(files)")
        }
        if "missing" not in files_columns:
            connection.execute(
                """
                ALTER TABLE files
                ADD COLUMN missing INTEGER NOT NULL DEFAULT 0
                    CHECK (missing IN (0, 1))
                """
            )
        if "last_checked_at" not in files_columns:
            connection.execute(
                "ALTER TABLE files ADD COLUMN last_checked_at TEXT"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS file_paths (
                id INTEGER PRIMARY KEY,
                file_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(file_id, path),
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS file_paths_path_index ON file_paths(path)"
        )
        connection.execute(
            """
            INSERT INTO file_paths (
                file_id, path, first_seen_at, last_seen_at
            )
            SELECT id, canonical_path, created_at, updated_at
            FROM files
            WHERE 1
            ON CONFLICT(file_id, path) DO NOTHING
            """
        )

    @staticmethod
    def _migrate_to_v3(connection: sqlite3.Connection) -> None:
        user_info_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(user_info)")
        }
        if "title" not in user_info_columns:
            connection.execute(
                """
                ALTER TABLE user_info
                ADD COLUMN title TEXT NOT NULL DEFAULT ''
                """
            )
