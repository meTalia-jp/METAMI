"""Ver1.0.x GUI小規模改修の回帰試験。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMenu, QToolButton


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ui.main_window import (
    DisplayTabSettingsDialog,
    MainWindow,
    RATING_PROMPT_MESSAGES,
)


class Ver10UiSmallUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.first_image = self.root / "first.png"
        self.second_image = self.root / "second.png"
        for path, color in (
            (self.first_image, 0xFFCC8844),
            (self.second_image, 0xFF4488CC),
        ):
            image = QImage(64, 48, QImage.Format.Format_ARGB32)
            image.fill(color)
            self.assertTrue(image.save(str(path)))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _close(window: MainWindow) -> None:
        window.file_list_pane.release_media()
        window.preview_pane.release_media()
        window.close()

    def test_character_subtitles_are_explicitly_two_lines(self) -> None:
        window = MainWindow(None)
        try:
            left_roles = window.file_list_pane.character_header.findChildren(
                type(window.status_icon), "characterRole"
            )
            right_roles = window.right_character_header.findChildren(
                type(window.status_icon), "characterRole"
            )
            self.assertEqual(
                left_roles[0].text(), "創作のひらめき\n素材ブラウジング"
            )
            self.assertEqual(
                right_roles[0].text(), "メタデータ解析\n編集ツール"
            )
        finally:
            self._close(window)

    def test_rating_prompt_changes_only_when_selected_work_changes(self) -> None:
        choices = [
            RATING_PROMPT_MESSAGES[0],
            RATING_PROMPT_MESSAGES[1],
            RATING_PROMPT_MESSAGES[2],
        ]
        with patch("ui.main_window.random.choice", side_effect=choices) as choose:
            window = MainWindow(None)
            try:
                window._set_paths([self.first_image, self.second_image])
                window._show_file(str(self.first_image))
                first_prompt = window.rating_appeal.prompt_label.text()
                window._show_file(str(self.first_image))
                self.assertEqual(
                    window.rating_appeal.prompt_label.text(), first_prompt
                )
                window._show_file(str(self.second_image))
                self.assertNotEqual(
                    window.rating_appeal.prompt_label.text(), first_prompt
                )
                self.assertIn(
                    "font-size: 9pt",
                    window.rating_appeal.prompt_label.styleSheet(),
                )
                self.assertEqual(choose.call_count, 3)
            finally:
                self._close(window)

    def test_menus_and_future_actions(self) -> None:
        window = MainWindow(None)
        try:
            file_menu = window.file_menu
            self.assertIsInstance(file_menu, QMenu)
            file_actions = {
                action.text().replace("&", ""): action
                for action in file_menu.actions()
                if not action.isSeparator()
            }
            self.assertIn("ファイルを開く(O)...", file_actions)
            self.assertIn("フォルダを開く(D)...", file_actions)
            self.assertTrue(file_actions["最近開いたフォルダ"].isEnabled())
            self.assertIsNotNone(file_actions["最近開いたフォルダ"].menu())
            self.assertTrue(
                file_actions["サブフォルダも含む"].isCheckable()
            )
            self.assertFalse(
                file_actions["見つからない項目を確認・整理"].isEnabled()
            )
            self.assertEqual(
                file_actions["見つからない項目を確認・整理"].toolTip(),
                "今後追加予定",
            )
            view_actions = [
                action.text() for action in window.view_menu.actions()
            ]
            self.assertEqual(
                view_actions,
                ["表示タブの設定", "表示を初期状態に戻す"],
            )
            help_actions = [
                action.text() for action in window.help_menu.actions()
            ]
            self.assertEqual(
                help_actions,
                ["METAMIについて", "バージョン情報", "作成メモ"],
            )
            self.assertEqual(
                [
                    action.text()
                    for action in window.creation_notes_menu.actions()
                ],
                ["LTX動画作成メモ"],
            )
        finally:
            self._close(window)

    def test_optional_tabs_can_hide_and_reset_while_wan_stays_disabled(self) -> None:
        window = MainWindow(None)
        try:
            tabs = window.metadata_pane.tabs
            self.assertIsNone(tabs.cornerWidget())
            scroll_buttons = {
                button.objectName(): button
                for button in tabs.tabBar().findChildren(QToolButton)
            }
            self.assertFalse(scroll_buttons["ScrollLeftButton"].icon().isNull())
            self.assertFalse(scroll_buttons["ScrollRightButton"].icon().isNull())
            self.assertEqual(
                scroll_buttons["ScrollLeftButton"].iconSize().width(), 12
            )
            self.assertEqual(
                scroll_buttons["ScrollRightButton"].iconSize().width(), 12
            )
            self.assertEqual(
                scroll_buttons["ScrollLeftButton"].minimumWidth(), 20
            )
            self.assertIn(
                "padding: 0",
                scroll_buttons["ScrollRightButton"].styleSheet(),
            )
            self.assertEqual(
                [tabs.tabText(index) for index in range(tabs.count())],
                [
                    "基本情報",
                    "タグ・メモ",
                    "LTX",
                    "WAN",
                    "画像生成",
                    "Workflow",
                    "JSON全文",
                ],
            )
            self.assertEqual(
                window.metadata_pane.ltx_tab.findChild(
                    type(window.status_icon), "placeholderTabMessage"
                ).text(),
                "LTX 2.3の生成情報を確認できません",
            )
            wan_index = tabs.indexOf(window.metadata_pane.wan_tab)
            self.assertFalse(tabs.isTabEnabled(wan_index))
            self.assertEqual(
                tabs.tabToolTip(wan_index), "WAN対応は準備中です"
            )
            window.metadata_pane.set_optional_tab_visible("ltx", False)
            window.metadata_pane.set_optional_tab_visible("workflow", False)
            self.assertFalse(
                window.metadata_pane.optional_tab_visibility()["ltx"]
            )
            window.metadata_pane.reset_optional_tabs()
            self.assertTrue(
                all(window.metadata_pane.optional_tab_visibility().values())
            )
            self.assertFalse(tabs.isTabEnabled(wan_index))
        finally:
            self._close(window)

    def test_display_reset_restores_only_splitter_positions(self) -> None:
        window = MainWindow(None)
        try:
            window.resize(1200, 760)
            window.show()
            QApplication.processEvents()
            original_size = window.size()
            initial_page_sizes = window.splitter.sizes()
            initial_detail_sizes = window.detail_splitter.sizes()
            window.splitter.setSizes([250, 950])
            window.detail_splitter.setSizes([180, 580])
            window.metadata_pane.tabs.setCurrentIndex(1)
            current_tab = window.metadata_pane.tabs.currentWidget()
            window._reset_display_layout()
            QApplication.processEvents()
            self.assertEqual(window.size(), original_size)
            self.assertIs(
                window.metadata_pane.tabs.currentWidget(), current_tab
            )
            self.assertEqual(window.splitter.sizes(), initial_page_sizes)
            self.assertEqual(
                window.detail_splitter.sizes(), initial_detail_sizes
            )
        finally:
            self._close(window)

    def test_tab_settings_dialog_has_its_own_reset(self) -> None:
        dialog = DisplayTabSettingsDialog(
            {
                "ltx": False,
                "wan": False,
                "image_generation": False,
                "workflow": False,
                "json": False,
            }
        )
        try:
            self.assertEqual(
                dialog.reset_button.text(), "タブ設定を初期状態に戻す"
            )
            dialog.reset_button.click()
            self.assertTrue(all(dialog.visibility().values()))
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
