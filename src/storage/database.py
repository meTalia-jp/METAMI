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
from storage.user_data_reuse import FileHashRecord, FileUserData


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

    def register_files(self, paths: Iterable[Path]) -> list[Path]:
        """基本情報を保存し、今回初めてDB登録されたパスを返す。"""
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
            return []
        try:
            with self._connect() as connection:
                canonical_paths = [record[0] for record in records]
                placeholders = ",".join("?" for _path in canonical_paths)
                existing = {
                    str(row[0]).casefold()
                    for row in connection.execute(
                        f"SELECT canonical_path FROM files "
                        f"WHERE canonical_path IN ({placeholders})",
                        canonical_paths,
                    ).fetchall()
                }
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
            return [
                Path(path) for path in canonical_paths
                if path.casefold() not in existing
            ]
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
                elif result.status is HashStatus.FAILED:
                    cursor = connection.execute(
                        """
                        UPDATE files SET
                            hash_status = ?, hash_error = ?
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

    def get_file_hashes(self, paths: Iterable[Path]) -> dict[str, StoredFileHash]:
        """複数パスの保存状態を、パスを変更せずまとめて返す。"""
        canonical_paths = list(
            dict.fromkeys(str(Path(path).resolve()) for path in paths)
        )
        result: dict[str, StoredFileHash] = {}
        try:
            with self._connect() as connection:
                for start in range(0, len(canonical_paths), 500):
                    batch = canonical_paths[start:start + 500]
                    if not batch:
                        continue
                    placeholders = ",".join("?" for _path in batch)
                    rows = connection.execute(
                        f"""
                        SELECT canonical_path, content_hash, hash_algorithm,
                               hash_status, hash_calculated_at, hashed_file_size,
                               hashed_modified_ns, hash_error
                        FROM files WHERE canonical_path IN ({placeholders})
                        """,
                        batch,
                    ).fetchall()
                    for row in rows:
                        try:
                            status = HashStatus(str(row[3]))
                        except ValueError:
                            status = HashStatus.STALE
                        if (
                            status is HashStatus.CALCULATED
                            and not is_valid_hash(row[1], str(row[2]))
                        ):
                            status = HashStatus.STALE
                        stored = StoredFileHash(
                            str(row[0]), row[1], row[2], status, row[4], row[5],
                            row[6], row[7],
                        )
                        result[str(Path(stored.path).resolve()).casefold()] = stored
        except sqlite3.Error as error:
            raise DatabaseError(f"ハッシュ情報を取得できません: {error}") from error
        return result

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

    def refresh_file_hash_statuses(
        self, paths: Iterable[Path]
    ) -> dict[str, HashStatus]:
        """複数項目をサイズとmtime_nsだけで確認し、変更分だけ保存する。"""
        candidates = [Path(path) for path in paths]
        stored_by_path = self.get_file_hashes(candidates)
        statuses: dict[str, HashStatus] = {}
        updates: list[tuple[str, str | None, str]] = []
        for path in candidates:
            key = str(path.resolve()).casefold()
            stored = stored_by_path.get(key)
            if stored is None:
                continue
            if not path.is_file():
                status = HashStatus.MISSING
            else:
                try:
                    stat = path.stat()
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
                        or not is_valid_hash(
                            stored.content_hash, stored.algorithm or ""
                        )
                    ):
                        status = HashStatus.STALE
                    else:
                        status = stored.status
            statuses[key] = status
            if status is not stored.status:
                error = (
                    "登録された場所にファイルが見つかりません。"
                    if status is HashStatus.MISSING else None
                )
                updates.append((status.value, error, str(path.resolve())))
        if updates:
            try:
                with self._connect() as connection:
                    connection.executemany(
                        "UPDATE files SET hash_status = ?, hash_error = ? "
                        "WHERE canonical_path = ?",
                        updates,
                    )
            except sqlite3.Error as error:
                raise DatabaseError(f"ハッシュ状態を更新できません: {error}") from error
        return statuses

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

    def find_file_records_by_hash(
        self,
        digest: str,
        *,
        algorithm: str = CURRENT_HASH_ALGORITHM,
        statuses: tuple[HashStatus, ...] = (HashStatus.CALCULATED,),
        exclude_file_id: int | None = None,
    ) -> list[FileHashRecord]:
        """同一ハッシュのfiles情報を安定した順序で読み取り専用取得する。"""
        if not is_valid_hash(digest, algorithm) or not statuses:
            return []
        allowed = tuple(dict.fromkeys(status.value for status in statuses))
        placeholders = ",".join("?" for _status in allowed)
        excluded_sql = " AND id <> ?" if exclude_file_id is not None else ""
        parameters: tuple[object, ...] = (digest, algorithm, *allowed)
        if exclude_file_id is not None:
            parameters += (exclude_file_id,)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"""
                    SELECT id, canonical_path, format_name, missing,
                           content_hash, hash_algorithm, hash_status,
                           hash_calculated_at, hashed_file_size,
                           hashed_modified_ns, hash_error
                    FROM files
                    WHERE content_hash = ? AND hash_algorithm = ?
                      AND hash_status IN ({placeholders}){excluded_sql}
                    ORDER BY canonical_path COLLATE NOCASE, id
                    """,
                    parameters,
                ).fetchall()
        except sqlite3.Error as error:
            raise DatabaseError(f"ハッシュ候補情報を取得できません: {error}") from error
        return [
            FileHashRecord(
                file_id=int(row[0]),
                canonical_path=str(row[1]),
                format_name=str(row[2]),
                missing=bool(row[3]),
                content_hash=row[4],
                hash_algorithm=row[5],
                hash_status=HashStatus(str(row[6])),
                hash_calculated_at=row[7],
                hashed_file_size=row[8],
                hashed_modified_ns=row[9],
                hash_error=row[10],
            )
            for row in rows
        ]

    def get_file_hash_records_by_ids(
        self, file_ids: Iterable[int]
    ) -> dict[int, FileHashRecord]:
        """複数file_idのfiles・ハッシュ情報を一括で読み取る。"""
        ids = list(dict.fromkeys(int(file_id) for file_id in file_ids))
        if not ids:
            return {}
        result: dict[int, FileHashRecord] = {}
        try:
            with self._connect() as connection:
                for start in range(0, len(ids), 500):
                    batch = ids[start:start + 500]
                    placeholders = ",".join("?" for _id in batch)
                    rows = connection.execute(
                        f"""
                        SELECT id, canonical_path, format_name, missing,
                               content_hash, hash_algorithm, hash_status,
                               hash_calculated_at, hashed_file_size,
                               hashed_modified_ns, hash_error
                        FROM files WHERE id IN ({placeholders})
                        """,
                        batch,
                    ).fetchall()
                    for row in rows:
                        record = self._file_hash_record_from_row(row)
                        result[record.file_id] = record
        except sqlite3.Error as error:
            raise DatabaseError(f"ファイル識別情報を一括取得できません: {error}") from error
        return result

    def find_file_records_by_hash_keys(
        self,
        hash_keys: Iterable[tuple[str, str]],
        *,
        statuses: tuple[HashStatus, ...] = (
            HashStatus.CALCULATED, HashStatus.MISSING,
        ),
    ) -> list[FileHashRecord]:
        """複数の(algorithm, digest)候補を一括検索する。"""
        keys = list(dict.fromkeys(
            (algorithm, digest) for algorithm, digest in hash_keys
            if is_valid_hash(digest, algorithm)
        ))
        if not keys or not statuses:
            return []
        allowed = tuple(dict.fromkeys(status.value for status in statuses))
        status_placeholders = ",".join("?" for _status in allowed)
        records: dict[int, FileHashRecord] = {}
        try:
            with self._connect() as connection:
                for start in range(0, len(keys), 200):
                    batch = keys[start:start + 200]
                    key_sql = " OR ".join(
                        "(hash_algorithm = ? AND content_hash = ?)" for _key in batch
                    )
                    parameters: list[object] = []
                    for algorithm, digest in batch:
                        parameters.extend((algorithm, digest))
                    parameters.extend(allowed)
                    rows = connection.execute(
                        f"""
                        SELECT id, canonical_path, format_name, missing,
                               content_hash, hash_algorithm, hash_status,
                               hash_calculated_at, hashed_file_size,
                               hashed_modified_ns, hash_error
                        FROM files
                        WHERE ({key_sql})
                          AND hash_status IN ({status_placeholders})
                        ORDER BY canonical_path COLLATE NOCASE, id
                        """,
                        parameters,
                    ).fetchall()
                    for row in rows:
                        record = self._file_hash_record_from_row(row)
                        records[record.file_id] = record
        except sqlite3.Error as error:
            raise DatabaseError(f"同一ハッシュ候補を一括取得できません: {error}") from error
        return sorted(
            records.values(),
            key=lambda record: (record.canonical_path.casefold(), record.file_id),
        )

    def get_file_user_data(self, file_id: int) -> FileUserData:
        """file_idの保存済み利用者データを返す。存在しなければ空DTO。"""
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT COALESCE(user_info.memo, ''),
                           COALESCE(user_info.rating, 0),
                           COALESCE(user_info.title, '')
                    FROM files
                    LEFT JOIN user_info ON user_info.file_id = files.id
                    WHERE files.id = ?
                    """,
                    (file_id,),
                ).fetchone()
                tag_rows = connection.execute(
                    """
                    SELECT tags.name
                    FROM file_tags
                    JOIN tags ON tags.id = file_tags.tag_id
                    WHERE file_tags.file_id = ? AND TRIM(tags.name) <> ''
                    ORDER BY tags.name COLLATE NOCASE, tags.name
                    """,
                    (file_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise DatabaseError(f"利用者データを読み込めません: {error}") from error
        if row is None:
            return FileUserData()
        return FileUserData(
            memo=str(row[0] or ""),
            rating=int(row[1] or 0),
            title=str(row[2] or ""),
            tags=tuple(str(tag[0]) for tag in tag_rows if str(tag[0]).strip()),
        )

    def get_file_user_data_many(
        self, file_ids: Iterable[int]
    ) -> dict[int, FileUserData]:
        """複数file_idの利用者データを、user_infoとタグ各1回ずつで取得する。"""
        ids = list(dict.fromkeys(int(file_id) for file_id in file_ids))
        result = {file_id: FileUserData() for file_id in ids}
        if not ids:
            return result
        try:
            with self._connect() as connection:
                for start in range(0, len(ids), 500):
                    batch = ids[start:start + 500]
                    placeholders = ",".join("?" for _id in batch)
                    rows = connection.execute(
                        f"""
                        SELECT files.id, COALESCE(user_info.memo, ''),
                               COALESCE(user_info.rating, 0),
                               COALESCE(user_info.title, '')
                        FROM files
                        LEFT JOIN user_info ON user_info.file_id = files.id
                        WHERE files.id IN ({placeholders})
                        """,
                        batch,
                    ).fetchall()
                    tags = connection.execute(
                        f"""
                        SELECT file_tags.file_id, tags.name
                        FROM file_tags JOIN tags ON tags.id = file_tags.tag_id
                        WHERE file_tags.file_id IN ({placeholders})
                          AND TRIM(tags.name) <> ''
                        ORDER BY tags.name COLLATE NOCASE, tags.name
                        """,
                        batch,
                    ).fetchall()
                    tags_by_id: dict[int, list[str]] = {}
                    for file_id, name in tags:
                        tags_by_id.setdefault(int(file_id), []).append(str(name))
                    for row in rows:
                        file_id = int(row[0])
                        result[file_id] = FileUserData(
                            memo=str(row[1] or ""), rating=int(row[2] or 0),
                            title=str(row[3] or ""),
                            tags=tuple(tags_by_id.get(file_id, ())),
                        )
        except sqlite3.Error as error:
            raise DatabaseError(f"利用者データを一括取得できません: {error}") from error
        return result

    def copy_file_user_data_if_empty(
        self,
        target_file_id: int,
        data: FileUserData,
        *,
        expected_algorithm: str,
        expected_digest: str,
    ) -> bool:
        """targetが空かつ識別情報不変の場合だけ、1 transactionで4項目を保存する。"""
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT files.missing, files.hash_status,
                           files.hash_algorithm, files.content_hash,
                           COALESCE(user_info.memo, ''),
                           COALESCE(user_info.rating, 0),
                           COALESCE(user_info.title, ''),
                           EXISTS(SELECT 1 FROM file_tags WHERE file_id = files.id)
                    FROM files
                    LEFT JOIN user_info ON user_info.file_id = files.id
                    WHERE files.id = ?
                    """,
                    (int(target_file_id),),
                ).fetchone()
                if row is None:
                    return False
                unchanged = (
                    not bool(row[0])
                    and str(row[1]) == HashStatus.CALCULATED.value
                    and str(row[2] or "") == expected_algorithm
                    and str(row[3] or "") == expected_digest
                )
                empty = (
                    not str(row[4] or "").strip()
                    and int(row[5] or 0) == 0
                    and not str(row[6] or "").strip()
                    and not bool(row[7])
                )
                if not unchanged or not empty:
                    return False
                connection.execute(
                    """
                    INSERT INTO user_info (
                        file_id, favorite, rating, title, memo, updated_at
                    ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(file_id) DO UPDATE SET
                        favorite = excluded.favorite,
                        rating = excluded.rating,
                        title = excluded.title,
                        memo = excluded.memo,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(target_file_id), int(data.rating > 0), int(data.rating),
                        data.title, data.memo,
                    ),
                )
                for tag in data.tags:
                    if not tag.strip():
                        continue
                    connection.execute(
                        "INSERT INTO tags (name) VALUES (?) ON CONFLICT(name) DO NOTHING",
                        (tag,),
                    )
                    tag_row = connection.execute(
                        "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (tag,)
                    ).fetchone()
                    if tag_row is None:
                        raise sqlite3.IntegrityError("タグを保存できません。")
                    connection.execute(
                        "INSERT INTO file_tags (file_id, tag_id) VALUES (?, ?)",
                        (int(target_file_id), int(tag_row[0])),
                    )
                return True
        except sqlite3.Error as error:
            raise DatabaseError(f"利用者データをコピーできません: {error}") from error

    def get_file_ids_by_paths(self, paths: Iterable[Path]) -> tuple[int, ...]:
        """登録済みパスに対応するfile_idを入力順で一括取得する。"""
        canonical = list(dict.fromkeys(str(Path(path).resolve()) for path in paths))
        if not canonical:
            return ()
        ids_by_path: dict[str, int] = {}
        try:
            with self._connect() as connection:
                for start in range(0, len(canonical), 500):
                    batch = canonical[start:start + 500]
                    placeholders = ",".join("?" for _path in batch)
                    rows = connection.execute(
                        f"SELECT id, canonical_path FROM files "
                        f"WHERE canonical_path IN ({placeholders})",
                        batch,
                    ).fetchall()
                    ids_by_path.update((str(path), int(file_id)) for file_id, path in rows)
        except sqlite3.Error as error:
            raise DatabaseError(f"登録済みfile_idを取得できません: {error}") from error
        return tuple(ids_by_path[path] for path in canonical if path in ids_by_path)

    def has_file_user_data(self, file_id: int) -> bool:
        """file_idに実質的な利用者入力があるか返す。"""
        return self.get_file_user_data(file_id).has_data

    @staticmethod
    def _file_hash_record_from_row(row) -> FileHashRecord:
        return FileHashRecord(
            file_id=int(row[0]), canonical_path=str(row[1]),
            format_name=str(row[2]), missing=bool(row[3]),
            content_hash=row[4], hash_algorithm=row[5],
            hash_status=HashStatus(str(row[6])), hash_calculated_at=row[7],
            hashed_file_size=row[8], hashed_modified_ns=row[9],
            hash_error=row[10],
        )

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

    def relink_missing_file(
        self,
        old_path: Path,
        new_path: Path,
        *,
        verified_hash: FileHashResult | None = None,
        expected_content_hash: str | None = None,
        expected_hash_algorithm: str | None = None,
    ) -> None:
        """missing記録を新パスへ付け替え、ハッシュ情報も原子的に整合させる。"""
        try:
            stat = new_path.stat()
            if not new_path.is_file():
                raise DatabaseError("選択されたパスはファイルではありません。")
            if verified_hash is not None:
                if (
                    not verified_hash.success
                    or verified_hash.status is not HashStatus.CALCULATED
                    or not is_valid_hash(
                        verified_hash.digest, verified_hash.algorithm or ""
                    )
                    or verified_hash.calculated_at is None
                    or verified_hash.file_size is None
                    or verified_hash.modified_ns is None
                ):
                    raise DatabaseError("選択したファイルの内容を確認できません。")
                if (
                    verified_hash.digest != expected_content_hash
                    or verified_hash.algorithm != expected_hash_algorithm
                ):
                    raise DatabaseError(
                        "選択したファイルは、以前登録されていたファイルと内容が一致しません。\n"
                        "この登録項目の保存場所として設定できません。\n"
                        "別のファイルを選択してください。"
                    )
                if (
                    stat.st_size != verified_hash.file_size
                    or stat.st_mtime_ns != verified_hash.modified_ns
                ):
                    raise DatabaseError("内容確認後にファイルが変更されました。もう一度選択してください。")
            old_canonical = str(old_path.resolve())
            new_canonical = str(new_path.resolve())
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT id, content_hash, hash_algorithm, hash_status,
                           hash_calculated_at, hashed_file_size, hashed_modified_ns
                    FROM files
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
                current_stat = new_path.stat()
                if verified_hash is not None:
                    if (
                        row[1] != expected_content_hash
                        or row[2] != expected_hash_algorithm
                        or str(row[3]) != HashStatus.MISSING.value
                        or not row[4]
                        or row[5] is None
                        or row[6] is None
                        or not is_valid_hash(row[1], str(row[2]))
                    ):
                        raise DatabaseError("保存済みのファイル識別情報が変更されています。")
                    if (
                        current_stat.st_size != verified_hash.file_size
                        or current_stat.st_mtime_ns != verified_hash.modified_ns
                    ):
                        raise DatabaseError(
                            "内容確認後にファイルが変更されました。もう一度選択してください。"
                        )
                    hash_values = (
                        verified_hash.digest,
                        verified_hash.algorithm,
                        HashStatus.CALCULATED.value,
                        verified_hash.calculated_at,
                        verified_hash.file_size,
                        verified_hash.modified_ns,
                        None,
                    )
                else:
                    hash_values = (
                        None, None, HashStatus.NOT_CALCULATED.value,
                        None, None, None, None,
                    )
                connection.execute(
                    """
                    UPDATE files
                    SET canonical_path = ?, filename = ?, format_name = ?,
                        size_bytes = ?, modified_ns = ?, missing = 0,
                        content_hash = ?, hash_algorithm = ?, hash_status = ?,
                        hash_calculated_at = ?, hashed_file_size = ?,
                        hashed_modified_ns = ?, hash_error = ?,
                        last_checked_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        new_canonical, new_path.name,
                        new_path.suffix.removeprefix(".").casefold(),
                        current_stat.st_size, current_stat.st_mtime_ns,
                        *hash_values, file_id,
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
