"""3つの独立ペインを接続するメインウィンドウ。"""

from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
    QMainWindow,
    QSplitter,
)

from metadata.display_service import DisplayDataError, build_display_data
from storage.database import DatabaseError, MetadataDatabase
from ui.file_list_pane import FileListPane
from ui.decorations import (
    status_pixmap,
)
from ui.metadata_pane import MetadataPane
from ui.ltx_video_tips_dialog import LtxVideoTipsDialog
from ui.preview_pane import PreviewPane
from ui.surface_widgets import (
    AnalysisCharacterHeader,
    AnalysisConsolePanel,
    AnalysisStatusPanel,
    RatingAppealPanel,
)
from ui.styles import (
    APP_STYLE,
    DETAIL_SPLITTER_SIZES,
    PAGE_SPLITTER_SIZES,
    WINDOW_SIZE,
)


APP_TITLE = "M.E.T.A.M.I."
SUPPORTED_SUFFIXES = {".png", ".webp", ".jpg", ".jpeg", ".mp4"}
RATING_IMAGE_DELAY_MS = 18_000
RATING_RETRY_DELAY_MS = 1_500
RATING_NUDGE_MESSAGES = (
    "この作品、どうだった？",
    "気に入ったら評価してね",
    "感想を星で教えてね",
    "めたみに感想を教えて",
    "★、ほしいかも",
)
RATING_PROMPT_MESSAGES = (
    "この作品、どうだった？",
    "気に入ったら★をつけてね",
    "今日の出来はどう？",
    "お気に入り度を教えてね",
    "この作品、何点？",
    "いいと思ったら星で評価",
    "あなたの評価を聞かせて",
    "また見たい作品かな？",
    "ひらめき度を星で残そう",
    "今回の仕上がりはどう？",
)


