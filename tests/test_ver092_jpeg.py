"""Ver0.9.2 JPG/JPEG対応と初期分割位置の回帰試験。"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from metadata.display_service import build_display_data  # noqa: E402
from storage.database import MetadataDatabase  # noqa: E402
from ui.file_list_pane import FileCard, FileListPane  # noqa: E402
from ui.main_window import MainWindow, SUPPORTED_SUFFIXES  # noqa: E402
from ui.preview_pane import PreviewPane  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Ver092JpegTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _image(
        self,
        name: str,
        width: int = 160,
        height: int = 90,
        image_format: str = "JPEG",
    ) -> Path:
        path = self.root / name
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(0xFF8C72C9)
        self.assertTrue(image.save(str(path), image_format))
        return path

    def _oriented_jpeg(self, name: str = "oriented.jpg") -> Path:
        path = self._image(name, 120, 60)
        jpeg = path.read_bytes()
        # EXIF Orientation=6（90度回転）を持つ最小APP1セグメント。
        tiff = (
            b"II"
            + struct.pack("<H", 42)
            + struct.pack("<I", 8)
            + struct.pack("<H", 1)
            + struct.pack("<HHI", 0x0112, 3, 1)
            + struct.pack("<H", 6)
            + b"\0\0"
            + struct.pack("<I", 0)
        )
        payload = b"Exif\0\0" + tiff
        app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
        path.write_bytes(jpeg[:2] + app1 + jpeg[2:])
        return path

    def test_supported_suffixes_are_case_insensitive(self) -> None:
        for name in ("sample.jpg", "sample.jpeg", "sample.JPG", "sample.JPEG"):
            with self.subTest(name=name):
                self.assertIn(Path(name).suffix.casefold(), SUPPORTED_SUFFIXES)

    def test_jpeg_only_and_mixed_folders_are_listed(self) -> None:
        jpg_only = self.root / "jpg-only"
        jpg_only.mkdir()
        self._image(str(jpg_only / "only.JPG"))
        first = MainWindow(None)
        try:
            first._accept_path(jpg_only)
            self.assertEqual(len(first.file_list_pane.all_paths()), 1)
        finally:
            first.file_list_pane.release_media()
            first.preview_pane.release_media()
            first.close()

        mixed = self.root / "mixed"
        mixed.mkdir()
        self._image(str(mixed / "a.png"), image_format="PNG")
        self._image(str(mixed / "b.webp"), image_format="WEBP")
        self._image(str(mixed / "c.jpeg"))
        (mixed / "d.mp4").write_bytes(b"invalid media data")
        (mixed / "ignored.txt").write_text("ignored", encoding="utf-8")
        second = MainWindow(None)
        try:
            second._accept_path(mixed)
            self.assertEqual(len(second.file_list_pane.all_paths()), 4)
        finally:
            second.file_list_pane.release_media()
            second.preview_pane.release_media()
            second.close()

    def test_jpeg_aspect_rules_thumbnail_preview_and_basic_info(self) -> None:
        cases = (
            ("landscape.jpg", 320, 180, "landscape"),
            ("portrait.jpeg", 180, 320, "portrait"),
            ("square.JPG", 200, 200, "square"),
        )
        preview = PreviewPane()
        try:
            for name, width, height, expected in cases:
                with self.subTest(name=name):
                    path = self._image(name, width, height)
                    before = _sha256(path)
                    data = build_display_data(path)
                    basic = dict(data.basic_info)
                    self.assertEqual(basic["ファイル名"], path.name)
                    self.assertEqual(basic["パス"], str(path.resolve()))
                    self.assertEqual(basic["形式"], path.suffix[1:].upper())
                    self.assertEqual(
                        basic["解像度"], f"{width} × {height} ピクセル"
                    )
                    self.assertEqual(data.status, "メタデータなし")

                    thumbnail = FileListPane._image_thumbnail(path)
                    self.assertFalse(thumbnail.isNull())
                    self.assertEqual(
                        FileCard._classify_aspect(
                            thumbnail.width(), thumbnail.height()
                        ),
                        expected,
                    )
                    preview.show_file(path)
                    self.assertEqual(preview.stack.currentIndex(), 1)
                    self.assertFalse(preview._image_pixmap.isNull())
                    self.assertEqual(_sha256(path), before)
        finally:
            preview.release_media()
            preview.close()

    def test_exif_orientation_is_applied_to_thumbnail_and_preview(self) -> None:
        path = self._oriented_jpeg()
        thumbnail = FileListPane._image_thumbnail(path)
        self.assertGreater(thumbnail.height(), thumbnail.width())
        preview = PreviewPane()
        try:
            preview.show_file(path)
            self.assertEqual(
                (preview._image_pixmap.width(), preview._image_pixmap.height()),
                (60, 120),
            )
            basic = dict(build_display_data(path).basic_info)
            self.assertEqual(basic["解像度"], "60 × 120 ピクセル")
        finally:
            preview.release_media()
            preview.close()

    def test_jpeg_user_data_missing_relink_and_original_protection(self) -> None:
        source = self._image("source.jpg")
        before = _sha256(source)
        database = MetadataDatabase(self.root / "metami.db")
        database.initialize()
        database.register_files([source])
        database.set_user_details(source, "JPEGタイトル", "JPEGメモ")
        database.set_rating(source, 2)
        database.add_tag(source, "写真")

        reopened = MetadataDatabase(self.root / "metami.db")
        reopened.initialize()
        self.assertEqual(reopened.get_user_title(source), "JPEGタイトル")
        self.assertEqual(reopened.get_memo(source), "JPEGメモ")
        self.assertEqual(reopened.get_file_ratings()[str(source.resolve())], 2)
        self.assertEqual(reopened.get_file_tags(source), ["写真"])
        self.assertEqual(_sha256(source), before)

        old_path = source
        replacement = self._image("replacement.JPEG", 90, 160)
        replacement_hash = _sha256(replacement)
        source.unlink()
        states = reopened.check_registered_files()
        self.assertTrue(states[str(old_path.resolve())])
        reopened.relink_missing_file(old_path, replacement)
        self.assertEqual(reopened.get_user_title(replacement), "JPEGタイトル")
        self.assertEqual(reopened.get_memo(replacement), "JPEGメモ")
        self.assertEqual(reopened.get_file_tags(replacement), ["写真"])
        self.assertEqual(_sha256(replacement), replacement_hash)

    def test_jpeg_missing_record_deletion_removes_only_metami_data(self) -> None:
        source = self._image("delete.jpeg")
        database_path = self.root / "metami.db"
        database = MetadataDatabase(database_path)
        database.initialize()
        database.register_files([source])
        database.set_user_details(source, "削除対象", "メモ")
        database.set_rating(source, 3)
        database.add_tag(source, "共有タグ")
        source.unlink()
        database.check_registered_files()
        database.delete_missing_record(source)

        connection = sqlite3.connect(database_path)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM files").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM user_info"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM file_paths"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM file_tags"
                ).fetchone()[0],
                0,
            )
            # タグ本体は他記録で再利用できるよう維持する。
            self.assertEqual(
                connection.execute("SELECT name FROM tags").fetchone()[0],
                "共有タグ",
            )
        finally:
            connection.close()

    def test_jpeg_format_filter_handles_both_extensions(self) -> None:
        jpg = self._image("first.jpg")
        jpeg = self._image("second.JPEG")
        png = self._image("third.png", image_format="PNG")
        pane = FileListPane()
        try:
            pane.set_paths([jpg, jpeg, png])
            pane.format_combo.setCurrentIndex(
                pane.format_combo.findData("jpeg")
            )
            pane._apply_filters()
            names = {
                Path(
                    pane.list_widget.item(index).data(
                        Qt.ItemDataRole.UserRole
                    )
                ).name
                for index in range(pane.list_widget.count())
            }
            self.assertEqual(names, {"first.jpg", "second.JPEG"})
        finally:
            pane.release_media()
            pane.close()

    def test_initial_split_keeps_organizer_controls_usable(self) -> None:
        window = MainWindow(None)
        try:
            window.resize(1000, 650)
            window.metadata_pane.tabs.setCurrentIndex(1)
            window.show()
            QApplication.processEvents()
            sizes = window.detail_splitter.sizes()
            self.assertGreaterEqual(window.metadata_pane.height(), 300)
            self.assertGreaterEqual(window.metadata_pane.memo_tab.editor.height(), 72)
            self.assertTrue(window.metadata_pane.title_editor.input.isVisible())
            self.assertTrue(window.metadata_pane.tag_editor.input.isVisible())
            self.assertTrue(window.metadata_pane.memo_tab.revert_button.isVisible())
            self.assertTrue(window.metadata_pane.memo_tab.save_button.isVisible())
            ratio = sizes[1] / sum(sizes)
            self.assertGreaterEqual(ratio, 0.40)
        finally:
            window.file_list_pane.release_media()
            window.preview_pane.release_media()
            window.close()


if __name__ == "__main__":
    unittest.main()
