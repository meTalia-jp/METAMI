"""Ver1.0.10段階Aの利用者データ再利用判定基盤を確認する。"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from dataclasses import FrozenInstanceError
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from storage.database import MetadataDatabase  # noqa: E402
from storage.file_hash import (  # noqa: E402
    CURRENT_HASH_ALGORITHM,
    HashStatus,
    calculate_file_hash,
)
from storage.schema_version import METAMI_SCHEMA_VERSION  # noqa: E402
from storage.user_data_reuse import (  # noqa: E402
    CopyDecision,
    FileHashRecord,
    FileUserData,
    SourceCandidate,
    SourceDataResolution,
    SourceDataStatus,
    assess_copyability,
    is_valid_source_record,
    resolve_source_data,
    select_source_candidates,
)


VALID_DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64


def record(
    file_id: int,
    *,
    digest: str | None = VALID_DIGEST,
    algorithm: str | None = CURRENT_HASH_ALGORITHM,
    status: HashStatus = HashStatus.CALCULATED,
    missing: bool = False,
    calculated_at: str | None = "2026-08-02T00:00:00Z",
    size: int | None = 10,
    modified_ns: int | None = 20,
    error: str | None = None,
) -> FileHashRecord:
    return FileHashRecord(
        file_id=file_id,
        canonical_path=f"media/{file_id}.png",
        format_name="png",
        missing=missing,
        content_hash=digest,
        hash_algorithm=algorithm,
        hash_status=status,
        hash_calculated_at=calculated_at,
        hashed_file_size=size,
        hashed_modified_ns=modified_ns,
        hash_error=error,
    )


class UserDataValueTests(unittest.TestCase):
    def test_empty_each_field_all_fields_and_frozen(self) -> None:
        self.assertFalse(FileUserData().has_data)
        cases = (
            FileUserData(memo="memo"),
            FileUserData(tags=("tag",)),
            FileUserData(rating=1),
            FileUserData(title="title"),
            FileUserData("memo", 3, "title", ("a", "b")),
        )
        self.assertTrue(all(item.has_data for item in cases))
        with self.assertRaises(FrozenInstanceError):
            cases[0].memo = "changed"  # type: ignore[misc]
        with self.assertRaises(ValueError):
            FileUserData(rating=4)

    def test_empty_rules_and_normalization(self) -> None:
        empty = FileUserData(" \n ", 0, "  ", ("", "  "))
        self.assertFalse(empty.has_data)
        left = FileUserData(" 完成\n本文 ", 3, "Title", (" 人物 ", "夕方", "人物"))
        right = FileUserData("完成\n本文", 3, "Title", ("夕方", "人物", "人物"))
        self.assertEqual(left.normalized_key(), right.normalized_key())
        self.assertEqual(left.memo, " 完成\n本文 ")
        self.assertNotEqual(
            left.normalized_key(), FileUserData("完成 本文", 3, "Title", left.tags).normalized_key()
        )
        self.assertNotEqual(
            left.normalized_key(), FileUserData("完成\n本文", 3, "title", left.tags).normalized_key()
        )
        self.assertNotEqual(
            left.normalized_key(), FileUserData("完成\n本文", 2, "Title", left.tags).normalized_key()
        )
        self.assertEqual(
            FileUserData(tags=("Tag",)).normalized_key(),
            FileUserData(tags=("tag", " TAG ")).normalized_key(),
        )


class ReuseDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = MetadataDatabase(self.root / "metami.db")
        self.database.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _file(self, name: str, content: bytes = b"same") -> tuple[Path, int]:
        path = self.root / name
        path.write_bytes(content)
        self.database.register_files([path])
        with closing(sqlite3.connect(self.database.path)) as connection:
            file_id = int(connection.execute(
                "SELECT id FROM files WHERE canonical_path = ?", (str(path.resolve()),)
            ).fetchone()[0])
        return path, file_id

    def test_get_user_data_empty_missing_id_and_all_fields(self) -> None:
        path, file_id = self._file("data.png")
        self.assertEqual(self.database.get_file_user_data(file_id), FileUserData())
        self.assertEqual(self.database.get_file_user_data(999999), FileUserData())
        self.assertFalse(self.database.has_file_user_data(file_id))
        self.database.set_user_details(path, " 作品 ", " メモ ")
        self.database.set_rating(path, 3)
        self.database.add_tag(path, "夕方")
        self.database.add_tag(path, "人物")
        data = self.database.get_file_user_data(file_id)
        self.assertEqual(data, FileUserData(" メモ ", 3, "作品", ("人物", "夕方")))
        self.assertTrue(self.database.has_file_user_data(file_id))

    def test_whitespace_user_info_is_empty_and_blank_tags_are_excluded(self) -> None:
        _path, file_id = self._file("blank.png")
        with closing(sqlite3.connect(self.database.path)) as connection:
            connection.execute(
                "INSERT INTO user_info (file_id, memo, rating, title) VALUES (?, '  ', 0, '  ')",
                (file_id,),
            )
            connection.execute("INSERT INTO tags (name) VALUES ('   ')")
            tag_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute("INSERT INTO file_tags (file_id, tag_id) VALUES (?, ?)", (file_id, tag_id))
            connection.commit()
        self.assertEqual(self.database.get_file_user_data(file_id).tags, ())
        self.assertFalse(self.database.has_file_user_data(file_id))

    def test_file_id_hash_search_filters_orders_excludes_and_does_not_write(self) -> None:
        first, first_id = self._file("B.png")
        second, second_id = self._file("a.png")
        third, _third_id = self._file("c.png", b"other")
        for path in (first, second, third):
            self.database.save_file_hash_result(calculate_file_hash(path))
        digest = calculate_file_hash(first).digest or ""
        before = self.database.path.read_bytes()
        found = self.database.find_file_records_by_hash(digest)
        self.assertEqual([item.file_id for item in found], [second_id, first_id])
        self.assertEqual(len(self.database.find_hash_matches(digest)), 2)
        self.assertEqual(
            [item.file_id for item in self.database.find_file_records_by_hash(digest, exclude_file_id=second_id)],
            [first_id],
        )
        self.assertEqual(self.database.find_file_records_by_hash(OTHER_DIGEST), [])
        self.assertEqual(self.database.find_file_records_by_hash(digest, algorithm="sha256"), [])
        self.assertEqual(self.database.find_file_records_by_hash(digest, statuses=(HashStatus.MISSING,)), [])
        self.assertEqual(self.database.path.read_bytes(), before)


class ReuseDecisionTests(unittest.TestCase):
    def test_source_candidate_status_data_and_self_filters(self) -> None:
        valid_missing = record(
            2, status=HashStatus.MISSING, missing=True,
            error="登録された場所にファイルが見つかりません。",
        )
        records = (
            record(1), valid_missing,
            record(3, status=HashStatus.NOT_CALCULATED),
            record(4, status=HashStatus.STALE),
            record(5, status=HashStatus.FAILED),
            record(6),
        )
        data = {1: FileUserData(memo="self"), 2: FileUserData(memo="source"), 6: FileUserData()}
        selected = select_source_candidates(records, data, target_file_id=1)
        self.assertEqual([item.record.file_id for item in selected], [2])
        self.assertTrue(is_valid_source_record(valid_missing))
        # missingのhash_errorは自由形式の表示文言なので、有効性判定に使わない。
        self.assertTrue(is_valid_source_record(record(
            7, status=HashStatus.MISSING, missing=True,
            error="任意の利用者向け説明文",
        )))
        self.assertFalse(is_valid_source_record(record(
            8, status=HashStatus.MISSING, missing=True,
            digest="bad", error="任意の利用者向け説明文",
        )))

    def test_source_resolution_unique_equivalent_and_conflicts(self) -> None:
        self.assertEqual(resolve_source_data(()).status, SourceDataStatus.NO_SOURCE_DATA)
        empty_candidate = SourceCandidate(record(9), FileUserData())
        empty_result = resolve_source_data((empty_candidate,))
        self.assertEqual(empty_result.status, SourceDataStatus.NO_SOURCE_DATA)
        self.assertEqual(empty_result.candidates, (empty_candidate,))
        first = SourceCandidate(record(1), FileUserData(" 完成 ", 3, "作品", ("人物", "夕方")))
        equivalent = SourceCandidate(record(2), FileUserData("完成", 3, "作品", ("夕方", "人物")))
        self.assertEqual(resolve_source_data((first,)).status, SourceDataStatus.UNIQUE_SOURCE_DATA)
        self.assertEqual(
            resolve_source_data((first, equivalent)).status,
            SourceDataStatus.EQUIVALENT_SOURCE_DATA,
        )
        equivalent_result = resolve_source_data((first, equivalent))
        self.assertEqual(
            [candidate.record.file_id for candidate in equivalent_result.candidates],
            [1, 2],
        )
        self.assertEqual(
            [candidate.user_data for candidate in equivalent_result.candidates],
            [first.user_data, equivalent.user_data],
        )
        self.assertIsNone(equivalent_result.user_data)
        variations = (
            FileUserData("別", 3, "作品", ("人物", "夕方")),
            FileUserData("完成", 2, "作品", ("人物", "夕方")),
            FileUserData("完成", 3, "別作品", ("人物", "夕方")),
            FileUserData("完成", 3, "作品", ("人物",)),
        )
        for index, data in enumerate(variations, 3):
            with self.subTest(data=data):
                other = SourceCandidate(record(index), data)
                self.assertEqual(
                    resolve_source_data((first, other)).status,
                    SourceDataStatus.CONFLICTING_SOURCE_DATA,
                )
                conflict = resolve_source_data((first, other))
                self.assertEqual(
                    [candidate.record.file_id for candidate in conflict.candidates],
                    [1, index],
                )
                self.assertIsNone(conflict.user_data)

    def test_copyability_success_and_source_failures(self) -> None:
        target = record(10)
        source = SourceCandidate(record(1), FileUserData(memo="memo"))
        unique = resolve_source_data((source,))
        self.assertEqual(
            assess_copyability(target, FileUserData(), unique).decision,
            CopyDecision.COPYABLE,
        )
        same = resolve_source_data((SourceCandidate(record(10), source.user_data),))
        self.assertEqual(assess_copyability(target, FileUserData(), same).decision, CopyDecision.SAME_RECORD)
        mismatch_algorithm = resolve_source_data((SourceCandidate(record(1, algorithm="sha256"), source.user_data),))
        self.assertEqual(assess_copyability(target, FileUserData(), mismatch_algorithm).decision, CopyDecision.HASH_ALGORITHM_MISMATCH)
        mismatch_digest = resolve_source_data((SourceCandidate(record(1, digest=OTHER_DIGEST), source.user_data),))
        self.assertEqual(assess_copyability(target, FileUserData(), mismatch_digest).decision, CopyDecision.HASH_MISMATCH)
        invalid = resolve_source_data((SourceCandidate(record(1, digest="bad"), source.user_data),))
        self.assertEqual(assess_copyability(target, FileUserData(), invalid).decision, CopyDecision.INVALID_SOURCE_HASH)
        self.assertEqual(
            assess_copyability(target, FileUserData(), resolve_source_data(())).decision,
            CopyDecision.NO_SOURCE_USER_DATA,
        )
        conflict = SourceDataResolution(SourceDataStatus.CONFLICTING_SOURCE_DATA, (source,))
        self.assertEqual(assess_copyability(target, FileUserData(), conflict).decision, CopyDecision.SOURCE_DATA_CONFLICT)

    def test_copyability_target_status_hash_and_each_user_field(self) -> None:
        source = resolve_source_data((SourceCandidate(record(1), FileUserData(memo="source")),))
        for status in (HashStatus.NOT_CALCULATED, HashStatus.STALE, HashStatus.FAILED):
            with self.subTest(status=status):
                self.assertEqual(
                    assess_copyability(record(10, status=status), FileUserData(), source).decision,
                    CopyDecision.TARGET_HASH_NOT_CALCULATED,
                )
        self.assertEqual(
            assess_copyability(record(10, digest="bad"), FileUserData(), source).decision,
            CopyDecision.INVALID_TARGET_HASH,
        )
        for data in (
            FileUserData(memo="memo"), FileUserData(tags=("tag",)),
            FileUserData(rating=1), FileUserData(title="title"),
        ):
            with self.subTest(data=data):
                self.assertEqual(
                    assess_copyability(record(10), data, source).decision,
                    CopyDecision.TARGET_HAS_USER_DATA,
                )
        self.assertEqual(METAMI_SCHEMA_VERSION, 2)


if __name__ == "__main__":
    unittest.main()
