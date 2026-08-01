"""Ver0.8.6.1 missing記録整理の回帰試験。"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from storage.database import DatabaseError, MetadataDatabase  # noqa: E402
from storage.file_hash import HashStatus, calculate_file_hash  # noqa: E402
from ui.file_list_pane import FileListPane  # noqa: E402


class MissingDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = MetadataDatabase(self.root / "metami.db")
        self.database.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create_file(self, name: str, content: bytes) -> Path:
        path = self.root / name
        path.write_bytes(content)
        return path

    def _mark_missing(self, path: Path) -> None:
        self.database.register_files([path])
        path.unlink()
        states = self.database.check_registered_files()
        self.assertTrue(states[str(path.resolve())])

    def _store_hash_then_mark_missing(self, path: Path):
        result = calculate_file_hash(path)
        self.assertTrue(result.success)
        self.database.save_file_hash_result(result)
        path.unlink()
        self.database.check_registered_files()
        self.database.refresh_file_hash_status(path)
        return result

    def test_relink_keeps_user_information_and_path_history(self) -> None:
        old = self._create_file("old.png", b"old-original")
        self.database.register_files([old])
        self.database.set_rating(old, 2)
        self.database.add_tag(old, "人物")
        self.database.add_tag(old, "夜景")
        self.database.set_memo(old, "保存済みメモ")
        old_key = str(old.resolve())
        old.unlink()
        self.database.check_registered_files()
        details = self.database.get_missing_file_details(old)
        self.assertEqual(details.filename, "old.png")
        self.assertEqual(details.rating, 2)
        self.assertEqual(details.tags, ("人物", "夜景"))
        self.assertTrue(details.has_memo)

        replacement = self._create_file("replacement.png", b"replacement")
        before = replacement.read_bytes()
        self.database.relink_missing_file(old, replacement)

        states = self.database.get_file_missing_states()
        self.assertNotIn(old_key, states)
        self.assertFalse(states[str(replacement.resolve())])
        self.assertEqual(self.database.get_file_ratings()[str(replacement.resolve())], 2)
        self.assertEqual(self.database.get_file_tags(replacement), ["人物", "夜景"])
        self.assertEqual(self.database.get_memo(replacement), "保存済みメモ")
        with closing(sqlite3.connect(self.database.path)) as connection:
            paths = {
                row[0]
                for row in connection.execute(
                    "SELECT path FROM file_paths ORDER BY path"
                )
            }
        self.assertEqual(paths, {old_key, str(replacement.resolve())})
        self.assertEqual(replacement.read_bytes(), before)

    def test_relink_matching_hash_updates_hash_for_new_path(self) -> None:
        old = self._create_file("hashed-old.png", b"same-content")
        self.database.register_files([old])
        old_result = self._store_hash_then_mark_missing(old)
        replacement = self._create_file("hashed-new.png", b"same-content")
        new_result = calculate_file_hash(replacement)

        self.database.relink_missing_file(
            old, replacement, verified_hash=new_result,
            expected_content_hash=old_result.digest,
            expected_hash_algorithm=old_result.algorithm,
        )

        stored = self.database.get_file_hash(replacement)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.status, HashStatus.CALCULATED)
        self.assertEqual(stored.content_hash, new_result.digest)
        self.assertEqual(stored.file_size, new_result.file_size)
        self.assertEqual(stored.modified_ns, new_result.modified_ns)
        self.assertIsNone(stored.error)

    def test_relink_mismatching_hash_rejects_without_db_changes(self) -> None:
        old = self._create_file("mismatch-old.png", b"old-content")
        self.database.register_files([old])
        old_result = self._store_hash_then_mark_missing(old)
        replacement = self._create_file("mismatch-new.png", b"different")
        new_result = calculate_file_hash(replacement)

        with self.assertRaisesRegex(DatabaseError, "内容が一致しません"):
            self.database.relink_missing_file(
                old, replacement, verified_hash=new_result,
                expected_content_hash=old_result.digest,
                expected_hash_algorithm=old_result.algorithm,
            )

        self.assertTrue(self.database.get_file_missing_states()[str(old.resolve())])
        self.assertIsNone(self.database.get_file_hash(replacement))
        self.assertEqual(self.database.get_file_hash(old).content_hash, old_result.digest)

    def test_relink_without_valid_hash_resets_all_hash_fields(self) -> None:
        old = self._create_file("invalid-old.png", b"old")
        self.database.register_files([old])
        with closing(sqlite3.connect(self.database.path)) as connection:
            connection.execute(
                """UPDATE files SET content_hash = 'invalid', hash_algorithm = 'bad',
                   hash_status = 'missing', hash_calculated_at = 'past',
                   hashed_file_size = 3, hashed_modified_ns = 1,
                   hash_error = 'old error' WHERE canonical_path = ?""",
                (str(old.resolve()),),
            )
            connection.commit()
        old.unlink()
        self.database.check_registered_files()
        replacement = self._create_file("invalid-new.png", b"new")

        self.database.relink_missing_file(old, replacement)

        stored = self.database.get_file_hash(replacement)
        self.assertEqual(stored.status, HashStatus.NOT_CALCULATED)
        self.assertIsNone(stored.content_hash)
        self.assertIsNone(stored.algorithm)
        self.assertIsNone(stored.calculated_at)
        self.assertIsNone(stored.file_size)
        self.assertIsNone(stored.modified_ns)
        self.assertIsNone(stored.error)

    def test_relink_rejects_file_changed_after_hash_calculation(self) -> None:
        old = self._create_file("race-old.png", b"same")
        self.database.register_files([old])
        old_result = self._store_hash_then_mark_missing(old)
        replacement = self._create_file("race-new.png", b"same")
        new_result = calculate_file_hash(replacement)
        replacement.write_bytes(b"changed-after-calculation")

        with self.assertRaisesRegex(DatabaseError, "内容確認後にファイルが変更"):
            self.database.relink_missing_file(
                old, replacement, verified_hash=new_result,
                expected_content_hash=old_result.digest,
                expected_hash_algorithm=old_result.algorithm,
            )
        self.assertTrue(self.database.get_file_missing_states()[str(old.resolve())])

    def test_relink_rejects_target_already_registered(self) -> None:
        old = self._create_file("duplicate-old.png", b"old")
        replacement = self._create_file("already-registered.png", b"new")
        self.database.register_files([old, replacement])
        old.unlink()
        self.database.check_registered_files()

        with self.assertRaisesRegex(DatabaseError, "別のMETAMI記録として登録済み"):
            self.database.relink_missing_file(old, replacement)

        states = self.database.get_file_missing_states()
        self.assertTrue(states[str(old.resolve())])
        self.assertFalse(states[str(replacement.resolve())])

    def test_delete_removes_only_target_relations_and_preserves_tags(self) -> None:
        target = self._create_file("target.mp4", b"target-original")
        survivor = self._create_file("survivor.webp", b"survivor-original")
        self.database.register_files([target, survivor])
        self.database.set_rating(target, 3)
        self.database.add_tag(target, "共通")
        self.database.add_tag(target, "対象のみ")
        self.database.set_memo(target, "削除対象メモ")
        self.database.set_rating(survivor, 1)
        self.database.add_tag(survivor, "共通")
        self.database.set_memo(survivor, "残すメモ")
        survivor_before = survivor.read_bytes()
        self._mark_missing(target)

        self.database.delete_missing_record(target)

        with closing(sqlite3.connect(self.database.path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            target_count = connection.execute(
                "SELECT COUNT(*) FROM files WHERE canonical_path = ?",
                (str(target.resolve()),),
            ).fetchone()[0]
            child_counts = tuple(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE file_id NOT IN (SELECT id FROM files)"
                ).fetchone()[0]
                for table in ("user_info", "file_tags", "file_paths")
            )
            tag_names = {
                row[0] for row in connection.execute("SELECT name FROM tags")
            }
        self.assertEqual(target_count, 0)
        self.assertEqual(child_counts, (0, 0, 0))
        self.assertEqual(tag_names, {"共通", "対象のみ"})
        self.assertEqual(self.database.get_file_ratings()[str(survivor.resolve())], 1)
        self.assertEqual(self.database.get_file_tags(survivor), ["共通"])
        self.assertEqual(self.database.get_memo(survivor), "残すメモ")
        self.assertEqual(survivor.read_bytes(), survivor_before)

    def test_relink_rolls_back_when_history_insert_fails(self) -> None:
        old = self._create_file("rollback.png", b"old")
        self.database.register_files([old])
        old_key = str(old.resolve())
        old.unlink()
        self.database.check_registered_files()
        replacement = self._create_file("replacement.png", b"new")
        replacement_key = str(replacement.resolve())
        with closing(sqlite3.connect(self.database.path)) as connection:
            connection.execute(
                """
                CREATE TRIGGER force_file_paths_failure
                BEFORE INSERT ON file_paths
                BEGIN
                    SELECT RAISE(ABORT, 'forced failure');
                END
                """
            )
            connection.commit()

        with self.assertRaises(DatabaseError):
            self.database.relink_missing_file(old, replacement)

        states = self.database.get_file_missing_states()
        self.assertTrue(states[old_key])
        self.assertNotIn(replacement_key, states)
        with closing(sqlite3.connect(self.database.path)) as connection:
            history = {
                row[0] for row in connection.execute("SELECT path FROM file_paths")
            }
        self.assertEqual(history, {old_key})
        self.assertEqual(replacement.read_bytes(), b"new")

    def test_delete_rolls_back_when_related_delete_fails(self) -> None:
        target = self._create_file("delete-rollback.webp", b"original")
        self.database.register_files([target])
        self.database.set_rating(target, 3)
        self.database.add_tag(target, "保護タグ")
        self.database.set_memo(target, "保護メモ")
        self._mark_missing(target)
        key = str(target.resolve())
        with closing(sqlite3.connect(self.database.path)) as connection:
            connection.execute(
                """
                CREATE TRIGGER force_user_info_delete_failure
                BEFORE DELETE ON user_info
                BEGIN
                    SELECT RAISE(ABORT, 'forced delete failure');
                END
                """
            )
            connection.commit()

        with self.assertRaises(DatabaseError):
            self.database.delete_missing_record(target)

        self.assertTrue(self.database.get_file_missing_states()[key])
        self.assertEqual(self.database.get_file_ratings()[key], 3)
        self.assertEqual(self.database.get_file_tags(target), ["保護タグ"])
        self.assertEqual(self.database.get_memo(target), "保護メモ")


class MissingFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.existing = self.root / "existing.png"
        image = QImage(2, 2, QImage.Format.Format_ARGB32)
        image.fill(0xFFFFFFFF)
        self.assertTrue(image.save(str(self.existing)))
        self.missing = self.root / "missing.mp4"
        self.pane = FileListPane()
        self.pane.set_paths(
            [self.existing, self.missing],
            missing_paths={str(self.missing.resolve()).casefold()},
        )

    def tearDown(self) -> None:
        self.pane.release_media()
        self.pane.close()
        self.temporary.cleanup()

    def _displayed_names(self) -> list[str]:
        return [
            Path(self.pane.list_widget.item(index).data(Qt.ItemDataRole.UserRole)).name
            for index in range(self.pane.list_widget.count())
        ]

    def test_state_filters_and_missing_count(self) -> None:
        self.assertEqual(self._displayed_names(), ["existing.png"])
        self.assertEqual(
            self.pane.missing_count_label.text(), "見つからないファイル: 1件"
        )
        self.assertFalse(self.pane.missing_count_label.isHidden())

        self.pane.state_filter.setCurrentIndex(
            self.pane.state_filter.findData("missing")
        )
        self.assertEqual(self._displayed_names(), ["missing.mp4"])

        self.pane.state_filter.setCurrentIndex(
            self.pane.state_filter.findData("all")
        )
        self.assertEqual(
            self._displayed_names(), ["existing.png", "missing.mp4"]
        )


if __name__ == "__main__":
    unittest.main()
