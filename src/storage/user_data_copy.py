"""同一内容ファイルへMETAMI利用者データを安全に一括コピーする。"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable, Iterable

from storage.database import DatabaseError, MetadataDatabase
from storage.file_hash import HashStatus, calculate_file_hash
from storage.user_data_candidate_detection import detect_reuse_candidates
from storage.user_data_reuse import (
    CopyDecision,
    FileUserData,
    SourceDataResolution,
    is_valid_target_record,
)


class FileCopyStatus(StrEnum):
    COPIED = "copied"
    SKIPPED_TARGET_HAS_DATA = "skipped_target_has_data"
    SKIPPED_SOURCE_CONFLICT = "skipped_source_conflict"
    SKIPPED_NO_SOURCE_DATA = "skipped_no_source_data"
    SKIPPED_INVALID_TARGET_HASH = "skipped_invalid_target_hash"
    SKIPPED_INVALID_SOURCE_HASH = "skipped_invalid_source_hash"
    SKIPPED_HASH_MISMATCH = "skipped_hash_mismatch"
    SKIPPED_TARGET_MISSING = "skipped_target_missing"
    SKIPPED_FILE_CHANGED = "skipped_file_changed"
    SKIPPED_NOT_IN_CURRENT_FOLDER = "skipped_not_in_current_folder"
    FAILED_DB_WRITE = "failed_db_write"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class FileCopyPlan:
    target_file_id: int
    target_path: Path
    target_hash_algorithm: str
    target_content_hash: str
    source_resolution: SourceDataResolution
    copy_data: FileUserData


@dataclass(frozen=True)
class FileCopyResult:
    target_file_id: int
    target_path: Path
    status: FileCopyStatus
    reason: str
    copied_items: tuple[str, ...] = ()
    error_message: str = ""


@dataclass(frozen=True)
class BatchCopySummary:
    planned_count: int
    copied_count: int
    skipped_existing_data_count: int
    skipped_conflict_count: int
    skipped_invalid_hash_count: int
    skipped_changed_file_count: int
    failed_count: int
    cancelled: bool
    results: tuple[FileCopyResult, ...]


def copy_user_data_batch(
    database: MetadataDatabase,
    target_file_ids: Iterable[int],
    *,
    allowed_paths: Iterable[Path],
    cancel_event: threading.Event | None = None,
    progress: Callable[[int, int, Path], None] | None = None,
) -> BatchCopySummary:
    """現在一覧のtargetだけを再判定し、1件ずつ独立してコピーする。"""
    ids = tuple(dict.fromkeys(int(value) for value in target_file_ids))
    allowed = {_path_key(path) for path in allowed_paths}
    detections = {item.target.file_id: item for item in detect_reuse_candidates(database, ids)}
    records = database.get_file_hash_records_by_ids(ids)
    target_data = database.get_file_user_data_many(ids)
    plans: list[FileCopyPlan] = []
    results: list[FileCopyResult] = []
    for file_id in ids:
        record = records.get(file_id)
        path = Path(record.canonical_path) if record else Path("")
        if record is None or _path_key(path) not in allowed:
            results.append(_result(file_id, path, FileCopyStatus.SKIPPED_NOT_IN_CURRENT_FOLDER, "現在のフォルダ対象外"))
            continue
        detected = detections.get(file_id)
        if detected is None:
            if record.missing:
                status = FileCopyStatus.SKIPPED_TARGET_MISSING
                reason = "コピー先ファイルが見つかりません"
            elif not is_valid_target_record(record):
                status = FileCopyStatus.SKIPPED_INVALID_TARGET_HASH
                reason = "コピー先の識別情報が無効です"
            elif target_data.get(file_id, FileUserData()).has_data:
                status = FileCopyStatus.SKIPPED_TARGET_HAS_DATA
                reason = "現在のファイルにデータがあります"
            else:
                status = FileCopyStatus.SKIPPED_NO_SOURCE_DATA
                reason = "利用できるコピー元データがありません"
            results.append(_result(file_id, path, status, reason))
            continue
        decision = detected.assessment.decision
        if decision is not CopyDecision.COPYABLE:
            status = {
                CopyDecision.TARGET_HAS_USER_DATA: FileCopyStatus.SKIPPED_TARGET_HAS_DATA,
                CopyDecision.SOURCE_DATA_CONFLICT: FileCopyStatus.SKIPPED_SOURCE_CONFLICT,
                CopyDecision.NO_SOURCE_USER_DATA: FileCopyStatus.SKIPPED_NO_SOURCE_DATA,
                CopyDecision.INVALID_SOURCE_HASH: FileCopyStatus.SKIPPED_INVALID_SOURCE_HASH,
                CopyDecision.HASH_MISMATCH: FileCopyStatus.SKIPPED_HASH_MISMATCH,
            }.get(decision, FileCopyStatus.SKIPPED_INVALID_TARGET_HASH)
            results.append(_result(file_id, path, status, detected.display_status))
            continue
        source_values = tuple(candidate.user_data for candidate in detected.sources.candidates)
        if not source_values:
            results.append(_result(file_id, path, FileCopyStatus.SKIPPED_NO_SOURCE_DATA, "コピー元データなし"))
            continue
        # 元表記が異なる候補から代表を自動選択しない。
        if any(value != source_values[0] for value in source_values[1:]):
            results.append(_result(file_id, path, FileCopyStatus.SKIPPED_SOURCE_CONFLICT, "元表記が異なる候補が複数"))
            continue
        plans.append(FileCopyPlan(
            file_id, path, record.hash_algorithm or "", record.content_hash or "",
            detected.sources, source_values[0],
        ))

    total = len(plans)
    for index, plan in enumerate(plans, 1):
        if cancel_event is not None and cancel_event.is_set():
            results.extend(
                _result(item.target_file_id, item.target_path, FileCopyStatus.CANCELLED, "未処理")
                for item in plans[index - 1:]
            )
            break
        if progress:
            progress(index - 1, total, plan.target_path)
        results.append(_execute_plan(database, plan))
        if progress:
            progress(index, total, plan.target_path)
    return _summary(len(plans), results, bool(cancel_event and cancel_event.is_set()))


def _execute_plan(database: MetadataDatabase, plan: FileCopyPlan) -> FileCopyResult:
    path = plan.target_path
    if not path.exists():
        return _result(plan.target_file_id, path, FileCopyStatus.SKIPPED_TARGET_MISSING, "ファイルが見つかりません")
    calculated = calculate_file_hash(path, algorithm=plan.target_hash_algorithm)
    if calculated.status is HashStatus.STALE:
        return _result(plan.target_file_id, path, FileCopyStatus.SKIPPED_FILE_CHANGED, calculated.error or "計算中に変更")
    if not calculated.success:
        return _result(plan.target_file_id, path, FileCopyStatus.SKIPPED_INVALID_TARGET_HASH, calculated.error or "読取失敗")
    if calculated.algorithm != plan.target_hash_algorithm or calculated.digest != plan.target_content_hash:
        return _result(plan.target_file_id, path, FileCopyStatus.SKIPPED_HASH_MISMATCH, "保存済み識別情報と一致しません")
    try:
        after = path.stat()
    except OSError as error:
        return _result(plan.target_file_id, path, FileCopyStatus.SKIPPED_TARGET_MISSING, "ファイルを再確認できません", str(error))
    if after.st_size != calculated.file_size or after.st_mtime_ns != calculated.modified_ns:
        return _result(plan.target_file_id, path, FileCopyStatus.SKIPPED_FILE_CHANGED, "ハッシュ計算後にファイルが変更されました")
    try:
        copied = database.copy_file_user_data_if_empty(
            plan.target_file_id,
            plan.copy_data,
            expected_algorithm=plan.target_hash_algorithm,
            expected_digest=plan.target_content_hash,
        )
    except DatabaseError as error:
        return _result(plan.target_file_id, path, FileCopyStatus.FAILED_DB_WRITE, "DB更新失敗", str(error))
    if not copied:
        return _result(plan.target_file_id, path, FileCopyStatus.SKIPPED_TARGET_HAS_DATA, "実行直前に状態が変わりました")
    items = tuple(name for name, present in (
        ("メモ", bool(plan.copy_data.memo.strip())),
        ("タグ", bool(plan.copy_data.tags)),
        ("星評価", plan.copy_data.rating > 0),
        ("利用者タイトル", bool(plan.copy_data.title.strip())),
    ) if present)
    return FileCopyResult(plan.target_file_id, path, FileCopyStatus.COPIED, "コピー完了", items)


def _result(file_id: int, path: Path, status: FileCopyStatus, reason: str, error: str = "") -> FileCopyResult:
    return FileCopyResult(file_id, path, status, reason, (), error)


def _summary(planned: int, results: list[FileCopyResult], cancelled: bool) -> BatchCopySummary:
    count = lambda *values: sum(item.status in values for item in results)
    return BatchCopySummary(
        planned, count(FileCopyStatus.COPIED),
        count(FileCopyStatus.SKIPPED_TARGET_HAS_DATA),
        count(FileCopyStatus.SKIPPED_SOURCE_CONFLICT),
        count(FileCopyStatus.SKIPPED_INVALID_TARGET_HASH, FileCopyStatus.SKIPPED_INVALID_SOURCE_HASH, FileCopyStatus.SKIPPED_HASH_MISMATCH, FileCopyStatus.SKIPPED_TARGET_MISSING),
        count(FileCopyStatus.SKIPPED_FILE_CHANGED),
        count(FileCopyStatus.FAILED_DB_WRITE), cancelled, tuple(results),
    )


def _path_key(path: Path) -> str:
    return str(Path(path).resolve()).casefold()
