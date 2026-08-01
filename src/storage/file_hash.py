"""ファイル内容識別用BLAKE2b-256の単体計算基盤。"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path


CURRENT_HASH_ALGORITHM = "blake2b_256"
CURRENT_HASH_DIGEST_SIZE = 32
DEFAULT_HASH_CHUNK_SIZE = 4 * 1024 * 1024
SUPPORTED_HASH_ALGORITHMS = frozenset({CURRENT_HASH_ALGORITHM})

_BLAKE2B_256_PATTERN = re.compile(r"[0-9a-f]{64}")


class HashStatus(StrEnum):
    """DBへ保存するハッシュ計算状態。"""

    NOT_CALCULATED = "not_calculated"
    CALCULATED = "calculated"
    STALE = "stale"
    FAILED = "failed"
    MISSING = "missing"


@dataclass(frozen=True)
class FileHashResult:
    """単体計算の成功・失敗と、計算時点のファイル状態。"""

    success: bool
    path: Path
    algorithm: str | None
    digest: str | None
    file_size: int | None
    modified_ns: int | None
    calculated_at: str | None
    status: HashStatus
    error: str | None


def calculate_file_hash(
    path: Path,
    *,
    algorithm: str = CURRENT_HASH_ALGORITHM,
    chunk_size: int = DEFAULT_HASH_CHUNK_SIZE,
) -> FileHashResult:
    """通常ファイルをチャンク読込し、内容ハッシュを返す。"""
    candidate = Path(path)
    if algorithm not in SUPPORTED_HASH_ALGORITHMS:
        return _failure(candidate, HashStatus.FAILED, f"未対応のハッシュ方式です: {algorithm}")
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        return _failure(candidate, HashStatus.FAILED, "チャンクサイズが不正です。")
    if not candidate.exists():
        return _failure(candidate, HashStatus.MISSING, "ファイルが見つかりません。")
    if not candidate.is_file():
        return _failure(candidate, HashStatus.FAILED, "通常のファイルではありません。")
    if not os.access(candidate, os.R_OK):
        return _failure(candidate, HashStatus.FAILED, "ファイルを読み取れません。")

    try:
        before = candidate.stat()
        digest = hashlib.blake2b(digest_size=CURRENT_HASH_DIGEST_SIZE)
        with candidate.open("rb") as source:
            while chunk := source.read(chunk_size):
                digest.update(chunk)
        after = candidate.stat()
    except OSError as error:
        status = HashStatus.MISSING if not candidate.exists() else HashStatus.FAILED
        return _failure(candidate, status, f"ハッシュを計算できません: {error}")

    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        return FileHashResult(
            False,
            candidate,
            algorithm,
            None,
            after.st_size,
            after.st_mtime_ns,
            None,
            HashStatus.STALE,
            "ハッシュ計算中にファイルが変更されました。",
        )
    value = digest.hexdigest()
    if not is_valid_hash(value, algorithm):
        return _failure(candidate, HashStatus.FAILED, "計算したハッシュ値が不正です。")
    return FileHashResult(
        True,
        candidate,
        algorithm,
        value,
        after.st_size,
        after.st_mtime_ns,
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        HashStatus.CALCULATED,
        None,
    )


def is_valid_hash(value: object, algorithm: str = CURRENT_HASH_ALGORITHM) -> bool:
    """方式に対応する保存可能な小文字16進値か確認する。"""
    return (
        algorithm == CURRENT_HASH_ALGORITHM
        and isinstance(value, str)
        and _BLAKE2B_256_PATTERN.fullmatch(value) is not None
    )


def _failure(path: Path, status: HashStatus, error: str) -> FileHashResult:
    return FileHashResult(False, path, None, None, None, None, None, status, error)
