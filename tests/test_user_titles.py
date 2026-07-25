"""Ver0.9.1 ユーザータイトル機能の回帰試験。"""

from __future__ import annotations

import hashlib
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

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QImage, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from storage.database import DatabaseError, MetadataDatabase  # noqa: E402
from ui.file_list_pane import FileCard, FileListPane  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.metadata_pane import TitleEditor  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class UserTitleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _image(self, name: str, width: int = 64, height: int = 64) -> Path:
        path = self.root / name
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(0xFF8C72C9)
        self.assertTrue(image.save(str(path), "PNG"))
        return path

    def _database(self) -> MetadataDatabase:
        database = MetadataDatabase(self.root / "metami.db")
        database.initialize()
        return database

    def test_version_2_migrates_to_3_without_losing_existing_data(self) -> None:
        source = self._image("existing.png")
        stat = source.stat()
        database = MetadataDatabase(self.root / "version2.db")
        with closing(sqlite3.connect(database.path)) as connection:
            database._migrate_to_v1(connection)
            cursor = connection.execute(
                """
                INSERT INTO files (
                    canonical_path, filename, format_name,
                    size_bytes, modified_ns
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(source.resolve()),
                    source.name,
                    "png",
                    stat.st_size,
                    stat.st_mtime_ns,
                ),
            )
            file_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO user_info (file_id, favorite, memo)
                VALUES (?, 1, ?)
                """,
                (file_id, "既存メモ"),
            )
            tag_id = int(
                connection.execute(
                    "INSERT INTO tags (name) VALUES ('既存タグ')"
                ).lastrowid
            )
            connection.execute(
                "INSERT INTO file_tags (file_id, tag_id) VALUES (?, ?)",
                (file_id, tag_id),
            )
            database._migrate_to_v2(connection)
            connection.execute(
                """
                INSERT INTO schema_info (id, version)
                VALUES (1, 2)
                ON CONFLICT(id) DO UPDATE SET version = 2
                """
            )
            connection.commit()

        database.initialize()
        self.assertEqual(database.get_schema_version(), 3)
        self.assertEqual(database.get_user_title(source), "")
        self.assertEqual(database.get_memo(source), "既存メモ")
        self.assertEqual(database.get_file_tags(source), ["既存タグ"])
        self.assertEqual(
            database.get_file_ratings()[str(source.resolve())], 1
        )
        with closing(sqlite3.connect(database.path)) as connection:
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(user_info)")
            }
        self.assertIn("title", columns)

        database.initialize()
        self.assertEqual(database.get_schema_version(), 3)
        self.assertEqual(database.get_memo(source), "既存メモ")

    def test_version_3_migration_failure_rolls_back(self) -> None:
        database_path = self.root / "rollback.db"
        database = MetadataDatabase(database_path)
        with closing(sqlite3.connect(database_path)) as connection:
            database._migrate_to_v1(connection)
            database._migrate_to_v2(connection)
            connection.execute(
                "INSERT INTO schema_info (id, version) VALUES (1, 2)"
            )
            connection.commit()

        class FailingMigrationDatabase(MetadataDatabase):
            @staticmethod
            def _migrate_to_v3(connection: sqlite3.Connection) -> None:
                connection.execute(
                    """
                    ALTER TABLE user_info
                    ADD COLUMN title TEXT NOT NULL DEFAULT ''
                    """
                )
                raise sqlite3.OperationalError("forced migration failure")

        with self.assertRaises(DatabaseError):
            FailingMigrationDatabase(database_path).initialize()

        with closing(sqlite3.connect(database_path)) as connection:
            version = connection.execute(
                "SELECT version FROM schema_info WHERE id = 1"
            ).fetchone()[0]
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(user_info)")
            }
        self.assertEqual(version, 2)
        self.assertNotIn("title", columns)

    def test_title_rules_persistence_duplicates_and_original_protection(
        self,
    ) -> None:
        first = self._image("first.png")
        second = self._image("second.png")
        first_hash = _sha256(first)
        second_hash = _sha256(second)
        database = self._database()
        database.set_rating(first, 3)
        database.add_tag(first, "人物")
        normalized = database.set_user_details(
            first, "  同じタイトル  ", "既存メモ"
        )
        database.set_user_title(second, "同じタイトル")
        self.assertEqual(normalized, "同じタイトル")

        reopened = MetadataDatabase(database.path)
        reopened.initialize()
        self.assertEqual(reopened.get_user_title(first), "同じタイトル")
        self.assertEqual(reopened.get_user_title(second), "同じタイトル")
        self.assertEqual(
            reopened.get_user_titles_by_path(),
            {
                str(first.resolve()): "同じタイトル",
                str(second.resolve()): "同じタイトル",
            },
        )
        forty = "題" * 40
        self.assertEqual(reopened.set_user_title(first, forty), forty)
        with self.assertRaises(ValueError):
            reopened.set_user_title(first, "題" * 41)
        self.assertEqual(reopened.get_user_title(first), forty)
        self.assertEqual(reopened.get_file_ratings()[str(first.resolve())], 3)
        self.assertEqual(reopened.get_file_tags(first), ["人物"])
        self.assertEqual(reopened.get_memo(first), "既存メモ")
        self.assertEqual(_sha256(first), first_hash)
        self.assertEqual(_sha256(second), second_hash)

        reopened.set_user_title(first, "   ")
        self.assertEqual(reopened.get_user_title(first), "")

    def test_title_editor_and_cards_enforce_stable_one_line_display(self) -> None:
        editor = TitleEditor()
        landscape = FileCard(self.root / "landscape.png")
        portrait = FileCard(self.root / "portrait.png")
        try:
            editor.set_title("", True)
            editor.input.setFocus()
            QTest.keyClicks(editor.input, "a" * 45)
            self.assertEqual(len(editor.text()), 40)
            self.assertEqual(editor.input.maxLength(), 40)
            self.assertEqual(
                editor.input.placeholderText(),
                "未設定時はファイル名を表示します",
            )

            full_title = "長いユーザータイトル" * 4
            landscape.set_user_title(full_title)
            landscape.set_thumbnail(QPixmap(160, 90))
            portrait.set_user_title(full_title)
            portrait.set_thumbnail(QPixmap(90, 160))
            for card in (landscape, portrait):
                self.assertEqual(card._name_text, full_title)
                self.assertEqual(card.name_label.toolTip(), full_title)
                self.assertFalse(card.name_label.wordWrap())
                self.assertEqual(card.size(), card.minimumSize())

            landscape.set_user_title("")
            self.assertEqual(landscape._name_text, "landscape.png")
            self.assertEqual(
                landscape.name_label.toolTip(), str(landscape.path)
            )
        finally:
            editor.close()
            landscape.close()
            portrait.close()

    def test_title_and_existing_text_are_searchable(self) -> None:
        first = self._image("first_file.png")
        second = self._image("second_file.png")
        pane = FileListPane()
        try:
            pane.set_paths(
                [first, second],
                titles_by_path={str(first.resolve()): "夏の思い出"},
                tags_by_path={str(first.resolve()): ["人物"]},
                memos_by_path={
                    str(second.resolve()): "明るさを修正する"
                },
            )

            def displayed() -> list[str]:
                return [
                    Path(
                        pane.list_widget.item(index).data(
                            Qt.ItemDataRole.UserRole
                        )
                    ).name
                    for index in range(pane.list_widget.count())
                ]

            pane.target_combo.setCurrentIndex(
                pane.target_combo.findData("title")
            )
            pane.search_edit.setText("思い")
            pane._apply_filters()
            self.assertEqual(displayed(), ["first_file.png"])

            pane.target_combo.setCurrentIndex(
                pane.target_combo.findData("all")
            )
            for query, expected in (
                ("人物", ["first_file.png"]),
                ("修正", ["second_file.png"]),
                ("SECOND", ["second_file.png"]),
            ):
                pane.search_edit.setText(query)
                pane._apply_filters()
                self.assertEqual(displayed(), expected)

            pane.search_edit.setText("思い")
            pane._apply_filters()
            pane.set_user_title(first, "冬の記録")
            self.assertEqual(displayed(), [])
        finally:
            pane.clear()
            pane.release_media()
            pane.close()
            QApplication.sendPostedEvents(
                None, QEvent.Type.DeferredDelete
            )
            QApplication.processEvents()

    def test_integrated_save_updates_card_and_blank_restores_filename(
        self,
    ) -> None:
        source = self._image("original_name.png", 160, 90)
        database = self._database()
        database.set_rating(source, 2)
        database.add_tag(source, "維持タグ")
        database.set_memo(source, "維持メモ")
        window = MainWindow(database)
        try:
            window._set_paths([source])
            QApplication.processEvents()
            window.metadata_pane.title_editor.input.setText("  表示タイトル  ")
            QTest.mouseClick(
                window.metadata_pane.memo_tab.save_button,
                Qt.MouseButton.LeftButton,
            )
            QApplication.processEvents()
            self.assertEqual(database.get_user_title(source), "表示タイトル")
            card = window.file_list_pane._visible_card(source)
            self.assertIsNotNone(card)
            assert card is not None
            self.assertEqual(card._name_text, "表示タイトル")
            self.assertEqual(database.get_file_ratings()[str(source.resolve())], 2)
            self.assertEqual(database.get_file_tags(source), ["維持タグ"])
            self.assertEqual(database.get_memo(source), "維持メモ")

            window.file_list_pane.target_combo.setCurrentIndex(
                window.file_list_pane.target_combo.findData("title")
            )
            window.file_list_pane.search_edit.setText("表示")
            window.file_list_pane._apply_filters()
            window.metadata_pane.title_editor.input.setText("別タイトル")
            window.metadata_pane.memo_tab.editor.setPlainText("更新メモ")
            QTest.mouseClick(
                window.metadata_pane.memo_tab.save_button,
                Qt.MouseButton.LeftButton,
            )
            QApplication.processEvents()
            self.assertEqual(database.get_user_title(source), "別タイトル")
            self.assertEqual(database.get_memo(source), "更新メモ")
            self.assertEqual(window.file_list_pane.list_widget.count(), 0)

            window.file_list_pane.reset_filters()
            QApplication.processEvents()
            window.metadata_pane.title_editor.input.clear()
            QTest.mouseClick(
                window.metadata_pane.memo_tab.save_button,
                Qt.MouseButton.LeftButton,
            )
            QApplication.processEvents()
            self.assertEqual(database.get_user_title(source), "")
            restored_card = window.file_list_pane._visible_card(source)
            self.assertIsNotNone(restored_card)
            assert restored_card is not None
            self.assertEqual(restored_card._name_text, source.name)
            self.assertEqual(database.get_file_ratings()[str(source.resolve())], 2)
            self.assertEqual(database.get_file_tags(source), ["維持タグ"])
            self.assertEqual(database.get_memo(source), "更新メモ")
        finally:
            window.file_list_pane.release_media()
            window.preview_pane.release_media()
            window.close()

    def test_missing_relink_and_delete_follow_file_record(self) -> None:
        old = self._image("old.png")
        survivor = self._image("survivor.png")
        database = self._database()
        database.set_user_details(old, "保持タイトル", "保持メモ")
        database.set_rating(old, 2)
        database.add_tag(old, "保持タグ")
        database.set_user_title(survivor, "保持タイトル")
        old.unlink()
        database.check_registered_files()
        self.assertEqual(database.get_user_title(old), "保持タイトル")

        replacement = self._image("replacement.png")
        replacement_hash = _sha256(replacement)
        database.relink_missing_file(old, replacement)
        self.assertEqual(
            database.get_user_title(replacement), "保持タイトル"
        )
        self.assertEqual(database.get_memo(replacement), "保持メモ")
        self.assertEqual(
            database.get_file_ratings()[str(replacement.resolve())], 2
        )
        self.assertEqual(database.get_file_tags(replacement), ["保持タグ"])
        self.assertEqual(_sha256(replacement), replacement_hash)

        replacement.unlink()
        database.check_registered_files()
        database.delete_missing_record(replacement)
        self.assertEqual(database.get_user_title(replacement), "")
        self.assertEqual(database.get_user_title(survivor), "保持タイトル")
        with closing(sqlite3.connect(database.path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM files WHERE canonical_path = ?",
                (str(replacement.resolve()),),
            ).fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
