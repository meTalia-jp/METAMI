"""最近開いたフォルダと再帰メディア探索の試験。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from metadata.media_finder import find_media_files
from ui.file_list_pane import FileCard
from ui.main_window import MainWindow


class FolderBrowsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings_path = self.root / "settings.ini"
        self.settings = QSettings(
            str(self.settings_path), QSettings.Format.IniFormat
        )
        self.settings.clear()
        self.settings.sync()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _close(window: MainWindow) -> None:
        window.file_list_pane.release_media()
        window.preview_pane.release_media()
        window.close()

    @staticmethod
    def _make_image(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(16, 12, QImage.Format.Format_ARGB32)
        image.fill(0xFF557799)
        if not image.save(str(path)):
            raise AssertionError(f"画像を作成できません: {path}")

    def test_media_finder_direct_and_recursive(self) -> None:
        direct = self.root / "direct.png"
        nested = self.root / "B" / "nested.JPG"
        ignored = self.root / "B" / "memo.txt"
        self._make_image(direct)
        self._make_image(nested)
        ignored.write_text("not media", encoding="utf-8")

        direct_result = find_media_files(self.root)
        recursive_result = find_media_files(
            self.root, include_subfolders=True
        )

        self.assertEqual(list(direct_result.paths), [direct])
        self.assertEqual(set(recursive_result.paths), {direct, nested})
        self.assertNotIn(ignored, recursive_result.paths)

    def test_media_finder_does_not_follow_directory_symlink(self) -> None:
        outside = self.root / "outside"
        linked_file = outside / "linked.png"
        self._make_image(linked_file)
        link = self.root / "link"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError:
            self.skipTest("この環境ではディレクトリリンクを作成できません")

        result = find_media_files(self.root, include_subfolders=True)
        self.assertNotIn(link / "linked.png", result.paths)

    def test_recent_folders_are_unique_limited_and_persistent(self) -> None:
        folders = [self.root / f"folder-{index}" for index in range(6)]
        for folder in folders:
            folder.mkdir()
        window = MainWindow(None, settings=self.settings)
        try:
            for folder in folders:
                self.assertTrue(window._open_folder(folder))
            self.assertEqual(len(window._recent_folders), 5)
            self.assertEqual(Path(window._recent_folders[0]), folders[-1])
            self.assertTrue(window._open_folder(folders[2]))
            self.assertEqual(Path(window._recent_folders[0]), folders[2])
            self.assertEqual(len(window._recent_folders), 5)
        finally:
            self._close(window)

        reloaded_settings = QSettings(
            str(self.settings_path), QSettings.Format.IniFormat
        )
        reloaded = MainWindow(None, settings=reloaded_settings)
        try:
            self.assertEqual(Path(reloaded._recent_folders[0]), folders[2])
            self.assertEqual(len(reloaded._recent_folders), 5)
        finally:
            self._close(reloaded)

    def test_missing_recent_folder_is_kept_and_clear_is_explicit(self) -> None:
        missing = self.root / "missing"
        self.settings.setValue("folders/recent", [str(missing)])
        self.settings.sync()
        window = MainWindow(None, settings=self.settings)
        try:
            self.assertFalse(window._open_folder(missing))
            self.assertEqual(window._recent_folders, [str(missing)])
            self.assertIn(
                "履歴からは削除していません",
                window.statusBar().currentMessage(),
            )
            window._clear_recent_folders()
            self.assertEqual(window._recent_folders, [])
            actions = window.recent_folders_menu.actions()
            self.assertEqual(actions[0].text(), "履歴はありません")
            self.assertFalse(actions[0].isEnabled())
        finally:
            self._close(window)

    def test_recent_menu_opens_folder_through_common_loader(self) -> None:
        folder = self.root / "recent"
        image = folder / "from-history.png"
        self._make_image(image)
        self.settings.setValue("folders/recent", [str(folder)])
        self.settings.sync()
        window = MainWindow(None, settings=self.settings)
        try:
            window.recent_folders_menu.actions()[0].trigger()
            self.assertEqual(window.file_list_pane.all_paths(), [image])
            self.assertEqual(window._current_folder, folder)
        finally:
            self._close(window)

    def test_subfolder_setting_reloads_current_folder_and_persists(self) -> None:
        direct = self.root / "direct.png"
        nested = self.root / "child" / "nested.png"
        self._make_image(direct)
        self._make_image(nested)
        window = MainWindow(None, settings=self.settings)
        try:
            self.assertTrue(window._open_folder(self.root))
            self.assertEqual(window.file_list_pane.all_paths(), [direct])
            window.include_subfolders_action.setChecked(True)
            self.assertEqual(
                set(window.file_list_pane.all_paths()), {direct, nested}
            )
        finally:
            self._close(window)

        reloaded_settings = QSettings(
            str(self.settings_path), QSettings.Format.IniFormat
        )
        reloaded = MainWindow(None, settings=reloaded_settings)
        try:
            self.assertTrue(reloaded.include_subfolders_action.isChecked())
            self.assertIn(
                reloaded.include_subfolders_action,
                reloaded.file_menu.actions(),
            )
            self.assertNotIn(
                reloaded.include_subfolders_action,
                reloaded.view_menu.actions(),
            )
            self.assertEqual(
                reloaded.include_subfolders_action.text(),
                "サブフォルダも含む",
            )
        finally:
            self._close(reloaded)

    def test_duplicate_names_show_relative_path_and_keep_full_paths(self) -> None:
        first = self.root / "A" / "image001.png"
        second = self.root / "B" / "image001.png"
        self._make_image(first)
        self._make_image(second)
        window = MainWindow(None, settings=self.settings)
        try:
            window.include_subfolders_action.setChecked(True)
            self.assertTrue(window._open_folder(self.root))
            self.assertEqual(
                set(window.file_list_pane.all_paths()), {first, second}
            )
            tooltips = {
                window.file_list_pane.list_widget.item(index).toolTip()
                for index in range(
                    window.file_list_pane.list_widget.count()
                )
            }
            self.assertEqual(tooltips, {str(first), str(second)})
            details = []
            for index in range(window.file_list_pane.list_widget.count()):
                item = window.file_list_pane.list_widget.item(index)
                card = window.file_list_pane.list_widget.itemWidget(item)
                self.assertIsInstance(card, FileCard)
                details.append(card.details.text())
            self.assertTrue(any("A" in text for text in details))
            self.assertTrue(any("B" in text for text in details))
        finally:
            self._close(window)

    def test_unreadable_subfolder_is_reported_without_stopping(self) -> None:
        visible = self.root / "visible.png"
        blocked = self.root / "blocked"
        self._make_image(visible)
        blocked.mkdir()
        original_scandir = os.scandir

        def selective_scandir(path):
            if Path(path) == blocked:
                raise PermissionError("blocked")
            return original_scandir(path)

        with patch(
            "metadata.media_finder.os.scandir", side_effect=selective_scandir
        ):
            result = find_media_files(self.root, include_subfolders=True)
        self.assertEqual(list(result.paths), [visible])
        self.assertEqual(result.unreadable_directories, (str(blocked),))


if __name__ == "__main__":
    unittest.main()
