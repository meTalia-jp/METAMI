"""Ver0.8.8 評価案内・メモ抜粋・DB状態表示の回帰試験。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer  # noqa: E402
from PySide6.QtTest import QSignalSpy  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from storage.database import MetadataDatabase  # noqa: E402
from ui.file_list_pane import FileListPane  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.preview_pane import PreviewPane  # noqa: E402
from ui.surface_widgets import AnalysisStatusPanel  # noqa: E402


class Ver088Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.image_path = self.root / "memo_sample.png"
        image = QImage(320, 180, QImage.Format.Format_ARGB32)
        image.fill(0xFFF8F3FF)
        self.assertTrue(image.save(str(self.image_path)))
        self.database = MetadataDatabase(self.root / "metami.db")
        self.database.initialize()
        self.database.register_files([self.image_path])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_database_returns_saved_memos_by_path(self) -> None:
        self.database.set_memo(
            self.image_path, "最初の行です。\n続きの内容です。"
        )
        memos = self.database.get_memos_by_path()
        self.assertEqual(
            memos[str(self.image_path.resolve())],
            "最初の行です。\n続きの内容です。",
        )

        self.database.set_memo(self.image_path, "")
        self.assertNotIn(
            str(self.image_path.resolve()),
            self.database.get_memos_by_path(),
        )

    def test_card_shows_one_line_memo_without_changing_height(self) -> None:
        pane = FileListPane()
        try:
            original_height = pane.list_widget.sizeHintForRow(0)
            memo = "光を調整する。\n次回は背景も確認する。"
            pane.set_paths(
                [self.image_path],
                memos_by_path={str(self.image_path.resolve()): memo},
            )
            card = pane._visible_card(self.image_path)
            self.assertIsNotNone(card)
            assert card is not None
            self.assertEqual(card._memo_text, "光を調整する。 次回は背景も確認する。")
            self.assertNotIn("\n", card.memo_excerpt.text())
            self.assertEqual(card.memo_excerpt.toolTip(), memo)
            self.assertEqual(card.height(), card.maximumHeight())
            self.assertGreater(card.height(), max(0, original_height))

            fixed_height = card.height()
            pane.set_memo(self.image_path, "更新済みメモ")
            self.assertEqual(card.height(), fixed_height)
            self.assertEqual(card._memo_text, "更新済みメモ")
            pane.set_memo(self.image_path, "")
            self.assertTrue(card.memo_excerpt.isHidden())
            self.assertEqual(card.height(), fixed_height)
        finally:
            pane.release_media()
            pane.close()

    def test_database_state_has_three_real_states(self) -> None:
        panel = AnalysisStatusPanel("online")
        try:
            self.assertEqual(panel.database_label.text(), "DB ● Online")
            panel.set_state("error")
            self.assertEqual(panel.database_label.text(), "DB ● Error")
            panel.set_state("offline")
            self.assertEqual(panel.database_label.text(), "DB ○ Offline")
        finally:
            panel.close()

    def test_image_nudge_is_once_per_session_and_rating_clears_it(self) -> None:
        window = MainWindow(self.database)
        try:
            window._set_paths([self.image_path])
            window._show_file(str(self.image_path))
            self.assertTrue(window._rating_prompt_timer.isActive())
            self.assertEqual(window._rating_prompt_timer.interval(), 18_000)
            window._rating_prompt_timer.stop()
            self.assertTrue(window.rating_appeal.nudge_label.isHidden())

            window._request_rating_nudge()
            self.assertFalse(window.rating_appeal.nudge_label.isHidden())
            first_message = window.rating_appeal.nudge_label.text()
            self.assertTrue(first_message)

            window.rating_appeal.clear_nudge()
            window._request_rating_nudge()
            self.assertTrue(window.rating_appeal.nudge_label.isHidden())

            window._rating_prompted_paths.clear()
            window._request_rating_nudge()
            self.assertFalse(window.rating_appeal.nudge_label.isHidden())
            window._save_current_rating(2, 0)
            self.assertTrue(window.rating_appeal.nudge_label.isHidden())
            self.assertEqual(
                self.database.get_file_ratings()[
                    str(self.image_path.resolve())
                ],
                2,
            )
        finally:
            window.file_list_pane.release_media()
            window.preview_pane.release_media()
            window.close()

    def test_rating_nudge_waits_while_playing_or_editing(self) -> None:
        window = MainWindow(self.database)
        try:
            window._set_paths([self.image_path])
            window._show_file(str(self.image_path))
            window._rating_prompt_timer.stop()

            window.preview_pane.is_video_playing = lambda: True
            window._request_rating_nudge()
            window._rating_prompt_timer.stop()
            self.assertTrue(window._rating_prompt_pending)
            self.assertTrue(window.rating_appeal.nudge_label.isHidden())

            window.preview_pane.is_video_playing = lambda: False
            window.metadata_pane.is_organizer_editing = lambda: True
            window._show_pending_rating_nudge()
            window._rating_prompt_timer.stop()
            self.assertTrue(window._rating_prompt_pending)
            self.assertTrue(window.rating_appeal.nudge_label.isHidden())

            window.metadata_pane.is_organizer_editing = lambda: False
            window._show_pending_rating_nudge()
            self.assertFalse(window._rating_prompt_pending)
            self.assertFalse(window.rating_appeal.nudge_label.isHidden())
        finally:
            window.file_list_pane.release_media()
            window.preview_pane.release_media()
            window.close()

    def test_rated_file_does_not_show_nudge(self) -> None:
        self.database.set_rating(self.image_path, 3)
        window = MainWindow(self.database)
        try:
            window._set_paths([self.image_path])
            window._show_file(str(self.image_path))
            self.assertFalse(window._rating_prompt_timer.isActive())
            window._request_rating_nudge()
            self.assertTrue(window.rating_appeal.nudge_label.isHidden())
        finally:
            window.file_list_pane.release_media()
            window.preview_pane.release_media()
            window.close()

    def test_video_engagement_emits_at_75_percent_or_end_only_once(self) -> None:
        pane = PreviewPane()
        try:
            spy = QSignalSpy(pane.ratingEngagementReached)
            pane._check_video_engagement(749, 1000)
            self.assertEqual(spy.count(), 0)
            pane._check_video_engagement(750, 1000)
            self.assertEqual(spy.count(), 1)
            pane._check_video_engagement(1000, 1000)
            self.assertEqual(spy.count(), 1)
            pane._engagement_emitted = False
            pane._on_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
            self.assertEqual(spy.count(), 2)
            pane._on_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
            self.assertEqual(spy.count(), 2)
        finally:
            pane.release_media()
            pane.close()


if __name__ == "__main__":
    unittest.main()
