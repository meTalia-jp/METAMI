"""Ver1.0.6候補の中央METAMIデータ管理を確認する。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings
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
    timestamped_database_name,
    validate_metami_database,
    validate_restore_source,
    with_database_suffix,
)
from storage.database import MetadataDatabase
from ui.data_management_dialog import (
    DATA_MANAGEMENT_HELP_PATH,
    DatabaseInfoDialog,
)
from ui.main_window import MainWindow


class Ver106DataManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = MetadataDatabase(self.root / "data" / "metami.db")
        self.database.initialize()
        self.media = self.root / "media" / "sample.png"
        self.media.parent.mkdir(parents=True)
        self.media.write_bytes(b"test-media")
        self.database.register_files([self.media])
        self.database.set_user_title(self.media, "バックアップ時タイトル")
        self.database.set_rating(self.media, 2)
        self.database.add_tag(self.media, "確認用")
        self.database.set_memo(self.media, "保存済みメモ")
        self.settings = QSettings(
            str(self.root / "settings.ini"), QSettings.Format.IniFormat
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _close(window: MainWindow) -> None:
        window.file_list_pane.release_media()
        window.preview_pane.release_media()
        window.close()

    def test_menu_order_and_data_management_actions(self) -> None:
        window = MainWindow(self.database, settings=self.settings)
        try:
            menus = [action.text() for action in window.menuBar().actions()]
            self.assertEqual(
                menus,
                ["ファイル(&F)", "表示(&V)", "データ管理(&D)", "ヘルプ(&H)"],
            )
            action_texts = [
                action.text() for action in window.data_menu.actions()
            ]
            self.assertEqual(
                action_texts,
                [
                    "METAMIデータをバックアップ…",
                    "METAMIデータを復元…",
                    "",
                    "データ保存場所を開く",
                    "データベース情報…",
                ],
            )
            self.assertNotIn("ハッシュ管理", action_texts)
            self.assertNotIn("miniDB", action_texts)
            self.assertEqual(window.database.path, self.database.path)
        finally:
            self._close(window)

    def test_timestamp_name_suffix_and_cancel_create_nothing(self) -> None:
        moment = datetime(2026, 7, 30, 16, 25, 0)
        self.assertEqual(
            timestamped_database_name(now=moment),
            "metami_backup_20260730_162500.db",
        )
        self.assertEqual(
            with_database_suffix(self.root / "backup"),
            self.root / "backup.db",
        )
        window = MainWindow(self.database, settings=self.settings)
        before = set(self.root.rglob("*"))
        try:
            with (
                patch("ui.main_window.QMessageBox.information"),
                patch(
                    "ui.main_window.QFileDialog.getSaveFileName",
                    return_value=("", ""),
                ),
            ):
                window._backup_metami_data()
            self.assertEqual(before, set(self.root.rglob("*")))
        finally:
            self._close(window)

    def test_ui_backup_and_restore_cancel_are_safe(self) -> None:
        destination = self.root / "exports" / "ui-backup"
        destination.parent.mkdir()
        window = MainWindow(self.database, settings=self.settings)
        try:
            with (
                patch(
                    "ui.main_window.QFileDialog.getSaveFileName",
                    return_value=(str(destination), ""),
                ),
                patch(
                    "ui.main_window.QMessageBox.information"
                ) as information,
            ):
                window._backup_metami_data()
            saved = destination.with_suffix(".db")
            self.assertTrue(saved.is_file())
            intro = information.call_args_list[0].args[2]
            self.assertIn("タイトル、星評価、タグ、メモ", intro)
            self.assertIn("画像・動画ファイル本体", intro)

            before = self._snapshot(self.database.path)
            self.database.set_user_title(self.media, "キャンセル前タイトル")
            changed = self._snapshot(self.database.path)
            with (
                patch(
                    "ui.main_window.QFileDialog.getOpenFileName",
                    return_value=(str(saved), ""),
                ),
                patch.object(
                    window,
                    "_confirm_database_restore",
                    return_value=False,
                ),
            ):
                window._restore_metami_data()
            self.assertNotEqual(before, changed)
            self.assertEqual(changed, self._snapshot(self.database.path))
        finally:
            self._close(window)

    def test_ui_restore_refreshes_cards_and_keeps_selection(self) -> None:
        backup = self.root / "restore-source.db"
        backup_database(self.database.path, backup)
        self.database.set_user_title(self.media, "復元前に変更したタイトル")
        self.database.set_rating(self.media, 1)
        window = MainWindow(self.database, settings=self.settings)
        try:
            self.assertTrue(window._open_folder(self.media.parent))
            self.assertTrue(window.file_list_pane.select_path(self.media))
            with (
                patch(
                    "ui.main_window.QFileDialog.getOpenFileName",
                    return_value=(str(backup), ""),
                ),
                patch.object(
                    window,
                    "_confirm_database_restore",
                    return_value=True,
                ),
                patch("ui.main_window.QMessageBox.information"),
            ):
                window._restore_metami_data()
            self.assertEqual(window._current_path, self.media)
            card = window.file_list_pane._visible_card(self.media)
            self.assertIsNotNone(card)
            self.assertEqual(card._name_text, "バックアップ時タイトル")
            self.assertEqual(card.rating, 2)
            self.assertEqual(
                self.database.get_user_title(self.media),
                "バックアップ時タイトル",
            )
        finally:
            self._close(window)

    def test_backup_uses_sqlite_copy_and_preserves_source(self) -> None:
        destination = self.root / "exports" / "backup.db"
        destination.parent.mkdir()
        source_before = self._snapshot(self.database.path)
        saved = backup_database(self.database.path, destination)
        self.assertEqual(saved, destination.resolve())
        self.assertEqual(validate_metami_database(destination), "ok")
        self.assertEqual(source_before, self._snapshot(self.database.path))
        self.assertEqual(source_before, self._snapshot(destination))
        self.assertEqual(self.media.read_bytes(), b"test-media")

    def test_backup_rejects_same_path_and_failed_quick_check(self) -> None:
        with self.assertRaises(DataManagementError):
            backup_database(self.database.path, self.database.path)
        destination = self.root / "broken-backup.db"
        with patch.object(
            data_management,
            "_quick_check",
            return_value="database disk image is malformed",
        ):
            with self.assertRaises(DataManagementError):
                backup_database(self.database.path, destination)
        self.assertFalse(destination.exists())

    def test_restore_validation_rejects_invalid_inputs(self) -> None:
        missing = self.root / "missing.db"
        empty = self.root / "empty.db"
        empty.touch()
        text = self.root / "text.db"
        text.write_text("not sqlite", encoding="utf-8")
        unrelated = self.root / "unrelated.db"
        with closing(sqlite3.connect(unrelated)) as connection:
            connection.execute("CREATE TABLE other (id INTEGER)")
            connection.commit()
        for path in (missing, empty, text, unrelated):
            with self.subTest(path=path):
                with self.assertRaises(DataManagementError):
                    validate_metami_database(path)
        with self.assertRaises(DataManagementError):
            validate_restore_source(
                self.database.path, self.database.path
            )

    def test_restore_creates_before_copy_and_restores_data(self) -> None:
        backup = self.root / "exports" / "backup.db"
        backup.parent.mkdir()
        backup_database(self.database.path, backup)
        self.database.set_user_title(self.media, "変更後タイトル")
        self.database.set_rating(self.media, 1)
        self.database.set_memo(self.media, "変更後メモ")
        result = restore_database(
            backup,
            self.database.path,
            now=datetime(2026, 7, 30, 16, 30, 0),
        )
        self.assertTrue(result.before_restore_path.is_file())
        self.assertEqual(
            result.before_restore_path.name,
            "metami_before_restore_20260730_163000.db",
        )
        self.assertEqual(
            result.before_restore_path.parent,
            self.database.path.parent / "backups",
        )
        self.assertEqual(
            self.database.get_user_title(self.media), "バックアップ時タイトル"
        )
        self.assertEqual(
            self.database.get_file_ratings()[str(self.media.resolve())], 2
        )
        self.assertEqual(self.database.get_memo(self.media), "保存済みメモ")
        before_restore = MetadataDatabase(result.before_restore_path)
        self.assertEqual(
            before_restore.get_user_title(self.media), "変更後タイトル"
        )
        self.assertEqual(self.media.read_bytes(), b"test-media")

    def test_restore_failure_recovers_current_database(self) -> None:
        restore_source = self.root / "restore.db"
        backup_database(self.database.path, restore_source)
        self.database.set_user_title(self.media, "復元直前タイトル")
        real_backup = data_management._backup_into_database
        call_count = 0

        def fail_restore_once(source: Path, destination: Path) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise DataManagementError("テスト用復元失敗")
            real_backup(source, destination)

        with patch.object(
            data_management,
            "_backup_into_database",
            side_effect=fail_restore_once,
        ):
            with self.assertRaisesRegex(
                DataManagementError, "復元前のMETAMIデータへ戻しました"
            ):
                restore_database(restore_source, self.database.path)
        self.assertEqual(
            self.database.get_user_title(self.media), "復元直前タイトル"
        )
        self.assertEqual(validate_metami_database(self.database.path), "ok")

    def test_restore_stops_when_before_restore_backup_fails(self) -> None:
        restore_source = self.root / "restore.db"
        backup_database(self.database.path, restore_source)
        before = self._snapshot(self.database.path)
        with patch.object(
            data_management,
            "backup_database",
            side_effect=DataManagementError("退避不可"),
        ):
            with self.assertRaisesRegex(
                DataManagementError, "復元前のMETAMIデータを退避できません"
            ):
                restore_database(restore_source, self.database.path)
        self.assertEqual(before, self._snapshot(self.database.path))

    def test_database_info_counts_and_dialog_are_read_only(self) -> None:
        info = get_database_info(self.database.path)
        self.assertEqual(info.file_name, "metami.db")
        self.assertEqual(info.directory, str(self.database.path.parent.resolve()))
        self.assertGreater(info.size_bytes, 0)
        self.assertEqual(info.registered_files, 1)
        self.assertEqual(info.titled_files, 1)
        self.assertEqual(info.tagged_files, 1)
        self.assertEqual(info.memo_files, 1)
        self.assertEqual(info.rated_files, 1)
        self.assertTrue(info.integrity_ok)
        self.assertEqual(info.integrity_details, "ok")
        dialog = DatabaseInfoDialog(info)
        try:
            self.assertEqual(
                dialog.value_labels["整合性確認結果"].text(), "正常"
            )
            self.assertEqual(
                dialog.value_labels["登録ファイル数"].text(), "1件"
            )
        finally:
            dialog.close()

    def test_help_explains_scope_and_no_automatic_backup(self) -> None:
        text = DATA_MANAGEMENT_HELP_PATH.read_text(encoding="utf-8")
        for expected in (
            "タイトル",
            "星評価",
            "タグ",
            "メモ",
            "画像・動画ファイル本体はバックアップ対象に含まれません",
            "復元",
            "自動退避",
            "データ保存場所",
            "強制終了しないでください",
            "自動バックアップ、定期バックアップ",
        ):
            self.assertIn(expected, text)

    def test_open_data_location_uses_database_parent(self) -> None:
        window = MainWindow(self.database, settings=self.settings)
        try:
            with patch(
                "ui.main_window.QDesktopServices.openUrl", return_value=True
            ) as open_url:
                window._open_data_location()
            url = open_url.call_args.args[0]
            self.assertEqual(
                Path(url.toLocalFile()), self.database.path.parent.resolve()
            )
        finally:
            self._close(window)

    @staticmethod
    def _snapshot(path: Path) -> tuple[tuple[str, tuple], ...]:
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


if __name__ == "__main__":
    unittest.main()