class ToggleSwitch(QAbstractButton):
    """ONは右、OFFは左へノブが移動する小型トグル。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(42, 22)

    def sizeHint(self) -> QSize:
        return QSize(42, 22)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self.rect().adjusted(1, 2, -1, -2)
        checked = self.isChecked()
        painter.setPen(
            QPen(QColor("#303238" if checked else "#858a93"), 1)
        )
        painter.setBrush(QColor("#555960" if checked else "#c8cbd1"))
        painter.drawRoundedRect(track, 10, 10)
        knob_size = 16
        knob_x = (
            self.width() - knob_size - 4 if checked else 4
        )
        painter.setPen(QPen(QColor("#655b72"), 0.8))
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(knob_x, 3, knob_size, knob_size)
        painter.end()


class DisplayTabSettingsDialog(QDialog):
    """起動中だけ保持する任意タブの表示設定。"""

    LABELS = (
        ("ltx", "LTX"),
        ("wan", "WAN"),
        ("image_generation", "画像生成"),
        ("workflow", "Workflow"),
        ("json", "JSON全文"),
    )

    def __init__(
        self, visibility: dict[str, bool], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("表示タブの設定")
        self.setModal(True)
        self.setMinimumWidth(320)
        self.toggles: dict[str, ToggleSwitch] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        description = QLabel(
            "詳細欄へ表示するタブを選んでください。\n"
            "基本情報とタグ・メモは常に表示されます。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        for key, label in self.LABELS:
            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(6, 1, 6, 1)
            row_layout.setSpacing(2)
            title = QLabel(label)
            toggle = ToggleSwitch()
            toggle.setObjectName(f"{key}TabToggle")
            toggle.setChecked(visibility[key])
            toggle.setAccessibleName(f"{label}タブを表示")
            toggle.setToolTip("右: ON／左: OFF")
            self.toggles[key] = toggle
            line = QWidget()
            line_layout = QVBoxLayout(line)
            line_layout.setContentsMargins(0, 0, 0, 0)
            line_layout.addWidget(title)
            line_layout.addWidget(
                toggle, 0, Qt.AlignmentFlag.AlignRight
            )
            row_layout.addWidget(line)
            layout.addWidget(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.reset_button = buttons.addButton(
            "タブ設定を初期状態に戻す",
            QDialogButtonBox.ButtonRole.ResetRole,
        )
        self.reset_button.clicked.connect(self.reset_visibility)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def visibility(self) -> dict[str, bool]:
        return {
            key: toggle.isChecked() for key, toggle in self.toggles.items()
        }

    def reset_visibility(self) -> None:
        for toggle in self.toggles.values():
            toggle.setChecked(True)


class MainWindow(QMainWindow):
    """入力受付と各ペイン間の連携だけを担当する。"""

    def __init__(
        self,
        database: MetadataDatabase | None = None,
        database_error: str = "",
    ) -> None:
        super().__init__()
        self.database = database
        self.database_error = database_error
        self._current_path: Path | None = None
        self._ignore_next_file_signal = False
        self._rating_prompted_paths: set[str] = set()
        self._rating_appeal_path_key = ""
        self._rating_prompt_pending = False
        self._rating_prompt_timer = QTimer(self)
        self._rating_prompt_timer.setSingleShot(True)
        self._rating_prompt_timer.timeout.connect(self._request_rating_nudge)
        self.setWindowTitle(APP_TITLE)
        self.resize(*WINDOW_SIZE)
        self.setMinimumSize(1000, 650)
        self.setAcceptDrops(True)
        self.setStyleSheet(APP_STYLE)

        self.file_list_pane = FileListPane()
        self.preview_pane = PreviewPane()
        self.metadata_pane = MetadataPane()
        self.file_list_pane.setMinimumWidth(380)
        self.preview_pane.setMinimumHeight(190)
        self.metadata_pane.setMinimumHeight(300)
        self.file_list_pane.fileSelected.connect(self._show_file)
        self.file_list_pane.selectionCleared.connect(self._show_no_search_results)
        self.file_list_pane.openFileRequested.connect(self._select_file)
        self.file_list_pane.openFolderRequested.connect(self._select_folder)
        self.file_list_pane.ratingChangeRequested.connect(
            self._save_rating
        )
        self.metadata_pane.ratingChangeRequested.connect(
            self._save_current_rating
        )
        self.metadata_pane.tagAddRequested.connect(self._add_current_tag)
        self.metadata_pane.tagRemoveRequested.connect(self._remove_current_tag)
        self.metadata_pane.organizerSaveRequested.connect(
            self._save_current_organizer
        )
        self.metadata_pane.notificationRequested.connect(
            lambda message: self._set_status(message, "active")
        )
        self.preview_pane.mediaError.connect(
            lambda message: self._set_status(message, "error")
        )
        self.preview_pane.locateMissingRequested.connect(
            self._locate_missing_file
        )
        self.preview_pane.deleteMissingRequested.connect(
            self._delete_missing_record
        )
        self.preview_pane.ratingEngagementReached.connect(
            self._request_rating_nudge
        )
        self.preview_pane.playbackIdle.connect(
            self._show_pending_rating_nudge
        )

        self.detail_splitter = QSplitter(Qt.Orientation.Vertical)
        self.detail_splitter.setObjectName("detailSplitter")
        self.detail_splitter.setHandleWidth(12)
        self.detail_splitter.addWidget(self.preview_pane)
        self.detail_splitter.addWidget(self.metadata_pane)
        self.detail_splitter.setSizes(DETAIL_SPLITTER_SIZES)
        self.detail_splitter.setStretchFactor(0, 3)
        self.detail_splitter.setStretchFactor(1, 2)

        right_page = AnalysisConsolePanel()
        right_page.setObjectName("analysisConsole")
        right_page.setMinimumWidth(480)
        right_layout = QVBoxLayout(right_page)
        right_layout.setContentsMargins(10, 8, 10, 10)
        right_layout.setSpacing(8)
        self.right_character_header = AnalysisCharacterHeader(
            "めたみ",
            "メタデータ解析\n編集ツール",
            "metami.png",
            "right",
        )
        database_state = (
            "error"
            if database_error
            else ("online" if self.database is not None else "offline")
        )
        self.analysis_status = AnalysisStatusPanel(database_state)
        self.console_status = self.analysis_status.database_label
        self.rating_appeal = RatingAppealPanel(
            self.metadata_pane.rating_control
        )
        self.rating_appeal.set_prompt(random.choice(RATING_PROMPT_MESSAGES))
        self.right_character_header.add_accessory(self.analysis_status)
        self.right_character_header.add_accessory(self.rating_appeal)
        right_layout.addWidget(self.right_character_header)
        right_layout.addWidget(self.detail_splitter)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("pageSplitter")
        self.splitter.setHandleWidth(8)
        self.splitter.addWidget(self.file_list_pane)
        self.splitter.addWidget(right_page)
        self.splitter.setSizes(PAGE_SPLITTER_SIZES)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)

        central = QWidget()
        central.setObjectName("applicationWorkspace")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addWidget(self.splitter)
        self.setCentralWidget(central)
        self._create_menus()
        self.status_icon = QLabel()
        self.status_icon.setObjectName("statusIcon")
        self.statusBar().addWidget(self.status_icon)
        if database_error:
            self._set_status(
                f"準備完了（ユーザー情報DBの一部処理でエラー: {database_error}）",
                "error",
            )
        else:
            self._set_status("準備完了", "ready")

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("ファイル(&F)")
        self.file_menu = file_menu
        select_file = file_menu.addAction("ファイルを開く(&O)...")
        select_file.triggered.connect(self._select_file)
        select_folder = file_menu.addAction("フォルダを開く(&D)...")
        select_folder.triggered.connect(self._select_folder)
        recent_folder = file_menu.addAction("最近開いたフォルダ")
        recent_folder.setEnabled(False)
        recent_folder.setToolTip("今後追加予定")
        missing_items = file_menu.addAction("見つからない項目を確認・整理")
        missing_items.setEnabled(False)
        missing_items.setToolTip("今後追加予定")
        file_menu.addSeparator()
        exit_action = file_menu.addAction("終了(&X)")
        exit_action.triggered.connect(self.close)
        view_menu = self.menuBar().addMenu("表示(&V)")
        self.view_menu = view_menu
        tab_settings = view_menu.addAction("表示タブの設定")
        tab_settings.triggered.connect(self._show_display_tab_settings)
        reset_tabs = view_menu.addAction("表示を初期状態に戻す")
        reset_tabs.triggered.connect(self._reset_display_layout)
        help_menu = self.menuBar().addMenu("ヘルプ(&H)")
        self.help_menu = help_menu
        about = help_menu.addAction("METAMIについて")
        about.triggered.connect(self._show_about)
        version = help_menu.addAction("バージョン情報")
        version.triggered.connect(self._show_version)
        creation_notes = help_menu.addMenu("作成メモ")
        self.creation_notes_menu = creation_notes
        ltx_video_tips = creation_notes.addAction("LTX動画作成メモ")
        ltx_video_tips.triggered.connect(self._show_ltx_video_tips)

    def _show_display_tab_settings(self) -> None:
        dialog = DisplayTabSettingsDialog(
            self.metadata_pane.optional_tab_visibility(), self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        for key, visible in dialog.visibility().items():
            self.metadata_pane.set_optional_tab_visible(key, visible)

    def _reset_display_layout(self) -> None:
        """ウィンドウ内容を保ったまま左右・上下分割だけを既定値へ戻す。"""
        self.splitter.setSizes(PAGE_SPLITTER_SIZES)
        self.detail_splitter.setSizes(DETAIL_SPLITTER_SIZES)

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            "METAMIについて",
            "M.E.T.A.M.I.は、画像・動画のメタデータを確認し、"
            "評価・タグ・メモで整理するアプリです。",
        )

    def _show_version(self) -> None:
        QMessageBox.information(
            self, "バージョン情報", "METAMI Ver1.0.2"
        )

    def _show_ltx_video_tips(self) -> None:
        dialog = LtxVideoTipsDialog(self)
        dialog.exec()

    def _select_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "ファイルを選択",
            "",
            "対応ファイル (*.png *.webp *.jpg *.jpeg *.JPG *.JPEG *.mp4);;"
            "PNGファイル (*.png);;WEBPファイル (*.webp);;"
            "JPEGファイル (*.jpg *.jpeg *.JPG *.JPEG);;"
            "MP4ファイル (*.mp4);;すべてのファイル (*)",
        )
        if selected:
            self._accept_path(Path(selected))

    def _select_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "フォルダを選択")
        if selected:
            self._accept_path(Path(selected))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime_data = event.mimeData()
        urls = mime_data.urls() if mime_data.hasUrls() else []
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        try:
            mime_data = event.mimeData()
            urls = mime_data.urls() if mime_data.hasUrls() else []
            if len(urls) != 1 or not urls[0].isLocalFile():
                self._show_input_error(
                    "ファイルまたはフォルダを1つだけドロップしてください。"
                )
                event.ignore()
                return
            path = Path(urls[0].toLocalFile())
            if not path.exists():
                self._show_input_error(
                    "ドロップされたパスが見つかりません。別の項目を選んでください。"
                )
                event.ignore()
                return
            self._accept_path(path)
            event.acceptProposedAction()
        except (OSError, ValueError):
            self._show_input_error(
                "入力を読み取れませんでした。別のファイルまたはフォルダをお試しください。"
            )
            event.ignore()

    def _accept_path(self, path: Path) -> None:
        if not self._resolve_unsaved_memo():
            return
        if path.is_file():
            self._set_paths([path])
            return
        try:
            paths = sorted(
                (
                    item
                    for item in path.iterdir()
                    if item.is_file() and item.suffix.lower() in SUPPORTED_SUFFIXES
                ),
                key=lambda item: item.name.casefold(),
            )
        except OSError:
            self._show_input_error(
                "フォルダを読み取れませんでした。アクセス権を確認してください。"
            )
            return
        display_paths = self._set_paths(paths, source_directory=path)
        if not display_paths:
            self.preview_pane.clear(
                "対応するPNG、WEBP、JPG/JPEG、MP4がありません。"
            )
            self.metadata_pane.clear()
            self._set_status(f"対象ファイル 0件: {path.resolve()}", "ready")

    def _set_paths(
        self, paths: list[Path], source_directory: Path | None = None
    ) -> list[Path]:
        """一覧を更新し、利用可能なら識別情報をDBへ記録する。"""
        display_paths = list(paths)
        database_message = ""
        ratings: dict[str, int] = {}
        tags_by_path: dict[str, list[str]] = {}
        all_tags: list[str] = []
        tag_usage_counts: dict[str, int] = {}
        memo_paths: set[str] = set()
        memos_by_path: dict[str, str] = {}
        titles_by_path: dict[str, str] = {}
        missing_paths: set[str] = set()
        if self.database is not None:
            try:
                self.database.register_files(paths)
                missing_states = self.database.check_registered_files()
                if source_directory is not None:
                    known_paths = self.database.get_registered_paths_in_directory(
                        source_directory
                    )
                    by_path = {
                        self._path_key(path): path for path in display_paths
                    }
                    for known_path in known_paths:
                        if known_path.suffix.lower() in SUPPORTED_SUFFIXES:
                            by_path.setdefault(
                                self._path_key(known_path), known_path
                            )
                    display_paths = sorted(
                        by_path.values(), key=lambda path: path.name.casefold()
                    )
                missing_paths = {
                    path.casefold()
                    for path, missing in missing_states.items()
                    if missing
                }
                ratings = {
                    path.casefold(): rating
                    for path, rating in self.database.get_file_ratings().items()
                }
                tags_by_path = {
                    path.casefold(): tags
                    for path, tags in self.database.get_tags_by_path().items()
                }
                tag_usage_counts = self.database.get_tag_usage_counts()
                all_tags = list(tag_usage_counts)
                memos_by_path = {
                    path.casefold(): memo
                    for path, memo in self.database.get_memos_by_path().items()
                }
                memo_paths = set(memos_by_path)
                titles_by_path = {
                    path.casefold(): title
                    for path, title
                    in self.database.get_user_titles_by_path().items()
                }
                self._set_database_state("online")
            except DatabaseError as error:
                database_message = str(error)
                self._set_database_state("error")
        self.file_list_pane.set_paths(
            display_paths,
            ratings=ratings,
            tags_by_path=tags_by_path,
            all_tags=all_tags,
            tag_usage_counts=tag_usage_counts,
            memo_paths=memo_paths,
            memos_by_path=memos_by_path,
            titles_by_path=titles_by_path,
            missing_paths=missing_paths,
        )
        if database_message:
            self._set_status(
                f"ユーザー情報DBへの保存をスキップしました: {database_message}",
                "error",
            )
        return display_paths

    def _save_rating(
        self, path_text: str, rating: int, previous_rating: int
    ) -> None:
        path = Path(path_text)
        if self.database is None:
            self.file_list_pane.set_rating(path, previous_rating)
            if self._is_current_path(path):
                self.metadata_pane.set_rating(previous_rating)
            self._set_status(
                "ユーザー情報DBを使用できないため、星ランクを保存できません。",
                "error",
            )
            return
        try:
            self.database.set_rating(path, rating)
        except DatabaseError as error:
            self._set_database_state("error")
            self.file_list_pane.set_rating(path, previous_rating)
            if self._is_current_path(path):
                self.metadata_pane.set_rating(previous_rating)
            self._set_status(f"星ランクを保存できません: {error}", "error")
            return
        self.file_list_pane.set_rating(path, rating)
        if self._is_current_path(path):
            self.metadata_pane.set_rating(rating)
            self._refresh_missing_preview()
            if rating:
                self._cancel_rating_nudge()
            elif previous_rating:
                self._schedule_rating_nudge(path)
        self._set_database_state("online")
        label = f"星{rating}" if rating else "星なし"
        self._set_status(f"{label}に変更しました: {path.resolve()}", "active")

    def _save_current_rating(self, rating: int, previous_rating: int) -> None:
        if self._current_path is None:
            self.metadata_pane.set_rating(0, False)
            return
        self._save_rating(str(self._current_path), rating, previous_rating)

    def _add_current_tag(self, name: str) -> None:
        if self._current_path is None:
            return
        if self.database is None:
            self._set_status(
                "ユーザー情報DBを使用できないため、タグを追加できません。",
                "error",
            )
            return
        try:
            display_name, added = self.database.add_tag(self._current_path, name)
            self._refresh_tags(self._current_path)
        except DatabaseError as error:
            self._set_database_state("error")
            self._set_status(f"タグを追加できません: {error}", "error")
            return
        self.metadata_pane.tag_editor.clear_input()
        if added:
            self._set_status(
                f"タグ「{display_name}」を追加しました。", "active"
            )
        else:
            self._set_status(
                f"タグ「{display_name}」は既に登録されています。", "ready"
            )
        self._set_database_state("online")

    def _remove_current_tag(self, name: str) -> None:
        if self._current_path is None or self.database is None:
            self._set_status("タグを削除できません。", "error")
            return
        path = self._current_path
        try:
            removed = self.database.remove_tag(path, name)
            self._refresh_tags(path)
        except DatabaseError as error:
            self._set_database_state("error")
            self._set_status(f"タグを削除できません: {error}", "error")
            return
        if removed:
            self._set_status(f"タグ「{name}」を外しました。", "active")
        self._set_database_state("online")

    def _refresh_tags(self, path: Path) -> None:
        if self.database is None:
            return
        tags = self.database.get_file_tags(path)
        tag_usage_counts = self.database.get_tag_usage_counts()
        self.file_list_pane.set_tags(
            path, tags, list(tag_usage_counts), tag_usage_counts
        )
        if self._is_current_path(path):
            self.metadata_pane.set_tags(tags)
            self._refresh_missing_preview()

    def _show_file(self, path_text: str) -> None:
        path = Path(path_text)
        if self._ignore_next_file_signal:
            self._ignore_next_file_signal = False
            return
        if (
            self._current_path is not None
            and self._same_path(path, self._current_path)
            and self.metadata_pane.is_organizer_dirty()
        ):
            return
        if (
            self._current_path is not None
            and not self._same_path(path, self._current_path)
            and not self._resolve_unsaved_memo()
        ):
            self._restore_current_selection()
            return
        self._cancel_rating_nudge()
        self._current_path = path
        path_key = self._path_key(path)
        if path_key != self._rating_appeal_path_key:
            self.rating_appeal.set_prompt(
                random.choice(RATING_PROMPT_MESSAGES)
            )
            self._rating_appeal_path_key = path_key
        self.metadata_pane.set_rating(self.file_list_pane.get_rating(path))
        self.metadata_pane.set_tags(self.file_list_pane.get_tags(path))
        memo_error = ""
        memo = ""
        title = ""
        if self.database is not None:
            try:
                memo = self.database.get_memo(path)
                title = self.database.get_user_title(path)
            except DatabaseError as error:
                memo_error = str(error)
                self._set_database_state("error")
        self.metadata_pane.set_user_title(
            title, enabled=self.database is not None and not memo_error
        )
        self.metadata_pane.set_memo(
            memo, enabled=self.database is not None and not memo_error
        )
        if not path.is_file():
            self._refresh_missing_preview()
            self.metadata_pane.show_error(
                "登録済みの原本ファイルが見つかりません。"
            )
            self._set_status(
                f"原本ファイルが見つかりません: {path}", "error"
            )
            return
        self.preview_pane.show_file(path)
        self._schedule_rating_nudge(path)
        try:
            data = build_display_data(path)
        except DisplayDataError as error:
            self.metadata_pane.show_error(str(error))
            self._set_status(f"読取エラー: {path.resolve()}", "error")
            return
        self.metadata_pane.set_data(data)
        if memo_error:
            self._set_status(f"メモを読み込めません: {memo_error}", "error")
        else:
            self._set_status(f"{data.status}: {path.resolve()}", "active")

    def _show_input_error(self, message: str) -> None:
        if not self._resolve_unsaved_memo():
            return
        self._current_path = None
        self._cancel_rating_nudge()
        self.file_list_pane.clear()
        self.preview_pane.clear("入力を受け付けられませんでした。")
        self.metadata_pane.show_error(message)
        self._set_status(f"エラー: {message}", "error")

    def _show_no_search_results(self, message: str) -> None:
        if not self._resolve_unsaved_memo():
            return
        self._current_path = None
        self._cancel_rating_nudge()
        self.preview_pane.clear(message)
        self.metadata_pane.clear()
        self._set_status(message, "ready")

    def _is_current_path(self, path: Path) -> bool:
        if self._current_path is None:
            return False
        try:
            return path.resolve() == self._current_path.resolve()
        except OSError:
            return path.absolute() == self._current_path.absolute()

    @staticmethod
    def _same_path(first: Path, second: Path) -> bool:
        try:
            return first.resolve() == second.resolve()
        except OSError:
            return first.absolute() == second.absolute()

    @staticmethod
    def _path_key(path: Path) -> str:
        try:
            return str(path.resolve()).casefold()
        except OSError:
            return str(path.absolute()).casefold()

    def _save_current_organizer(self, title: str, memo: str) -> bool:
        if self._current_path is None or self.database is None:
            self._set_status("タイトルとメモを保存できません。", "error")
            return False
        path = self._current_path
        try:
            normalized_title = self.database.set_user_details(
                path, title, memo
            )
        except (DatabaseError, ValueError) as error:
            if isinstance(error, DatabaseError):
                self._set_database_state("error")
            self._set_status(
                f"タイトルとメモを保存できません: {error}", "error"
            )
            return False
        self.metadata_pane.mark_organizer_saved(normalized_title)
        self.file_list_pane.set_user_title(path, normalized_title)
        self.file_list_pane.set_memo(path, memo)
        self._set_database_state("online")
        self._refresh_missing_preview()
        self._set_status(
            f"タイトルとメモを保存しました: {path.resolve()}",
            "active",
        )
        return True

    def _save_current_memo(self, memo: str) -> bool:
        """旧内部呼出しとの互換用。現在のタイトルと一緒に保存する。"""
        return self._save_current_organizer(
            self.metadata_pane.user_title_text(), memo
        )

    def _refresh_missing_preview(self) -> None:
        """現在選択中がmissingなら、DBに保存された最新情報を表示する。"""
        path = self._current_path
        if path is None or path.is_file() or self.database is None:
            return
        try:
            details = self.database.get_missing_file_details(path)
        except DatabaseError as error:
            self._set_database_state("error")
            self.preview_pane.clear("原本ファイルが見つかりません。")
            self._set_status(f"missing記録を読み込めません: {error}", "error")
            return
        self.preview_pane.show_missing(
            filename=details.filename,
            path=details.path,
            last_checked_at=details.last_checked_at,
            rating=details.rating,
            tags=details.tags,
            has_memo=details.has_memo,
        )

    def _locate_missing_file(self) -> None:
        """ユーザーが選んだ代替ファイルへ、同じDB記録を付け替える。"""
        old_path = self._current_path
        if old_path is None or old_path.is_file() or self.database is None:
            self._set_status("関連付け対象のmissing記録がありません。", "error")
            return
        if not self._resolve_unsaved_memo():
            return
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "代替ファイルを選択",
            "",
            "対応ファイル (*.png *.webp *.jpg *.jpeg *.JPG *.JPEG *.mp4);;"
            "すべてのファイル (*)",
        )
        if not selected:
            return
        new_path = Path(selected)
        if not new_path.is_file() or new_path.suffix.lower() not in SUPPORTED_SUFFIXES:
            QMessageBox.warning(
                self,
                "ファイルを関連付けできません",
                "PNG、WEBP、MP4の実在するファイルを選択してください。",
            )
            return
        answer = QMessageBox.question(
            self,
            "新しいパスへ関連付け",
            "次のファイルを、見つからない記録の新しい原本として関連付けますか？\n\n"
            f"以前: {old_path}\n新しいファイル: {new_path}\n\n"
            "保存済みのタイトル、評価、タグ、メモは維持されます。\n"
            "METAMIは原本ファイルを変更しません。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.database.relink_missing_file(old_path, new_path)
        except DatabaseError as error:
            self._set_database_state("error")
            QMessageBox.warning(self, "関連付けに失敗しました", str(error))
            self._set_status(f"新しいパスへ関連付けできません: {error}", "error")
            return
        paths = [
            new_path if self._same_path(path, old_path) else path
            for path in self.file_list_pane.all_paths()
        ]
        self._current_path = None
        self._set_database_state("online")
        self._set_paths(paths)
        self.file_list_pane.select_path(new_path)
        self._set_status(f"新しいパスへ関連付けました: {new_path}", "active")

    def _delete_missing_record(self) -> None:
        """missing記録だけを、明示確認後にトランザクションで削除する。"""
        path = self._current_path
        if path is None or path.is_file() or self.database is None:
            self._set_status("削除対象のmissing記録がありません。", "error")
            return
        if not self._resolve_unsaved_memo():
            return
        answer = QMessageBox.question(
            self,
            "METAMIの記録を削除",
            "このmissing記録を削除しますか？\n\n"
            "METAMI内のタイトル、評価、タグとの関連、メモ、"
            "パス履歴を削除します。\n"
            "原本の画像・動画ファイルは操作しません。\n\n"
            f"対象: {path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.database.delete_missing_record(path)
        except DatabaseError as error:
            self._set_database_state("error")
            QMessageBox.warning(self, "記録を削除できません", str(error))
            self._set_status(f"METAMIの記録を削除できません: {error}", "error")
            return
        remaining = [
            candidate
            for candidate in self.file_list_pane.all_paths()
            if not self._same_path(candidate, path)
        ]
        self._current_path = None
        self._set_database_state("online")
        self._set_paths(remaining)
        if not remaining:
            self.preview_pane.clear("ファイルを選択してください。")
            self.metadata_pane.clear()
        self._set_status("METAMI内のmissing記録を削除しました。", "active")

    def _resolve_unsaved_memo(self) -> bool:
        if not self.metadata_pane.is_organizer_dirty():
            return True
        choice = self._ask_unsaved_memo()
        if choice == "save":
            return self._save_current_organizer(
                self.metadata_pane.user_title_text(),
                self.metadata_pane.memo_text(),
            )
        if choice == "discard":
            self.metadata_pane.revert_organizer()
            return True
        return False

    def _ask_unsaved_memo(self) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("未保存の変更")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("タイトルまたはメモが保存されていません。")
        box.setInformativeText("保存してから移動しますか？")
        save = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("破棄", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return "save"
        if clicked is discard:
            return "discard"
        if clicked is cancel:
            return "cancel"
        return "cancel"

    def _restore_current_selection(self) -> None:
        if self._current_path is None:
            return
        self._ignore_next_file_signal = True
        restored = self.file_list_pane.select_path(self._current_path)
        if not restored:
            self._ignore_next_file_signal = False

    def _set_status(self, message: str, state: str = "ready") -> None:
        self.status_icon.setPixmap(status_pixmap(state))
        self.statusBar().showMessage(message)

    def _set_database_state(self, state: str) -> None:
        self.analysis_status.set_state(state)

    def _schedule_rating_nudge(self, path: Path) -> None:
        """未評価素材に対し、形式に応じた案内開始条件を設定する。"""
        self._rating_prompt_timer.stop()
        self._rating_prompt_pending = False
        self.rating_appeal.clear_nudge()
        if (
            not path.is_file()
            or self.file_list_pane.get_rating(path) > 0
            or self._path_key(path) in self._rating_prompted_paths
        ):
            return
        if path.suffix.lower() != ".mp4":
            self._rating_prompt_timer.start(RATING_IMAGE_DELAY_MS)

    def _request_rating_nudge(self) -> None:
        """条件を満たした未評価素材へ、起動中1回だけ短文を表示する。"""
        path = self._current_path
        if (
            path is None
            or not path.is_file()
            or self.file_list_pane.get_rating(path) > 0
        ):
            self._rating_prompt_pending = False
            return
        key = self._path_key(path)
        if key in self._rating_prompted_paths:
            self._rating_prompt_pending = False
            return
        if (
            self.preview_pane.is_video_playing()
            or self.metadata_pane.is_organizer_editing()
        ):
            self._rating_prompt_pending = True
            self._rating_prompt_timer.start(RATING_RETRY_DELAY_MS)
            return
        message_index = sum(path.name.encode("utf-8")) % len(
            RATING_NUDGE_MESSAGES
        )
        self.rating_appeal.show_nudge(
            RATING_NUDGE_MESSAGES[message_index]
        )
        self._rating_prompted_paths.add(key)
        self._rating_prompt_pending = False

    def _show_pending_rating_nudge(self) -> None:
        if self._rating_prompt_pending:
            self._request_rating_nudge()

    def _cancel_rating_nudge(self) -> None:
        self._rating_prompt_timer.stop()
        self._rating_prompt_pending = False
        self.rating_appeal.clear_nudge()

    def closeEvent(self, event) -> None:
        if not self._resolve_unsaved_memo():
            event.ignore()
            return
        self.file_list_pane.release_media()
        self.preview_pane.release_media()
        self._rating_prompt_timer.stop()
        super().closeEvent(event)
