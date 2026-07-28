"""DB登録パスの存在状態を、保存処理から独立して確認する。"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from storage.database import RegisteredFileRecord


@dataclass(frozen=True)
class FilePresenceResult:
    record: RegisteredFileRecord
    status: str
    checked_at: str
    error: str = ""

    @property
    def is_available(self) -> bool:
        return self.status == "正常"


def check_registered_file(
    record: RegisteredFileRecord,
) -> FilePresenceResult:
    """1件を確認する。DBや実ファイルへの書き込みは行わない。"""
    checked_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    if not record.path.strip():
        return FilePresenceResult(
            record, "確認できません", checked_at, "登録パスが空です"
        )
    try:
        mode = Path(record.path).stat().st_mode
    except FileNotFoundError:
        return FilePresenceResult(record, "見つかりません", checked_at)
    except (OSError, ValueError) as error:
        return FilePresenceResult(
            record, "確認できません", checked_at, str(error)
        )
    if not stat.S_ISREG(mode):
        return FilePresenceResult(
            record,
            "確認できません",
            checked_at,
            "登録パスは通常ファイルではありません",
        )
    return FilePresenceResult(record, "正常", checked_at)
