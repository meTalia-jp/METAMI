"""Ver1.0.7候補の中央DBスキーマ管理基盤を確認する。"""

from __future__ import annotations

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

import main as app_main
from storage import data_management
from storage.data_management import (
    DataManagementError,
    backup_database,
    get_database_info,
    restore_database,
    validate_metami_database,
    validate_restore_source,
)
from storage.database import MetadataDatabase
from storage.migrations import manager
from storage.migrations.manager import (
    MIGRATIONS,
    MigrationError,
    migrate_database,
)
from storage.schema_version import (
    METAMI_SCHEMA_VERSION,
    SCHEMA_COMPATIBLE,
    SCHEMA_TOO_NEW,
    SCHEMA_UPDATE_REQUIRED,
    SchemaVersionError,
    check_schema_compatibility,
    get_user_version,
)
from ui.data_management_dialog import DatabaseInfoDialog


class Ver107SchemaManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _database(self, name: str = "metami.db") -> MetadataDatabase:
        database = MetadataDatabase(self.root / name)
        database.initialize()
        return database

    @staticmethod
    def _set_version(path: Path, version: int) -> None:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(f"PRAGMA user_version = {version}")
            connection.commit()

    @staticmethod
    def _schema_snapshot(path: Path) -> tuple[tuple, ...]:
        with closing(sqlite3.connect(path)) as connection:
            return tuple(
                connection.execute(
                    """
                    SELECT type, name, tbl_name, COALESCE(sql, '')
                    FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%'
                    ORDER BY type, name
                    """
                )
            )

    @staticmethod
    def _data_snapshot(path: Path) -> tuple[tuple[str, tuple], ...]:
        result: list[tuple[str, tuple]] = []
        with closing(sqlite3.connect(path)) as connection:
            tables = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                )
            ]
            for table in tables:
                rows = tuple(connection.execute(f'SELECT * FROM "{table}"'))
                result.append((table, rows))
        return tuple(result)

    def test_new_database_is_schema_one_and_valid(self) -> None:
        database = self._database()
        self.assertEqual(METAMI_SCHEMA_VERSION, 2)
        self.assertEqual(get_user_version(database.path), 2)
        self.assertEqual(validate_metami_database(database.path), "ok")

    def test_schema_versions_are_classified_without_writes(self) -> None:
        database = self._database()
        before = database.path.read_bytes()
        self.assertEqual(
            check_schema_compatibility(0).status, SCHEMA_UPDATE_REQUIRED
        )
        self.assertEqual(
            check_schema_compatibility(1).status, SCHEMA_UPDATE_REQUIRED
        )
        self.assertEqual(
            check_schema_compatibility(2).status, SCHEMA_COMPATIBLE
        )
        with self.assertRaises(SchemaVersionError):
            check_schema_compatibility(-1)
        self.assertEqual(before, database.path.read_bytes())

    def test_zero_to_one_preserves_schema_and_data_and_creates_backup(
        self,
    ) -> None:
        database = self._database()
        media = self.root / "sample.png"
        media.write_bytes(b"media")
        database.register_files([media])
        database.set_user_title(media, "保存タイトル")
        database.set_rating(media, 3)
        database.add_tag(media, "保存タグ")
        database.set_memo(media, "保存メモ")
        self._set_version(database.path, 0)
        schema_before = self._schema_snapshot(database.path)
        data_before = self._data_snapshot(database.path)

        result = migrate_database(
            database.path,
            target_version=1,
            now=datetime(2026, 7, 30, 17, 50, 0),
        )

        self.assertTrue(result.success)
        self.assertEqual((result.from_version, result.to_version), (0, 1))
        self.assertIsNotNone(result.backup_path)
        self.assertEqual(
            result.backup_path.name,
            "metami_before_migration_v0_to_v1_20260730_175000.db",
        )
        self.assertEqual(
            result.backup_path.parent, database.path.parent / "backups"
        )
        self.assertEqual(get_user_version(result.backup_path), 0)
        self.assertEqual(get_user_version(database.path), 1)
        self.assertEqual(schema_before, self._schema_snapshot(database.path))
        self.assertEqual(data_before, self._data_snapshot(database.path))
        self.assertEqual(validate_metami_database(database.path), "ok")

    def test_backup_failure_does_not_start_migration(self) -> None:
        database = self._database()
        self._set_version(database.path, 0)
        before = database.path.read_bytes()
        with patch.object(
            data_management,
            "backup_database",
            side_effect=DataManagementError("バックアップ不可"),
        ):
            with self.assertRaises(MigrationError):
                migrate_database(database.path, target_version=1)
        self.assertEqual(get_user_version(database.path), 0)
        self.assertEqual(before, database.path.read_bytes())

    def test_migration_failure_rolls_back_and_recovers_from_backup(self) -> None:
        database = self._database()
        self._set_version(database.path, 0)
        before = self._data_snapshot(database.path)

        def fail_step(connection: sqlite3.Connection) -> None:
            connection.execute("PRAGMA user_version = 1")
            raise sqlite3.OperationalError("forced migration failure")

        with patch.dict(MIGRATIONS, {0: fail_step}, clear=True):
            with self.assertRaisesRegex(
                MigrationError, "更新前のデータへ戻しました"
            ) as raised:
                migrate_database(database.path, target_version=1)
        self.assertIsNotNone(raised.exception.backup_path)
        self.assertEqual(get_user_version(database.path), 0)
        self.assertEqual(before, self._data_snapshot(database.path))

    def test_unregistered_step_is_not_executed(self) -> None:
        database = self._database()
        self._set_version(database.path, 0)
        with patch.dict(MIGRATIONS, {}, clear=True):
            with self.assertRaisesRegex(MigrationError, "未登録"):
                migrate_database(database.path)
        self.assertEqual(get_user_version(database.path), 0)
        self.assertFalse((database.path.parent / "backups").exists())

    def test_too_new_database_is_rejected_without_modification(self) -> None:
        database = self._database()
        self._set_version(database.path, 3)
        before = database.path.read_bytes()
        with patch("main.QMessageBox.critical") as critical:
            self.assertFalse(app_main.prepare_database(database))
        self.assertTrue(critical.called)
        self.assertEqual(get_user_version(database.path), 3)
        self.assertEqual(before, database.path.read_bytes())
        with self.assertRaises(DataManagementError):
            validate_restore_source(database.path, self.root / "current.db")

    def test_cancelled_zero_schema_startup_does_not_modify_database(self) -> None:
        database = self._database()
        self._set_version(database.path, 0)
        before = database.path.read_bytes()
        with patch("main.confirm_schema_update", return_value=False):
            self.assertFalse(app_main.prepare_database(database))
        self.assertEqual(get_user_version(database.path), 0)
        self.assertEqual(before, database.path.read_bytes())

    def test_confirmed_zero_schema_startup_migrates_once(self) -> None:
        database = self._database()
        self._set_version(database.path, 0)
        with (
            patch("main.confirm_schema_update", return_value=True),
            patch("main.QMessageBox.information"),
        ):
            self.assertTrue(app_main.prepare_database(database))
        self.assertEqual(get_user_version(database.path), 2)
        backups = list(
            (database.path.parent / "backups").glob(
                "metami_before_migration_v0_to_v1_*.db"
            )
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual(
            len(
                list(
                    (database.path.parent / "backups").glob(
                        "metami_before_migration_v1_to_v2_*.db"
                    )
                )
            ),
            1,
        )
        before_second_start = database.path.read_bytes()
        with patch("main.confirm_schema_update") as confirm:
            self.assertTrue(app_main.prepare_database(database))
        confirm.assert_not_called()
        self.assertEqual(before_second_start, database.path.read_bytes())
        self.assertEqual(
            len(list((database.path.parent / "backups").glob("*.db"))), 2
        )

    def test_manual_backup_preserves_schema_version(self) -> None:
        database = self._database()
        destination = self.root / "manual.db"
        backup_database(database.path, destination)
        self.assertEqual(get_user_version(destination), 2)

    def test_restore_schema_one_and_migrate_schema_zero(self) -> None:
        current = self._database("current.db")
        schema_one = self.root / "schema-one.db"
        backup_database(current.path, schema_one)
        restore_database(schema_one, current.path)
        self.assertEqual(get_user_version(current.path), 2)

        schema_zero = self.root / "schema-zero.db"
        backup_database(current.path, schema_zero)
        self._set_version(schema_zero, 0)
        restore_database(schema_zero, current.path)
        self.assertEqual(get_user_version(current.path), 2)
        self.assertTrue(
            list(
                (current.path.parent / "backups").glob(
                    "metami_before_restore_*.db"
                )
            )
        )
        self.assertTrue(
            list(
                (current.path.parent / "backups").glob(
                    "metami_before_migration_v0_to_v1_*.db"
                )
            )
        )

    def test_database_info_and_dialog_show_schema_compatibility(self) -> None:
        database = self._database()
        info = get_database_info(database.path)
        self.assertEqual(info.schema_version, 2)
        self.assertEqual(info.supported_schema_version, 2)
        self.assertEqual(info.compatibility_status, SCHEMA_COMPATIBLE)
        dialog = DatabaseInfoDialog(info)
        try:
            self.assertEqual(
                dialog.value_labels["DBスキーマバージョン"].text(), "2"
            )
            self.assertEqual(
                dialog.value_labels["METAMI対応スキーマ"].text(), "2"
            )
            self.assertEqual(dialog.value_labels["互換性"].text(), "正常")
        finally:
            dialog.close()

    def test_help_explains_schema_updates_and_safety(self) -> None:
        help_path = (
            ROOT / "src" / "assets" / "help" / "data_management.md"
        )
        text = help_path.read_text(encoding="utf-8")
        for expected in (
            "DBスキーマバージョン",
            "アプリのバージョンとは別",
            "自動的にバックアップ",
            "画像・動画",
            "古いMETAMIでは開けない",
            "強制終了しない",
            "自動回復",
            "利用者が",
        ):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
