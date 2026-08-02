"""METAMIデータ管理の読み取り専用ダイアログ。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from storage.data_management import DatabaseInfo


DATA_MANAGEMENT_HELP_PATH = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "help"
    / "data_management.md"
)


class DatabaseInfoDialog(QDialog):
    """中央DBの場所、件数、整合性を編集せず表示する。"""

    def __init__(
        self, info: DatabaseInfo, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("データベース情報")
        self.setObjectName("databaseInfoDialog")
        self.resize(620, 620)
        self.setMinimumSize(520, 560)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        values = (
            ("データベースファイル名", info.file_name),
            ("保存場所", info.directory),
            ("ファイルサイズ", _format_file_size(info.size_bytes)),
            ("最終更新日時", info.modified_at.strftime("%Y-%m-%d %H:%M:%S")),
            ("登録ファイル数", f"{info.registered_files}件"),
            ("タイトル登録数", f"{info.titled_files}件"),
            ("タグ登録項目数", f"{info.tagged_files}件"),
            ("メモ登録項目数", f"{info.memo_files}件"),
            ("評価登録項目数", f"{info.rated_files}件"),
            ("DBスキーマバージョン", str(info.schema_version)),
            (
                "METAMI対応スキーマ",
                str(info.supported_schema_version),
            ),
            ("互換性", info.compatibility_status),
            ("標準ハッシュ方式", info.standard_hash_algorithm),
            ("ハッシュ計算済み", f"{info.hash_calculated_files}件"),
            ("未計算", f"{info.hash_not_calculated_files}件"),
            ("再計算が必要", f"{info.hash_stale_files}件"),
            ("計算失敗", f"{info.hash_failed_files}件"),
            ("見つからない", f"{info.hash_missing_files}件"),
            ("不明なハッシュ状態", f"{info.hash_unknown_files}件"),
            ("SQLiteバージョン", info.sqlite_version),
            (
                "整合性確認結果",
                "正常" if info.integrity_ok else "問題が検出されました",
            ),
        )
        self.value_labels: dict[str, QLabel] = {}
        for heading, value in values:
            label = QLabel(value)
            label.setWordWrap(True)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            if heading == "整合性確認結果":
                label.setToolTip(info.integrity_details)
            form.addRow(f"{heading}:", label)
            self.value_labels[heading] = label

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("閉じる")
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(buttons)


class DataManagementHelpDialog(QDialog):
    """バックアップと復元の注意事項をMarkdownで表示する。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        markdown_path: Path = DATA_MANAGEMENT_HELP_PATH,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("METAMIデータのバックアップと復元")
        self.resize(760, 620)
        self.setMinimumSize(520, 400)
        self.setSizeGripEnabled(True)

        self.browser = QTextBrowser()
        self.browser.setReadOnly(True)
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        self.browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        try:
            markdown = markdown_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            self.browser.setPlainText(
                "バックアップと復元の説明を読み込めませんでした。"
            )
        else:
            self.browser.setMarkdown(markdown)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("閉じる")
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(self.browser, 1)
        layout.addWidget(buttons)


def _format_file_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
