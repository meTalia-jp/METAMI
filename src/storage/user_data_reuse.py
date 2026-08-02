"""同一内容ファイルへ利用者データを再利用できるか判定する読み取り専用基盤。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Mapping

from storage.file_hash import (
    SUPPORTED_HASH_ALGORITHMS,
    HashStatus,
    is_valid_hash,
)


@dataclass(frozen=True)
class FileUserData:
    """METAMIで利用者が入力した値。保存値そのものを保持する。"""

    memo: str = ""
    rating: int = 0
    title: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.rating not in range(4):
            raise ValueError("星評価は0から3で指定してください。")

    @property
    def has_data(self) -> bool:
        return bool(
            self.memo.strip()
            or self.rating > 0
            or self.title.strip()
            or any(tag.strip() for tag in self.tags)
        )

    def normalized_key(self) -> tuple[str, int, str, frozenset[str]]:
        """保存値を書き換えず、実質的な同一性比較用キーを返す。"""
        return (
            self.memo.strip(),
            int(self.rating),
            self.title.strip(),
            frozenset(
                tag.strip().casefold() for tag in self.tags if tag.strip()
            ),
        )


@dataclass(frozen=True)
class FileHashRecord:
    """同一ハッシュ検索で返すfilesレコードの読み取り専用情報。"""

    file_id: int
    canonical_path: str
    format_name: str
    missing: bool
    content_hash: str | None
    hash_algorithm: str | None
    hash_status: HashStatus
    hash_calculated_at: str | None
    hashed_file_size: int | None
    hashed_modified_ns: int | None
    hash_error: str | None


@dataclass(frozen=True)
class SourceCandidate:
    record: FileHashRecord
    user_data: FileUserData


class SourceDataStatus(StrEnum):
    NO_SOURCE_DATA = "no_source_data"
    UNIQUE_SOURCE_DATA = "unique_source_data"
    EQUIVALENT_SOURCE_DATA = "equivalent_source_data"
    CONFLICTING_SOURCE_DATA = "conflicting_source_data"


@dataclass(frozen=True)
class SourceDataResolution:
    status: SourceDataStatus
    candidates: tuple[SourceCandidate, ...] = ()
    user_data: FileUserData | None = None


class CopyDecision(StrEnum):
    COPYABLE = "copyable"
    SAME_RECORD = "same_record"
    HASH_ALGORITHM_MISMATCH = "hash_algorithm_mismatch"
    HASH_MISMATCH = "hash_mismatch"
    TARGET_HASH_NOT_CALCULATED = "target_hash_not_calculated"
    NO_SOURCE_USER_DATA = "no_source_user_data"
    SOURCE_DATA_CONFLICT = "source_data_conflict"
    TARGET_HAS_USER_DATA = "target_has_user_data"
    INVALID_SOURCE_HASH = "invalid_source_hash"
    INVALID_TARGET_HASH = "invalid_target_hash"


@dataclass(frozen=True)
class CopyAssessment:
    decision: CopyDecision
    source_data: FileUserData | None = None

    @property
    def copyable(self) -> bool:
        return self.decision is CopyDecision.COPYABLE


def is_valid_source_record(record: FileHashRecord) -> bool:
    """calculated、または正常な旧結果を保持するmissingか判定する。"""
    if not _has_complete_hash(record):
        return False
    if record.hash_status is HashStatus.CALCULATED:
        return not record.missing and not (record.hash_error or "").strip()
    if record.hash_status is HashStatus.MISSING:
        # hash_errorは利用者向けの自由形式文言であり、安定した判定キーではない。
        # missingの有効性は状態と保存済み正常結果の必須項目だけで判断する。
        return record.missing
    return False


def is_valid_target_record(record: FileHashRecord) -> bool:
    """コピー先として現在有効なcalculatedレコードか判定する。"""
    return (
        record.hash_status is HashStatus.CALCULATED
        and not record.missing
        and _has_complete_hash(record)
        and not (record.hash_error or "").strip()
    )


def select_source_candidates(
    records: Iterable[FileHashRecord],
    user_data_by_file_id: Mapping[int, FileUserData],
    *,
    target_file_id: int,
) -> tuple[SourceCandidate, ...]:
    """有効な識別情報と利用者データを持つ別レコードだけを返す。"""
    candidates = [
        SourceCandidate(record, user_data_by_file_id.get(record.file_id, FileUserData()))
        for record in records
        if record.file_id != target_file_id
        and is_valid_source_record(record)
        and user_data_by_file_id.get(record.file_id, FileUserData()).has_data
    ]
    return tuple(sorted(candidates, key=lambda item: (item.record.canonical_path.casefold(), item.record.file_id)))


def resolve_source_data(
    candidates: Iterable[SourceCandidate],
) -> SourceDataResolution:
    """コピー元候補の保存値が一種類に確定するか判定する。"""
    items = tuple(candidates)
    data_items = tuple(item for item in items if item.user_data.has_data)
    if not data_items:
        return SourceDataResolution(SourceDataStatus.NO_SOURCE_DATA, items)
    groups: dict[tuple[str, int, str, frozenset[str]], list[SourceCandidate]] = {}
    for item in data_items:
        groups.setdefault(item.user_data.normalized_key(), []).append(item)
    if len(groups) > 1:
        return SourceDataResolution(SourceDataStatus.CONFLICTING_SOURCE_DATA, items)
    status = (
        SourceDataStatus.UNIQUE_SOURCE_DATA
        if len(data_items) == 1
        else SourceDataStatus.EQUIVALENT_SOURCE_DATA
    )
    resolved_data = data_items[0].user_data if len(data_items) == 1 else None
    return SourceDataResolution(status, items, resolved_data)


def assess_copyability(
    target: FileHashRecord,
    target_user_data: FileUserData,
    sources: SourceDataResolution,
) -> CopyAssessment:
    """DBを変更せず、確定したコピー元とコピー先の組み合わせを判定する。"""
    if target.hash_status is not HashStatus.CALCULATED:
        return CopyAssessment(CopyDecision.TARGET_HASH_NOT_CALCULATED)
    if not is_valid_target_record(target):
        return CopyAssessment(CopyDecision.INVALID_TARGET_HASH)
    if sources.status is SourceDataStatus.NO_SOURCE_DATA:
        return CopyAssessment(CopyDecision.NO_SOURCE_USER_DATA)
    if sources.status is SourceDataStatus.CONFLICTING_SOURCE_DATA:
        return CopyAssessment(CopyDecision.SOURCE_DATA_CONFLICT)
    if target_user_data.has_data:
        return CopyAssessment(CopyDecision.TARGET_HAS_USER_DATA)
    for source in sources.candidates:
        if source.record.file_id == target.file_id:
            return CopyAssessment(CopyDecision.SAME_RECORD)
        if source.record.hash_algorithm != target.hash_algorithm:
            return CopyAssessment(CopyDecision.HASH_ALGORITHM_MISMATCH)
        if not is_valid_source_record(source.record):
            return CopyAssessment(CopyDecision.INVALID_SOURCE_HASH)
        if source.record.content_hash != target.content_hash:
            return CopyAssessment(CopyDecision.HASH_MISMATCH)
    return CopyAssessment(CopyDecision.COPYABLE, sources.user_data)


def _has_complete_hash(record: FileHashRecord) -> bool:
    return bool(
        record.hash_algorithm in SUPPORTED_HASH_ALGORITHMS
        and is_valid_hash(record.content_hash, record.hash_algorithm or "")
        and record.hash_calculated_at
        and record.hashed_file_size is not None
        and record.hashed_file_size >= 0
        and record.hashed_modified_ns is not None
        and record.hashed_modified_ns >= 0
    )
