"""Ver1.0.8候補のファイルハッシュ保存基盤を確認する。"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from storage import data_management
from storage.data_management import (
    DataManagementError,
    backup_database,
    get_database_info,
    restore_database,
    validate_restore_source,
)
from storage.database import DatabaseError, MetadataDatabase
from storage.file_hash import (
    CURRENT_HASH_ALGORITHM,
    CURRENT_HASH_DIGEST_SIZE,
    DEFAULT_HASH_CHUNK_SIZE,
    FileHashResult,
    HashStatus,
    calculate_file_hash,
    is_valid_hash,
)
from storage.migrations.manager import MIGRATIONS, MigrationError, migrate_database
from storage.migrations.v1_to_v2 import HASH_COLUMNS
from storage.schema_version import METAMI_SCHEMA_VERSION, get_user_version
from ui.data_management_dialog import DatabaseInfoDialog


class Ver108FileHashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _new_database(self, name: str = "metami.db") -> MetadataDatabase:
        database = MetadataDatabase(self.root / name)
        database.initialize()
        return database

    def _schema_one_database(self, name: str = "schema-one.db") -> MetadataDatabase:
        database = MetadataDatabase(self.root / name)
        with closing(sqlite3.connect(database.path)) as connection:
            database._migrate_to_v1(connection)
            database._migrate_to_v2(connection)
            database._migrate_to_v3(connection)
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        return database

    @staticmethod
    def _columns(path: Path) -> dict[str, tuple]:
        with closing(sqlite3.connect(path)) as connection:
            return {
                str(row[1]): tuple(row)
                for row in connection.execute("PRAGMA table_info(files)")
            }

    def test_blake2b_256_is_chunked_stable_and_handles_empty_unicode(self) -> None:
        self.assertEqual(CURRENT_HASH_ALGORITHM, "blake2b_256")
        self.assertEqual(CURRENT_HASH_DIGEST_SIZE, 32)
        self.assertEqual(DEFAULT_HASH_CHUNK_SIZE, 4 * 1024 * 1024)
        first = self.root / "日本語_😀.bin"
        first.write_bytes((b"abc123" * 400_000) + b"end")
        one = calculate_file_hash(first, chunk_size=1024)
        two = calculate_file_hash(first, chunk_size=1024 * 1024)
        expected = hashlib.blake2b(first.read_bytes(), digest_size=32).hexdigest()
        self.assertTrue(one.success)
        self.assertEqual(one.digest, expected)
        self.assertEqual(two.digest, expected)
        self.assertEqual(len(one.digest or ""), 64)
        self.assertRegex(one.digest or "", r"^[0-9a-f]{64}$")
        self.assertEqual(one.algorithm, CURRENT_HASH_ALGORITHM)
        self.assertEqual(one.file_size, first.stat().st_size)
        self.assertEqual(one.modified_ns, first.stat().st_mtime_ns)
        self.assertTrue(one.calculated_at.endswith("Z"))

        empty = self.root / "empty.bin"
        empty.touch()
        empty_result = calculate_file_hash(empty)
        self.assertTrue(empty_result.success)
        self.assertEqual(empty_result.file_size, 0)

    def test_different_content_and_invalid_hash_values(self) -> None:
        first = self.root / "first.png"
        second = self.root / "second.png"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        self.assertNotEqual(
            calculate_file_hash(first).digest,
            calculate_file_hash(second).digest,
        )
        valid = "a" * 64
        self.assertTrue(is_valid_hash(valid))
        for invalid in (None, "", "a" * 63, "A" * 64, "g" * 64):
            with self.subTest(invalid=invalid):
                self.assertFalse(is_valid_hash(invalid))
        self.assertFalse(is_valid_hash(valid, "another_algorithm"))

    def test_missing_directory_unreadable_and_unsupported_are_results(self) -> None:
        missing = calculate_file_hash(self.root / "missing.png")
        self.assertFalse(missing.success)
        self.assertEqual(missing.status, HashStatus.MISSING)
        directory = calculate_file_hash(self.root)
        self.assertEqual(directory.status, HashStatus.FAILED)
        source = self.root / "blocked.png"
        source.write_bytes(b"blocked")
        with patch("storage.file_hash.os.access", return_value=False):
            unreadable = calculate_file_hash(source)
        self.assertEqual(unreadable.status, HashStatus.FAILED)
        unsupported = calculate_file_hash(source, algorithm="sha256")
        self.assertIn("未対応", unsupported.error or "")

    def test_change_during_calculation_does_not_return_digest(self) -> None:
        source = self.root / "changing.bin"
        source.write_bytes(b"before" * 100)
        real_open = Path.open

        class ChangingReader:
            def __init__(self, wrapped) -> None:
                self.wrapped = wrapped
                self.changed = False

            def __enter__(self):
                self.wrapped.__enter__()
                return self

            def __exit__(self, *args):
                return self.wrapped.__exit__(*args)

            def read(self, size=-1):
                chunk = self.wrapped.read(size)
                if chunk and not self.changed:
                    self.changed = True
                    with real_open(source, "ab") as destination:
                        destination.write(b"changed")
                return chunk

        def changing_open(path: Path, *args, **kwargs):
            return ChangingReader(real_open(path, *args, **kwargs))

        with patch.object(Path, "open", changing_open):
            result = calculate_file_hash(source, chunk_size=64)
        self.assertFalse(result.success)
        self.assertEqual(result.status, HashStatus.STALE)
        self.assertIsNone(result.digest)
        self.assertIn("変更", result.error or "")

    def test_new_database_is_schema_two_with_hash_defaults(self) -> None:
        database = self._new_database()
        self.assertEqual(METAMI_SCHEMA_VERSION, 2)
        self.assertEqual(get_user_version(database.path), 2)
        columns = self._columns(database.path)
        self.assertTrue(set(HASH_COLUMNS).issubset(columns))
        self.assertEqual(columns["hash_status"][4], "'not_calculated'")
        image = self.root / "image.png"
        video = self.root / "video.mp4"
        image.write_bytes(b"image")
        video.write_bytes(b"video")
        database.register_files([image, video])
        self.assertEqual(database.get_file_hash(image).status, HashStatus.NOT_CALCULATED)
        self.assertEqual(database.get_file_hash(video).status, HashStatus.NOT_CALCULATED)

    def test_schema_one_to_two_migrates_without_hashing_or_data_loss(self) -> None:
        database = self._schema_one_database()
        image = self.root / "existing.png"
        image.write_bytes(b"existing")
        database.register_files([image])
        database.set_user_title(image, "既存タイトル")
        database.set_rating(image, 3)
        database.add_tag(image, "既存タグ")
        database.set_memo(image, "既存メモ")
        original_bytes = image.read_bytes()
        result = migrate_database(
            database.path,
            now=datetime(2026, 8, 1, 10, 0, 0),
        )
        self.assertEqual((result.from_version, result.to_version), (1, 2))
        self.assertEqual(
            result.backup_path.name,
            "metami_before_migration_v1_to_v2_20260801_100000.db",
        )
        self.assertEqual(get_user_version(database.path), 2)
        stored = database.get_file_hash(image)
        self.assertEqual(stored.status, HashStatus.NOT_CALCULATED)
        self.assertIsNone(stored.content_hash)
        self.assertEqual(database.get_user_title(image), "既存タイトル")
        self.assertEqual(database.get_memo(image), "既存メモ")
        self.assertEqual(database.get_file_tags(image), ["既存タグ"])
        self.assertEqual(image.read_bytes(), original_bytes)

    def test_zero_to_one_to_two_creates_two_step_backups(self) -> None:
        database = self._schema_one_database()
        with closing(sqlite3.connect(database.path)) as connection:
            connection.execute("PRAGMA user_version = 0")
            connection.commit()
        result = migrate_database(
            database.path,
            now=datetime(2026, 8, 1, 10, 5, 0),
        )
        self.assertEqual(get_user_version(database.path), 2)
        self.assertEqual(len(result.backup_paths), 2)
        self.assertIn("v0_to_v1", result.backup_paths[0].name)
        self.assertIn("v1_to_v2", result.backup_paths[1].name)

    def test_migration_backup_failure_and_step_failure_are_safe(self) -> None:
        database = self._schema_one_database()
        with patch.object(
            data_management,
            "backup_database",
            side_effect=DataManagementError("backup failed"),
        ):
            with self.assertRaises(MigrationError):
                migrate_database(database.path)
        self.assertEqual(get_user_version(database.path), 1)
        self.assertNotIn("content_hash", self._columns(database.path))

        def fail_step(connection: sqlite3.Connection) -> None:
            connection.execute("ALTER TABLE files ADD COLUMN temporary_test TEXT")
            raise sqlite3.OperationalError("forced failure")

        with patch.dict(MIGRATIONS, {1: fail_step}, clear=True):
            with self.assertRaisesRegex(MigrationError, "更新前のデータへ戻しました"):
                migrate_database(database.path)
        self.assertEqual(get_user_version(database.path), 1)
        self.assertNotIn("temporary_test", self._columns(database.path))

    def test_calculate_save_stale_missing_restore_and_recalculate(self) -> None:
        database = self._new_database()
        source = self.root / "sample.png"
        source.write_bytes(b"original")
        database.register_files([source])
        first = calculate_file_hash(source)
        database.save_file_hash_result(first)
        stored = database.get_file_hash(source)
        self.assertEqual(stored.status, HashStatus.CALCULATED)
        self.assertEqual(stored.content_hash, first.digest)
        self.assertIsNone(stored.error)

        source.write_bytes(b"changed")
        self.assertEqual(database.refresh_file_hash_status(source), HashStatus.STALE)
        self.assertEqual(database.get_file_hash(source).content_hash, first.digest)
        source.unlink()
        self.assertEqual(database.refresh_file_hash_status(source), HashStatus.MISSING)
        self.assertEqual(database.get_file_hash(source).content_hash, first.digest)
        source.write_bytes(b"original")
        second = calculate_file_hash(source)
        database.save_file_hash_result(second)
        self.assertEqual(second.digest, first.digest)
        self.assertEqual(database.get_file_hash(source).status, HashStatus.CALCULATED)

    def test_missing_uncalculated_and_failed_states(self) -> None:
        database = self._new_database()
        source = self.root / "missing.png"
        source.write_bytes(b"soon missing")
        database.register_files([source])
        source.unlink()
        self.assertEqual(database.refresh_file_hash_status(source), HashStatus.MISSING)
        self.assertIsNone(database.get_file_hash(source).content_hash)
        source.write_bytes(b"restored")
        failed = FileHashResult(
            False, source, CURRENT_HASH_ALGORITHM, None, None, None, None,
            HashStatus.FAILED, "test failure",
        )
        database.save_file_hash_result(failed)
        stored = database.get_file_hash(source)
        self.assertEqual(stored.status, HashStatus.FAILED)
        self.assertEqual(stored.error, "test failure")

    def test_hash_search_returns_candidates_only_for_valid_requested_states(self) -> None:
        database = self._new_database()
        first = self.root / "a.png"
        second = self.root / "b.png"
        first.write_bytes(b"same")
        second.write_bytes(b"same")
        database.register_files([first, second])
        result = calculate_file_hash(first)
        database.save_file_hash_result(result)
        database.save_file_hash_result(
            FileHashResult(
                True, second, result.algorithm, result.digest, second.stat().st_size,
                second.stat().st_mtime_ns, result.calculated_at,
                HashStatus.CALCULATED, None,
            )
        )
        matches = database.find_hash_matches(result.digest or "")
        self.assertEqual(matches, sorted([str(first.resolve()), str(second.resolve())]))
        second.write_bytes(b"different")
        database.refresh_file_hash_status(second)
        self.assertEqual(database.find_hash_matches(result.digest or ""), [str(first.resolve())])
        self.assertEqual(database.find_hash_matches("A" * 64), [])
        self.assertEqual(
            database.find_hash_matches(result.digest or "", algorithm="sha256"), []
        )
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())

    def test_backup_restore_preserves_hash_and_old_schema_is_upgraded(self) -> None:
        current = self._new_database("current.db")
        source = self.root / "source.png"
        source.write_bytes(b"hash me")
        current.register_files([source])
        result = calculate_file_hash(source)
        current.save_file_hash_result(result)
        backup = self.root / "schema-two.db"
        backup_database(current.path, backup)
        self.assertEqual(get_user_version(backup), 2)
        restore_database(backup, current.path)
        self.assertEqual(current.get_file_hash(source).content_hash, result.digest)

        old = self._schema_one_database("old.db")
        restore_database(old.path, current.path)
        self.assertEqual(get_user_version(current.path), 2)
        self.assertTrue(set(HASH_COLUMNS).issubset(self._columns(current.path)))
        with closing(sqlite3.connect(backup)) as connection:
            connection.execute("PRAGMA user_version = 3")
            connection.commit()
        with self.assertRaises(DataManagementError):
            validate_restore_source(backup, current.path)

    def test_database_info_counts_and_dialog(self) -> None:
        database = self._new_database()
        paths = [self.root / f"{name}.png" for name in ("calc", "stale", "fail", "missing", "new")]
        for index, path in enumerate(paths):
            path.write_bytes(f"content-{index}".encode())
        database.register_files(paths)
        for path in paths[:2]:
            database.save_file_hash_result(calculate_file_hash(path))
        paths[1].write_bytes(b"changed")
        database.refresh_file_hash_status(paths[1])
        database.save_file_hash_result(
            FileHashResult(False, paths[2], None, None, None, None, None, HashStatus.FAILED, "failure")
        )
        paths[3].unlink()
        database.refresh_file_hash_status(paths[3])
        info = get_database_info(database.path)
        self.assertEqual(info.standard_hash_algorithm, CURRENT_HASH_ALGORITHM)
        self.assertEqual(info.hash_calculated_files, 1)
        self.assertEqual(info.hash_stale_files, 1)
        self.assertEqual(info.hash_failed_files, 1)
        self.assertEqual(info.hash_missing_files, 1)
        self.assertEqual(info.hash_not_calculated_files, 1)
        dialog = DatabaseInfoDialog(info)
        try:
            self.assertEqual(dialog.value_labels["標準ハッシュ方式"].text(), "blake2b_256")
            self.assertEqual(dialog.value_labels["ハッシュ計算済み"].text(), "1件")
        finally:
            dialog.close()

    def test_help_explains_hash_scope_and_limits(self) -> None:
        text = (ROOT / "src" / "assets" / "help" / "data_management.md").read_text(encoding="utf-8")
        for expected in (
            "ファイル識別情報とハッシュ", "blake2b_256", "Pythonの標準機能",
            "ファイル名や保存場所とは別", "未計算でも異常ではありません",
            "動画", "再計算", "自動的に移動、統合、削除", "電子署名",
            "同じ内容のファイル", "メモ、タグ、星評価", "以前のMETAMIデータ",
            "コピー可能なN件へ一括コピー", "既存データへの上書きには対応していません",
            "元のDB記録を自動変更",
        ):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
