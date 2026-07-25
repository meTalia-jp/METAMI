"""Ver0.8.9 Ver0.8系統合確認の回帰試験。"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from metadata.display_service import DisplayDataError, build_display_data  # noqa: E402
from storage.database import DatabaseError, MetadataDatabase  # noqa: E402
from ui.file_list_pane import FileCard, FileListPane  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.metadata_pane import MemoEditor, MemoTab  # noqa: E402
from ui.preview_pane import PreviewPane  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Ver089IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _image(
        self, name: str, width: int, height: int, image_format: str
    ) -> Path:
        path = self.root / name
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(0xFF8C72C9)
        self.assertTrue(image.save(str(path), image_format))
        return path

    def test_png_and_webp_basic_info_and_preview(self) -> None:
        paths = (
            self._image("landscape.png", 320, 180, "PNG"),
            self._image("portrait.webp", 180, 320, "WEBP"),
        )
        preview = PreviewPane()
        try:
            for path in paths:
                before = _sha256(path)
                data = build_display_data(path)
                basic = dict(data.basic_info)
                self.assertEqual(basic["ファイル名"], path.name)
                self.assertEqual(basic["パス"], str(path.resolve()))
                self.assertEqual(basic["形式"], path.suffix[1:].upper())
                self.assertIn("ピクセル", basic["解像度"])
                self.assertEqual(data.status, "メタデータなし")

                preview.show_file(path)
                self.assertEqual(preview.stack.currentIndex(), 1)
                self.assertFalse(preview._image_pixmap.isNull())
                self.assertEqual(_sha256(path), before)
        finally:
            preview.release_media()
            preview.close()

    def test_all_requested_aspect_ratios_use_width_height_rule(self) -> None:
        cases = (
            (160, 90, "landscape"),   # 16:9
            (90, 160, "portrait"),    # 9:16
            (100, 100, "square"),     # 1:1
            (160, 120, "landscape"),  # 4:3
            (120, 160, "portrait"),   # 3:4
            (150, 100, "landscape"),  # 3:2
            (100, 150, "portrait"),   # 2:3
            (211, 100, "landscape"),  # その他の横長
            (100, 211, "portrait"),   # その他の縦長
        )
        placeholder = self.root / "aspect.png"
        for width, height, expected in cases:
            with self.subTest(width=width, height=height):
                self.assertEqual(
                    FileCard._classify_aspect(width, height), expected
                )
                card = FileCard(placeholder)
                try:
                    pixmap = QPixmap(width, height)
                    pixmap.fill(Qt.GlobalColor.white)
                    card.set_thumbnail(pixmap)
                    self.assertEqual(card._portrait_mode, expected == "portrait")
                    displayed = card.thumbnail.pixmap()
                    self.assertFalse(displayed.isNull())
                    self.assertLessEqual(displayed.width(), card.thumbnail.width())
                    self.assertLessEqual(displayed.height(), card.thumbnail.height())
                finally:
                    card.close()

    def test_rating_tags_and_memo_survive_database_reopen(self) -> None:
        source = self._image("persist.png", 64, 64, "PNG")
        original_hash = _sha256(source)
        database_path = self.root / "metami.db"
        first = MetadataDatabase(database_path)
        first.initialize()
        first.register_files([source])
        first.set_rating(source, 3)
        first.add_tag(source, "人物")
        first.add_tag(source, "再利用候補")
        first.set_memo(source, "日本語メモ\n次回も使用する")

        reopened = MetadataDatabase(database_path)
        reopened.initialize()
        key = str(source.resolve())
        self.assertEqual(reopened.get_file_ratings()[key], 3)
        self.assertEqual(
            reopened.get_file_tags(source), ["人物", "再利用候補"]
        )
        self.assertEqual(
            reopened.get_memo(source), "日本語メモ\n次回も使用する"
        )
        self.assertFalse(reopened.get_file_missing_states()[key])
        self.assertEqual(_sha256(source), original_hash)

    def test_memo_placeholder_hides_as_soon_as_editor_gets_focus(self) -> None:
        memo = MemoTab()
        memo.set_memo("", True)
        self.assertIsInstance(memo.editor, MemoEditor)
        self.assertEqual(
            memo.editor.placeholderText(), MemoEditor.PLACEHOLDER
        )
        memo.show()
        QApplication.processEvents()
        try:
            memo.editor.setFocus()
            QTest.qWait(20)
            self.assertEqual(memo.editor.placeholderText(), "")

            memo.editor.clearFocus()
            QTest.qWait(20)
            self.assertEqual(
                memo.editor.placeholderText(), MemoEditor.PLACEHOLDER
            )

            memo.editor.setFocus()
            QTest.keyClicks(memo.editor, "memo")
            memo.editor.clearFocus()
            QTest.qWait(20)
            self.assertEqual(memo.editor.toPlainText(), "memo")
            self.assertEqual(memo.editor.placeholderText(), "")
        finally:
            memo.close()

    def test_long_values_keep_card_size_and_use_elision(self) -> None:
        source = self._image(
            "非常に長いファイル名_" + "a" * 120 + ".png",
            320,
            180,
            "PNG",
        )
        pane = FileListPane()
        long_tag = "非常に長いタグ名" * 10
        long_memo = "長いメモです。" * 80
        try:
            pane.resize(700, 700)
            pane.show()
            pane.set_paths(
                [source],
                tags_by_path={str(source.resolve()): [long_tag, "人物", "夜景"]},
                memos_by_path={str(source.resolve()): long_memo},
            )
            card = pane._visible_card(source)
            self.assertIsNotNone(card)
            assert card is not None
            fixed_size = card.size()
            QApplication.processEvents()
            self.assertEqual(card.size(), fixed_size)
            self.assertEqual(card.name_label.toolTip(), str(source))
            self.assertEqual(card.memo_excerpt.toolTip(), long_memo)
            self.assertNotIn("\n", card.memo_excerpt.text())
            self.assertTrue(
                "…" in card.name_label.text()
                or len(card.name_label.text()) < len(source.name)
            )
        finally:
            pane.release_media()
            pane.close()

    def test_invalid_supported_extension_is_reported_without_crash(self) -> None:
        for suffix in (".png", ".webp", ".jpg", ".jpeg", ".mp4"):
            with self.subTest(suffix=suffix):
                invalid = self.root / f"broken{suffix}"
                invalid.write_bytes(b"invalid media data")
                before = _sha256(invalid)
                with self.assertRaises(DisplayDataError):
                    build_display_data(invalid)
                self.assertEqual(_sha256(invalid), before)

    def test_search_and_existing_filters_remain_composable(self) -> None:
        png = self._image("night_scene.png", 160, 90, "PNG")
        webp = self._image("portrait.webp", 90, 160, "WEBP")
        mp4 = self.root / "motion.mp4"
        mp4.write_bytes(b"invalid media data")
        pane = FileListPane()
        try:
            pane.set_paths(
                [png, webp, mp4],
                ratings={str(png.resolve()).casefold(): 3},
                tags_by_path={
                    str(png.resolve()).casefold(): ["夜景"],
                    str(webp.resolve()).casefold(): ["人物"],
                },
                all_tags=["夜景", "人物"],
                tag_usage_counts={"夜景": 1, "人物": 1},
            )
            self.assertEqual(pane.list_widget.count(), 3)

            pane.format_combo.setCurrentIndex(
                pane.format_combo.findData("webp")
            )
            pane._apply_filters()
            self.assertEqual(pane.list_widget.count(), 1)
            self.assertEqual(
                Path(
                    pane.list_widget.item(0).data(Qt.ItemDataRole.UserRole)
                ).name,
                "portrait.webp",
            )

            pane.reset_filters()
            pane.search_edit.setText("night")
            pane._apply_filters()
            self.assertEqual(pane.list_widget.count(), 1)

            pane.search_edit.clear()
            pane.rating_filter.setCurrentIndex(
                pane.rating_filter.findData(3)
            )
            pane._apply_filters()
            self.assertEqual(pane.list_widget.count(), 1)

            pane.reset_filters()
            pane._selected_tag = "人物"
            pane.tag_filter.setEditText("人物")
            pane._apply_filters()
            self.assertEqual(pane.list_widget.count(), 1)
            self.assertEqual(
                Path(
                    pane.list_widget.item(0).data(Qt.ItemDataRole.UserRole)
                ).name,
                "portrait.webp",
            )

            pane.reset_filters()
            self.assertEqual(pane.list_widget.count(), 3)
        finally:
            pane.release_media()
            pane.close()

    def test_new_and_existing_database_show_online(self) -> None:
        database_path = self.root / "metami.db"
        database = MetadataDatabase(database_path)
        database.initialize()
        first = MainWindow(database)
        try:
            self.assertEqual(first.console_status.text(), "DB ● Online")
        finally:
            first.file_list_pane.release_media()
            first.preview_pane.release_media()
            first.close()

        reopened = MetadataDatabase(database_path)
        reopened.initialize()
        second = MainWindow(reopened)
        try:
            self.assertEqual(second.console_status.text(), "DB ● Online")
        finally:
            second.file_list_pane.release_media()
            second.preview_pane.release_media()
            second.close()

    def test_database_failure_has_error_and_offline_states(self) -> None:
        directory_as_database = self.root / "not-a-database"
        directory_as_database.mkdir()
        broken = MetadataDatabase(directory_as_database)
        with self.assertRaises(DatabaseError):
            broken.initialize()

        error_window = MainWindow(None, "データベースを開けません")
        try:
            self.assertEqual(error_window.console_status.text(), "DB ● Error")
        finally:
            error_window.file_list_pane.release_media()
            error_window.preview_pane.release_media()
            error_window.close()

        offline_window = MainWindow(None)
        try:
            self.assertEqual(
                offline_window.console_status.text(), "DB ○ Offline"
            )
        finally:
            offline_window.file_list_pane.release_media()
            offline_window.preview_pane.release_media()
            offline_window.close()

    def test_minimum_and_full_hd_layout_keep_core_controls_available(self) -> None:
        window = MainWindow(None)
        try:
            for width, height in ((1000, 650), (1280, 800), (1920, 1080)):
                with self.subTest(size=(width, height)):
                    window.resize(width, height)
                    window.show()
                    QApplication.processEvents()
                    self.assertGreater(window.file_list_pane.width(), 0)
                    self.assertGreater(window.preview_pane.height(), 0)
                    self.assertGreater(window.metadata_pane.height(), 0)
                    self.assertTrue(window.file_list_pane.search_edit.isVisible())
                    self.assertTrue(window.metadata_pane.tabs.isVisible())
                    self.assertTrue(window.rating_appeal.isVisible())
        finally:
            window.file_list_pane.release_media()
            window.preview_pane.release_media()
            window.close()


if __name__ == "__main__":
    unittest.main()
