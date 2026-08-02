"""Ver1.0.2候補のLTX動画作成メモを確認する。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QTextBrowser,
)


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ui.ltx_video_tips_dialog import (
    LOAD_ERROR_MESSAGE,
    LTX_VIDEO_TIPS_PATH,
    LtxVideoTipsDialog,
    load_ltx_video_tips,
)
from ui.main_window import MainWindow


class LtxVideoTipsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _close_window(window: MainWindow) -> None:
        window.file_list_pane.release_media()
        window.preview_pane.release_media()
        window.close()

    def test_markdown_file_contains_required_readable_elements(self) -> None:
        markdown = load_ltx_video_tips()
        self.assertIsNotNone(markdown)
        assert markdown is not None
        self.assertIn("# LTX動画作成メモ", markdown)
        self.assertIn("## 1. 解像度の目安", markdown)
        self.assertIn("| 呼び方 | 一般的な設定値 | LTXでの扱い |", markdown)
        self.assertIn("> 解像度は32の倍数条件", markdown)
        self.assertIn("```text", markdown)
        self.assertIn("- Prompt EnhanceをOFFにして比較する", markdown)
        self.assertNotIn("\ufffd", markdown)

    def test_dialog_renders_markdown_and_supports_read_only_copy(self) -> None:
        dialog = LtxVideoTipsDialog()
        try:
            self.assertEqual(dialog.windowTitle(), "LTX動画作成メモ")
            self.assertIsInstance(dialog.browser, QTextBrowser)
            self.assertTrue(dialog.browser.isReadOnly())
            self.assertFalse(dialog.browser.openExternalLinks())
            self.assertIn("<table", dialog.browser.toHtml().casefold())
            self.assertIn("LTX動画作成メモ", dialog.browser.toPlainText())
            self.assertEqual(
                dialog.browser.horizontalScrollBarPolicy(),
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )
            self.assertGreater(dialog.height(), 400)
            dialog.resize(900, 720)
            self.assertEqual(dialog.size().width(), 900)
            self.assertEqual(dialog.size().height(), 720)

            dialog.browser.selectAll()
            dialog.browser.copy()
            QApplication.processEvents()
            self.assertIn(
                "LTX動画作成メモ", QApplication.clipboard().text()
            )
        finally:
            dialog.close()

    def test_close_button_rejects_dialog(self) -> None:
        dialog = LtxVideoTipsDialog()
        buttons = dialog.findChild(QDialogButtonBox)
        self.assertIsNotNone(buttons)
        assert buttons is not None
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        self.assertIsNotNone(close_button)
        assert close_button is not None
        self.assertEqual(close_button.text(), "閉じる")
        close_button.click()
        self.assertEqual(dialog.result(), dialog.DialogCode.Rejected)

    def test_missing_markdown_shows_safe_message(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.md"
            self.assertIsNone(load_ltx_video_tips(missing))
            dialog = LtxVideoTipsDialog(markdown_path=missing)
            try:
                self.assertEqual(
                    dialog.browser.toPlainText(), LOAD_ERROR_MESSAGE
                )
            finally:
                dialog.close()

    def test_help_submenu_opens_dedicated_dialog(self) -> None:
        window = MainWindow(None)
        try:
            self.assertEqual(
                [action.text() for action in window.help_menu.actions()],
                [
                    "METAMIについて",
                    "バージョン情報",
                    "作成メモ",
                    "METAMIデータのバックアップと復元",
                ],
            )
            actions = window.creation_notes_menu.actions()
            self.assertEqual(
                [action.text() for action in actions],
                ["LTX動画作成メモ"],
            )
            with patch("ui.main_window.LtxVideoTipsDialog") as dialog_class:
                actions[0].trigger()
                dialog_class.assert_called_once_with(window)
                dialog_class.return_value.exec.assert_called_once_with()
        finally:
            self._close_window(window)

    def test_default_markdown_path_is_distribution_relative(self) -> None:
        self.assertEqual(
            LTX_VIDEO_TIPS_PATH,
            SRC / "assets" / "help" / "ltx_video_tips.md",
        )


if __name__ == "__main__":
    unittest.main()
