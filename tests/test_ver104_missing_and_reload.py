"""Ver1.0.4候補の再読み込みと読み取り専用missing確認。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from metadata.file_presence import check_registered_file
from storage.database import MetadataDatabase, RegisteredFileRecord
from ui.main_window import MainWindow
from ui.missing_items_dialog import MissingItemsDialog


class Ver104Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = QSettings(
            str(self.root / "settings.ini"), QSettings.Format.IniFormat
        )
        self.database = MetadataDatabase(self.root / "test.db")
        self.database.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _image(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(12, 8, QImage.Format.Format_ARGB32)
        image.fill(0xFF7799BB)
        if not image.save(str(path)):
            raise AssertionError(f"画像を作成できません: {path}")

    @staticmethod
    def _close(window: MainWindow) -> None:
        window.file_list_pane.release_media()
        window.preview_pane.release_media()
        window.close()

    @staticmethod
    def _wait_dialog(dialog: MissingItemsDialog) -> None:
        deadline = time.monotonic() + 5
        while dialog._thread is not None and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        QApplication.processEvents()
        if dialog._thread is not None:
            raise AssertionError("存在確認処理が時間内に完了しませんでした")

    def _database_snapshot(self) -> tuple[tuple[str, tuple], ...]:
        result: list[tuple[str, tuple]] = []
        connection = sqlite3.connect(self.database.path)
        try:
            for table in ("files", "file_paths", "user_info", "tags", "file_tags"):
                rows = tuple(connection.execute(f"SELECT * FROM {table}"))
                result.append((table, rows))
        finally:
            connection.close()
        return tuple(result)

    def test_reload_action_button_and_f5_share_one_action(self) -> None:
        window = MainWindow(None, settings=self.settings)
        try:
            window.resize(1000, 650)
            window.show()
            QApplication.processEvents()
            self.assertFalse(window.reload_folder_action.isEnabled())
            self.assertFalse(window.file_list_pane.reload_folder_button.isEnabled())
            self.assertIsNone(
                window.file_list_pane.reload_folder_button.defaultAction()
            )
            self.assertEqual(
                window.file_list_pane.reload_folder_button.text(), "再読込み"
            )
            self.assertEqual(
                window.reload_folder_action.shortcut().toString(), "F5"
            )
            self.assertEqual(
                window.file_list_pane.reload_folder_button.toolTip(),
                "現在開いているフォルダの内容を再読み込みします",
            )
            buttons = (
                window.file_list_pane.open_file_button,
                window.file_list_pane.open_folder_button,
                window.file_list_pane.reload_folder_button,
            )
            self.assertTrue(
                all(button.minimumWidth() >= 142 for button in buttons)
            )
            self.assertEqual([button.x() for button in buttons], [buttons[0].x()] * 3)
            self.assertLess(buttons[0].y(), buttons[1].y())
            self.assertLess(buttons[1].y(), buttons[2].y())
            self.assertEqual(
                window.file_list_pane.search_edit.maximumWidth(), 230
            )
            folder = self.root / "media"
            source = folder / "first.png"
            self._image(source)
            self.assertTrue(window._open_folder(folder))
            self.assertTrue(window.reload_folder_action.isEnabled())
            self.assertTrue(window.file_list_pane.reload_folder_button.isEnabled())
            self.assertEqual(
                window.file_list_pane.reload_folder_button.text(), "再読込み"
            )
        finally:
            self._close(window)

    def test_reload_reflects_changes_preserves_selection_and_history(self) -> None:
        folder = self.root / "media"
        first = folder / "first.png"
        second = folder / "second.png"
        self._image(first)
        window = MainWindow(self.database, settings=self.settings)
        try:
            self.assertTrue(window.missing_items_action.isEnabled())
            self.assertTrue(window._open_folder(folder))
            window.file_list_pane.select_path(first)
            history = list(window._recent_folders)
            self._image(second)
            window.reload_folder_action.trigger()
            self.assertEqual(
                set(window.file_list_pane.all_paths()), {first, second}
            )
            self.assertEqual(window._current_path, first)
            self.assertEqual(window._recent_folders, history)

            first.unlink()
            window.file_list_pane.reload_folder_button.click()
            self.assertEqual(window.file_list_pane.all_paths(), [second])
            self.assertIsNone(window._current_path)
            self.assertEqual(window._recent_folders, history)
        finally:
            self._close(window)

    def test_reload_uses_subfolder_setting_and_handles_missing_folder(self) -> None:
        folder = self.root / "media"
        direct = folder / "direct.png"
        nested = folder / "child" / "nested.png"
        self._image(direct)
        self._image(nested)
        window = MainWindow(None, settings=self.settings)
        moved = self.root / "moved"
        try:
            self.assertTrue(window._open_folder(folder))
            window.reload_folder_action.trigger()
            self.assertEqual(window.file_list_pane.all_paths(), [direct])
            window.include_subfolders_action.setChecked(True)
            self.assertEqual(
                set(window.file_list_pane.all_paths()), {direct, nested}
            )
            history = list(window._recent_folders)
            folder.rename(moved)
            window.reload_folder_action.trigger()
            self.assertFalse(window.reload_folder_action.isEnabled())
            self.assertEqual(window._recent_folders, history)
            self.assertIn(
                "現在のフォルダが見つかりません",
                window.statusBar().currentMessage(),
            )
        finally:
            self._close(window)

    def test_presence_classification_handles_missing_empty_and_directory(self) -> None:
        def record(file_id: int, path: str) -> RegisteredFileRecord:
            return RegisteredFileRecord(
                file_id, path, Path(path).name, "png", 0, "", 0, (), ""
            )

        missing = check_registered_file(record(1, str(self.root / "none.png")))
        empty = check_registered_file(record(2, ""))
        directory = check_registered_file(record(3, str(self.root)))
        self.assertEqual(missing.status, "見つかりません")
        self.assertEqual(empty.status, "確認できません")
        self.assertEqual(directory.status, "確認できません")

    def test_missing_dialog_is_read_only_and_recheck_removes_restored_file(
        self,
    ) -> None:
        existing = self.root / "existing.png"
        missing = self.root / "missing.png"
        self._image(existing)
        self._image(missing)
        self.database.register_files([existing, missing])
        self.database.set_rating(missing, 2)
        self.database.add_tag(missing, "確認用")
        self.database.set_memo(missing, "保存済みメモ")
        missing.unlink()
        before = self._database_snapshot()

        dialog = MissingItemsDialog(self.database)
        try:
            self._wait_dialog(dialog)
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertEqual(dialog.table.item(0, 1).text(), "missing.png")
            self.assertEqual(dialog.table.item(0, 2).text(), str(missing.resolve()))
            self.assertEqual(dialog.table.item(0, 3).text(), "画像")
            dialog.table.selectRow(0)
            QApplication.processEvents()
            self.assertIn("確認用", dialog.details.toPlainText())
            self.assertIn("保存済みメモ", dialog.details.toPlainText())
            self.assertEqual(before, self._database_snapshot())

            self._image(missing)
            dialog.start_check()
            self._wait_dialog(dialog)
            self.assertEqual(dialog.table.rowCount(), 0)
            self.assertEqual(
                dialog.result_label.text(), "見つからない項目はありません。"
            )
            self.assertEqual(before, self._database_snapshot())
        finally:
            dialog.close()

    def test_same_name_missing_records_remain_distinct(self) -> None:
        first = self.root / "A" / "same.png"
        second = self.root / "B" / "same.png"
        self._image(first)
        self._image(second)
        self.database.register_files([first, second])
        first.unlink()
        second.unlink()
        dialog = MissingItemsDialog(self.database)
        try:
            self._wait_dialog(dialog)
            self.assertEqual(dialog.table.rowCount(), 2)
            paths = {
                dialog.table.item(row, 2).text()
                for row in range(dialog.table.rowCount())
            }
            self.assertEqual(paths, {str(first.resolve()), str(second.resolve())})
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
