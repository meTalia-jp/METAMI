"""今回calculatedになったfile_idだけを対象とする再利用候補検出。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from storage.database import MetadataDatabase
from storage.file_hash import HashStatus
from storage.user_data_reuse import (
    CopyAssessment,
    CopyDecision,
    FileHashRecord,
    FileUserData,
    SourceCandidate,
    SourceDataResolution,
    SourceDataStatus,
    assess_copyability,
    is_valid_target_record,
    resolve_source_data,
    select_source_candidates,
)


@dataclass(frozen=True)
class ReuseCandidateResult:
    target: FileHashRecord
    sources: SourceDataResolution
    assessment: CopyAssessment
    available_items: tuple[str, ...]
    display_status: str

    @property
    def target_filename(self) -> str:
        return Path(self.target.canonical_path).name

    @property
    def notification_key(self) -> tuple[int, tuple[int, ...], str, str]:
        return (
            self.target.file_id,
            tuple(sorted(item.record.file_id for item in self.sources.candidates)),
            self.target.hash_algorithm or "",
            self.target.content_hash or "",
        )


def detect_reuse_candidates(
    database: MetadataDatabase,
    target_file_ids: Iterable[int],
) -> tuple[ReuseCandidateResult, ...]:
    """DBを更新せず、今回成功したtarget群の候補を一括判定する。"""
    target_ids = tuple(dict.fromkeys(int(file_id) for file_id in target_file_ids))
    targets = database.get_file_hash_records_by_ids(target_ids)
    valid_targets = {
        file_id: target for file_id, target in targets.items()
        if target.hash_status is HashStatus.CALCULATED
        and is_valid_target_record(target)
    }
    keys = (
        (target.hash_algorithm or "", target.content_hash or "")
        for target in valid_targets.values()
    )
    records = database.find_file_records_by_hash_keys(keys)
    data_by_id = database.get_file_user_data_many(
        record.file_id for record in records
    )
    records_by_key: dict[tuple[str, str], list[FileHashRecord]] = {}
    for record in records:
        records_by_key.setdefault(
            (record.hash_algorithm or "", record.content_hash or ""), []
        ).append(record)

    results: list[ReuseCandidateResult] = []
    for target_id in target_ids:
        target = valid_targets.get(target_id)
        if target is None:
            continue
        same_hash = records_by_key.get(
            (target.hash_algorithm or "", target.content_hash or ""), []
        )
        candidates = select_source_candidates(
            same_hash, data_by_id, target_file_id=target.file_id
        )
        if not candidates:
            continue
        resolution = resolve_source_data(candidates)
        assessment = assess_copyability(
            target,
            data_by_id.get(target.file_id, FileUserData()),
            resolution,
        )
        results.append(
            ReuseCandidateResult(
                target, resolution, assessment,
                _available_items(candidates),
                _display_status(resolution, assessment),
            )
        )
    return tuple(results)


def _available_items(candidates: Iterable[SourceCandidate]) -> tuple[str, ...]:
    items: list[str] = []
    data = [candidate.user_data for candidate in candidates]
    if any(item.memo.strip() for item in data):
        items.append("メモ")
    if any(any(tag.strip() for tag in item.tags) for item in data):
        items.append("タグ")
    if any(item.rating > 0 for item in data):
        items.append("星評価")
    if any(item.title.strip() for item in data):
        items.append("タイトル")
    return tuple(items)


def _display_status(
    resolution: SourceDataResolution, assessment: CopyAssessment
) -> str:
    if assessment.decision is CopyDecision.TARGET_HAS_USER_DATA:
        return "現在のファイルにデータあり"
    if resolution.status is SourceDataStatus.CONFLICTING_SOURCE_DATA:
        return "異なるデータの候補が複数"
    if assessment.decision is CopyDecision.COPYABLE:
        if resolution.status is SourceDataStatus.EQUIVALENT_SOURCE_DATA:
            return "同じデータの候補が複数"
        return "利用可能"
    return "対象外または識別情報不正"
