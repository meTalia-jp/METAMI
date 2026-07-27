"""LTX動画作成メモをMarkdownから読み込んで表示する。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


LTX_VIDEO_TIPS_PATH = (
    Path(__file__).resolve().parents[1] / "assets" / "help" / "ltx_video_tips.md"
)
LOAD_ERROR_MESSAGE = "LTX動画作成メモを読み込めませんでした"


def load_ltx_video_tips(path: Path = LTX_VIDEO_TIPS_PATH) -> str | None:
    """UTF-8の作成メモを読み込み、失敗時はNoneを返す。"""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


class LtxVideoTipsDialog(QDialog):
    """編集せず参照・選択・コピーできるLTX作成メモ。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        markdown_path: Path = LTX_VIDEO_TIPS_PATH,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("LTX動画作成メモ")
        self.setObjectName("ltxVideoTipsDialog")
        self.resize(820, 680)
        self.setMinimumSize(520, 400)
        self.setSizeGripEnabled(True)

        self.browser = QTextBrowser()
        self.browser.setObjectName("ltxVideoTipsBrowser")
        self.browser.setReadOnly(True)
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        self.browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        markdown = load_ltx_video_tips(markdown_path)
        if markdown is None:
            self.browser.setPlainText(LOAD_ERROR_MESSAGE)
        else:
            self.browser.setMarkdown(markdown)
        self.browser.moveCursor(self.browser.textCursor().MoveOperation.Start)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("閉じる")
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self.browser, 1)
        layout.addWidget(buttons)
