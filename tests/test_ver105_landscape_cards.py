"""Ver1.0.5候補のカード表示形式切り替えを確認する。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QListWidget, QWidget


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from storage.database import MetadataDatabase
from ui.file_list_pane import (
    LANDSCAPE_VIEW,
    THUMBNAIL_VIEW,
    FileCard,
    LandscapeFileCard,
)
from ui.main_window import MainWindow
from ui.styles import LANDSCAPE_CARD_HEIGHT, LANDSCAPE_THUMBNAIL_SIZE


class Ver105LandscapeCardTests(unittest.TestCase):
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
        self.database = MetadataDatabase(self.root / "test.db")
        self.database.initialize()
        self.media = self.root / "media"
        self.first = self.media / "first.png"
        self.second = self.media / "child" / "second.png"
        self._image(self.first, 160, 90)
        self._image(self.second, 90, 160)
        self.database.register_files([self.first, self.second])
        self.database.set_rating(self.first, 2)
        self.database.add_tag(self.first, "風景")
        self.database.add_tag(self.first, "長いタグ名" * 8)
        self.database.set_memo(self.first, "長いメモです。" * 30)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _image(path: Path, width: int, height: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(0xFF6688AA)
        if not image.save(str(path)):
            raise AssertionError(f"画像を作成できません: {path}")

    @staticmethod
    def _close(window: MainWindow) -> None:
        window.file_list_pane.release_media()
        window.preview_pane.release_media()
        window.close()

    @staticmethod
    def _cards(window: MainWindow) -> list[QWidget]:
        pane = window.file_list_pane
        return [
            pane.list_widget.itemWidget(pane.list_widget.item(index))
            for index in range(pane.list_widget.count())
        ]

    def test_menu_is_exclusive_and_initial_mode_is_thumbnail(self) -> None:
        window = MainWindow(None, settings=self.settings)
        try:
            actions = window.card_view_menu.actions()
            self.assertEqual(
                [action.text() for action in actions],
                ["サムネイルカード", "ランドスケープカード"],
            )
            self.assertTrue(window.card_view_group.isExclusive())
            self.assertTrue(window.thumbnail_card_action.isChecked())
            self.assertFalse(window.landscape_card_action.isChecked())
            self.assertEqual(
                window.file_list_pane.view_mode(), THUMBNAIL_VIEW
            )
        finally:
            self._close(window)

    def test_switch_preserves_data_selection_and_uses_cached_thumbnail(
        self,
    ) -> None:
        window = MainWindow(self.database, settings=self.settings)
        try:
            window.include_subfolders_action.setChecked(True)
            self.assertTrue(window._open_folder(self.media))
            window.file_list_pane.select_path(self.first)
            QApplication.processEvents()
            paths = window.file_list_pane.all_paths()
            history = list(window._recent_folders)
            current_path = window._current_path
            with patch.object(
                window.file_list_pane,
                "_image_thumbnail",
                wraps=window.file_list_pane._image_thumbnail,
            ) as thumbnail_reader:
                window.landscape_card_action.trigger()
                self.assertEqual(thumbnail_reader.call_count, 0)

            self.assertEqual(
                window.file_list_pane.view_mode(), LANDSCAPE_VIEW
            )
            self.assertEqual(window.file_list_pane.all_paths(), paths)
            self.assertEqual(window._current_path, current_path)
            self.assertEqual(window._recent_folders, history)
            self.assertTrue(window.include_subfolders_action.isChecked())
            selected = window.file_list_pane.list_widget.currentItem()
            self.assertEqual(
                Path(selected.data(Qt.ItemDataRole.UserRole)), self.first
            )
            self.assertTrue(
                all(
                    isinstance(card, LandscapeFileCard)
                    for card in self._cards(window)
                )
            )

            window.thumbnail_card_action.trigger()
            self.assertEqual(
                window.file_list_pane.view_mode(), THUMBNAIL_VIEW
            )
            self.assertTrue(
                all(isinstance(card, FileCard) for card in self._cards(window))
            )
            self.assertEqual(window._current_path, self.first)
            thumbnail_card = window.file_list_pane._visible_card(self.first)
            self.assertIsInstance(thumbnail_card, FileCard)
            assert isinstance(thumbnail_card, FileCard)
            self.assertIsNotNone(thumbnail_card.graphicsEffect())
            self.assertEqual(
                window.file_list_pane.list_widget.spacing(), 7
            )
        finally:
            self._close(window)

    def test_landscape_layout_content_elision_and_rating(self) -> None:
        window = MainWindow(self.database, settings=self.settings)
        try:
            window.resize(1200, 760)
            window.show()
            window.include_subfolders_action.setChecked(True)
            self.assertTrue(window._open_folder(self.media))
            window.landscape_card_action.trigger()
            QApplication.processEvents()
            pane = window.file_list_pane
            self.assertEqual(
                pane.list_widget.viewMode(), QListWidget.ViewMode.ListMode
            )
            self.assertFalse(pane.list_widget.isWrapping())
            self.assertEqual(
                pane.list_widget.horizontalScrollBarPolicy(),
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )
            self.assertEqual(pane.list_widget.spacing(), 4)
            first_card = pane._visible_card(self.first)
            self.assertIsInstance(first_card, LandscapeFileCard)
            assert isinstance(first_card, LandscapeFileCard)
            self.assertIsNone(first_card.graphicsEffect())
            self.assertEqual(first_card.height(), LANDSCAPE_CARD_HEIGHT)
            self.assertEqual(
                pane.list_widget.item(0).sizeHint().height(),
                LANDSCAPE_CARD_HEIGHT + 2,
            )
            self.assertEqual(
                (first_card.thumbnail.width(), first_card.thumbnail.height()),
                LANDSCAPE_THUMBNAIL_SIZE,
            )
            self.assertFalse(first_card.thumbnail.pixmap().isNull())
            self.assertEqual(first_card.favorite_button.text(), "★★☆")
            self.assertEqual(first_card.format_badge.text(), "PNG")
            self.assertEqual(
                first_card.format_badge.toolTip(), "PNGファイル"
            )
            self.assertIn("×", first_card.analysis_label.text())
            self.assertNotIn("Seed", first_card.analysis_label.text())
            self.assertFalse(hasattr(first_card, "path_label"))
            self.assertEqual(first_card.tags_heading.text(), "タグ")
            self.assertEqual(first_card.memo_heading.text(), "メモ")
            self.assertTrue(first_card.tags_heading.font().bold())
            self.assertTrue(first_card.memo_heading.font().bold())
            self.assertEqual(
                first_card.analysis_label.geometry().y(),
                first_card.tags_heading.geometry().y(),
            )
            self.assertIn("風景", first_card.tags_label.toolTip())
            self.assertIn("…", first_card.tags_label.text())
            self.assertIn("長いメモ", first_card.memo_label.toolTip())
            self.assertLessEqual(
                first_card.memo_label.text().count("\n") + 1,
                2,
            )
            self.assertIn("…", first_card.memo_label.text())
            self.assertGreaterEqual(
                first_card.memo_label.minimumHeight(),
                first_card.memo_label.fontMetrics().lineSpacing() * 2 + 8,
            )
            self.assertEqual(first_card.name_label.toolTip(), str(self.first))
            first_card.favorite_button.click()
            self.assertEqual(pane.get_rating(self.first), 3)
            self.assertTrue(first_card.property("selected"))

            widths = [
                card.width()
                for card in self._cards(window)
                if isinstance(card, LandscapeFileCard)
            ]
            self.assertTrue(all(width > 240 for width in widths))
            self.assertLessEqual(
                max(widths), pane.list_widget.viewport().width()
            )
        finally:
            self._close(window)

    def test_same_names_are_distinguished_by_full_path_tooltips(self) -> None:
        other = self.media / "other" / "second.png"
        self._image(other, 160, 90)
        window = MainWindow(None, settings=self.settings)
        try:
            window.include_subfolders_action.setChecked(True)
            self.assertTrue(window._open_folder(self.media))
            window.landscape_card_action.trigger()
            cards = {
                card.path: card
                for card in self._cards(window)
                if isinstance(card, LandscapeFileCard)
            }
            self.assertEqual(
                cards[self.second].name_label.toolTip(), str(self.second)
            )
            self.assertEqual(cards[other].name_label.toolTip(), str(other))
        finally:
            self._close(window)

    def test_empty_tags_and_memo_show_none(self) -> None:
        window = MainWindow(None, settings=self.settings)
        try:
            self.assertTrue(window._open_folder(self.media))
            window.landscape_card_action.trigger()
            card = window.file_list_pane._visible_card(self.first)
            self.assertIsInstance(card, LandscapeFileCard)
            assert isinstance(card, LandscapeFileCard)
            self.assertEqual(card.tags_label.text(), "なし")
            self.assertEqual(card.memo_label.text(), "なし")
        finally:
            self._close(window)

    def test_mode_persists_and_layout_reset_restores_thumbnail(self) -> None:
        window = MainWindow(None, settings=self.settings)
        try:
            window.landscape_card_action.trigger()
            self.assertEqual(
                self.settings.value("display/cardViewMode"), LANDSCAPE_VIEW
            )
        finally:
            self._close(window)

        reloaded_settings = QSettings(
            str(self.settings_path), QSettings.Format.IniFormat
        )
        reloaded = MainWindow(None, settings=reloaded_settings)
        try:
            self.assertTrue(reloaded.landscape_card_action.isChecked())
            self.assertEqual(
                reloaded.file_list_pane.view_mode(), LANDSCAPE_VIEW
            )
            reloaded._reset_display_layout()
            self.assertTrue(reloaded.thumbnail_card_action.isChecked())
            self.assertEqual(
                reloaded.file_list_pane.view_mode(), THUMBNAIL_VIEW
            )
        finally:
            self._close(reloaded)


if __name__ == "__main__":
    unittest.main()
