"""3つの独立ペインを接続するメインウィンドウ。"""

from __future__ import annotations

import os
import random
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, QThread, QTimer, Qt, QUrl
from PySide6.QtGui import (
    QActionGroup,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QPainter,
    QPen,
)
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
from metadata.media_finder import (
    MediaSearchError,
    SUPPORTED_SUFFIXES,
    find_media_files,
)
from storage.database import DatabaseError, MetadataDatabase
from storage.file_hash import (
    HashStatus,
    SUPPORTED_HASH_ALGORITHMS,
    calculate_file_hash,
    is_bulk_registration_status,
    is_valid_hash,
)
from storage.data_management import (
    DataManagementError,
    backup_database,
    get_database_info,
    restore_database,
    timestamped_database_name,
    validate_metami_database,
    validate_restore_source,
    with_database_suffix,
)
from ui.data_management_dialog import (
    DatabaseInfoDialog,
    DataManagementHelpDialog,
)
from ui.file_list_pane import (
    LANDSCAPE_VIEW,
    THUMBNAIL_VIEW,
    FileListPane,
)
from ui.file_identity_dialogs import (
    AUTO_IMAGES_ONLY,
    AUTO_OFF,
    AUTO_REGISTRATION_KEY,
    IMAGE_SUFFIXES,
    VIDEO_SUFFIXES,
    AutoRegistrationSettingsDialog,
    BulkRegistrationDialog,
    FileIdentityRegistrationWorker,
    IdentityStatusDialog,
    RegistrationProgress,
    RegistrationProgressDialog,
    RegistrationSummary,
    automatic_image_candidates,
    can_register_status,
    can_reregister_status,
    normalized_auto_registration_mode,
    requires_large_registration_confirmation,
)
from ui.decorations import (
    status_pixmap,
)
from ui.metadata_pane import MetadataPane
from ui.missing_items_dialog import MissingItemsDialog
from ui.ltx_video_tips_dialog import LtxVideoTipsDialog
from ui.preview_pane import PreviewPane
from ui.reuse_candidates_dialog import (
    ReuseCandidateDetectionWorker,
    ReuseCandidatesDialog,
    UserDataCopyProgressDialog,
    UserDataCopyResultDialog,
    UserDataCopyWorker,
)
from storage.user_data_candidate_detection import detect_reuse_candidates
from storage.user_data_candidate_detection import ReuseCandidateResult
from storage.user_data_reuse import CopyDecision
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
RECENT_FOLDERS_KEY = "folders/recent"
INCLUDE_SUBFOLDERS_KEY = "folders/includeSubfolders"
CARD_VIEW_MODE_KEY = "display/cardViewMode"
MAX_RECENT_FOLDERS = 5
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
        settings: QSettings | None = None,
    ) -> None:
        super().__init__()
        self.database = database
        self.database_error = database_error
        self.settings = settings or QSettings("meTalia-jp", "METAMI")
        self._recent_folders = self._load_recent_folders()
        self._include_subfolders = self._setting_bool(
            INCLUDE_SUBFOLDERS_KEY, False
        )
        saved_view_mode = str(
            self.settings.value(CARD_VIEW_MODE_KEY, THUMBNAIL_VIEW)
        )
        self._card_view_mode = (
            saved_view_mode
            if saved_view_mode in {THUMBNAIL_VIEW, LANDSCAPE_VIEW}
            else THUMBNAIL_VIEW
        )
        self._current_folder: Path | None = None
        self._current_path: Path | None = None
        self._current_display_paths: tuple[Path, ...] = ()
        self._data_operation_in_progress = False
        self._identity_auto_mode = normalized_auto_registration_mode(
            self.settings.value(AUTO_REGISTRATION_KEY, AUTO_IMAGES_ONLY)
        )
        self._identity_thread: QThread | None = None
        self._identity_worker: FileIdentityRegistrationWorker | None = None
        self._identity_progress_dialog: RegistrationProgressDialog | None = None
        self._identity_processing_paths: set[str] = set()
        self._identity_exit_pending = False
        self._identity_operation_automatic = False
        self._identity_successful_file_ids: tuple[int, ...] = ()
        self._reuse_detection_thread: QThread | None = None
        self._reuse_detection_worker: ReuseCandidateDetectionWorker | None = None
        self._reuse_notified_keys: set[tuple] = set()
        self._reuse_notification: QMessageBox | None = None
        self._reuse_candidates_dialog: ReuseCandidatesDialog | None = None
        self._reuse_exit_pending = False
        self._reuse_detection_stale = False
        self._reuse_pending_target_ids: set[int] = set()
        self._copy_thread: QThread | None = None
        self._copy_worker: UserDataCopyWorker | None = None
        self._copy_progress_dialog: UserDataCopyProgressDialog | None = None
        self._copy_result_dialog: UserDataCopyResultDialog | None = None
        self._copy_exit_pending = False
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
        self.file_list_pane.set_view_mode(self._card_view_mode)
        self.file_list_pane.set_reload_action(self.reload_folder_action)
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
        self.reload_folder_action = file_menu.addAction(
            "現在のフォルダを再読み込み"
        )
        self.reload_folder_action.setShortcut("F5")
        self.reload_folder_action.setEnabled(False)
        self.reload_folder_action.triggered.connect(
            self._reload_current_folder
        )
        self.recent_folders_menu = file_menu.addMenu("最近開いたフォルダ")
        self._refresh_recent_folders_menu()
        file_menu.addSeparator()
        self.include_subfolders_action = file_menu.addAction(
            "サブフォルダも含む"
        )
        self.include_subfolders_action.setCheckable(True)
        self.include_subfolders_action.setChecked(self._include_subfolders)
        self.include_subfolders_action.toggled.connect(
            self._set_include_subfolders
        )
        self.missing_items_action = file_menu.addAction(
            "見つからない項目を確認・整理"
        )
        self.missing_items_action.setEnabled(self.database is not None)
        self.missing_items_action.triggered.connect(self._show_missing_items)
        file_menu.addSeparator()
        exit_action = file_menu.addAction("終了(&X)")
        exit_action.triggered.connect(self.close)
        view_menu = self.menuBar().addMenu("表示(&V)")
        self.view_menu = view_menu
        card_view_menu = view_menu.addMenu("表示形式")
        self.card_view_menu = card_view_menu
        self.card_view_group = QActionGroup(self)
        self.card_view_group.setExclusive(True)
        self.thumbnail_card_action = card_view_menu.addAction(
            "サムネイルカード"
        )
        self.landscape_card_action = card_view_menu.addAction(
            "ランドスケープカード"
        )
        for action in (
            self.thumbnail_card_action,
            self.landscape_card_action,
        ):
            action.setCheckable(True)
            self.card_view_group.addAction(action)
        self.thumbnail_card_action.setChecked(
            self._card_view_mode == THUMBNAIL_VIEW
        )
        self.landscape_card_action.setChecked(
            self._card_view_mode == LANDSCAPE_VIEW
        )
        self.thumbnail_card_action.triggered.connect(
            lambda checked: checked
            and self._set_card_view_mode(THUMBNAIL_VIEW)
        )
        self.landscape_card_action.triggered.connect(
            lambda checked: checked
            and self._set_card_view_mode(LANDSCAPE_VIEW)
        )
        view_menu.addSeparator()
        tab_settings = view_menu.addAction("表示タブの設定")
        tab_settings.triggered.connect(self._show_display_tab_settings)
        reset_tabs = view_menu.addAction("表示を初期状態に戻す")
        reset_tabs.triggered.connect(self._reset_display_layout)
        data_menu = self.menuBar().addMenu("データ管理(&D)")
        self.data_menu = data_menu
        self.backup_data_action = data_menu.addAction(
            "METAMIデータをバックアップ…"
        )
        self.restore_data_action = data_menu.addAction(
            "METAMIデータを復元…"
        )
        data_menu.addSeparator()
        identity_menu = data_menu.addMenu("ファイル識別情報")
        self.identity_menu = identity_menu
        self.identity_settings_action = identity_menu.addAction("登録の設定…")
        identity_menu.addSeparator()
        self.identity_register_action = identity_menu.addAction(
            "選択中のファイルを登録"
        )
        self.identity_reregister_action = identity_menu.addAction(
            "選択中のファイルを再登録"
        )
        self.identity_bulk_action = identity_menu.addAction(
            "フォルダ内の未登録項目を登録…"
        )
        self.identity_status_action = identity_menu.addAction("登録状況…")
        data_menu.addSeparator()
        self.open_data_location_action = data_menu.addAction(
            "データ保存場所を開く"
        )
        self.database_info_action = data_menu.addAction(
            "データベース情報…"
        )
        data_actions = (
            self.backup_data_action,
            self.restore_data_action,
            self.identity_settings_action,
            self.identity_register_action,
            self.identity_reregister_action,
            self.identity_bulk_action,
            self.identity_status_action,
            self.open_data_location_action,
            self.database_info_action,
        )
        for action in data_actions:
            action.setEnabled(self.database is not None)
        self.backup_data_action.triggered.connect(self._backup_metami_data)
        self.restore_data_action.triggered.connect(self._restore_metami_data)
        self.identity_settings_action.triggered.connect(
            self._show_identity_registration_settings
        )
        self.identity_register_action.triggered.connect(
            self._register_selected_identity
        )
        self.identity_reregister_action.triggered.connect(
            self._reregister_selected_identity
        )
        self.identity_bulk_action.triggered.connect(
            self._register_folder_identities
        )
        self.identity_status_action.triggered.connect(
            self._show_identity_status
        )
        self.open_data_location_action.triggered.connect(
            self._open_data_location
        )
        self.database_info_action.triggered.connect(
            self._show_database_info
        )
        self._update_identity_actions()
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
        data_help = help_menu.addAction(
            "METAMIデータのバックアップと復元"
        )
        data_help.triggered.connect(self._show_data_management_help)

    def _show_display_tab_settings(self) -> None:
        dialog = DisplayTabSettingsDialog(
            self.metadata_pane.optional_tab_visibility(), self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        for key, visible in dialog.visibility().items():
            self.metadata_pane.set_optional_tab_visible(key, visible)

    def _reset_display_layout(self) -> None:
        """分割位置とカード表示形式を初期状態へ戻す。"""
        self.splitter.setSizes(PAGE_SPLITTER_SIZES)
        self.detail_splitter.setSizes(DETAIL_SPLITTER_SIZES)
        self._set_card_view_mode(THUMBNAIL_VIEW)

    def _set_card_view_mode(self, mode: str) -> None:
        self._card_view_mode = mode
        self.thumbnail_card_action.setChecked(mode == THUMBNAIL_VIEW)
        self.landscape_card_action.setChecked(mode == LANDSCAPE_VIEW)
        self.file_list_pane.set_view_mode(mode)
        self.settings.setValue(CARD_VIEW_MODE_KEY, mode)
        self.settings.sync()

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            "METAMIについて",
            "M.E.T.A.M.I.は、画像・動画のメタデータを確認し、"
            "評価・タグ・メモで整理するアプリです。",
        )

    def _show_version(self) -> None:
        QMessageBox.information(
            self,
            "バージョン情報",
            "METAMI Ver1.1.0\n\n"
            "同じ内容のファイルに登録された以前のMETAMIデータを確認し、\n"
            "現在のフォルダへ一括コピーできるようになりました。",
        )

    def _show_ltx_video_tips(self) -> None:
        dialog = LtxVideoTipsDialog(self)
        dialog.exec()

    def _show_data_management_help(self) -> None:
        dialog = DataManagementHelpDialog(self)
        dialog.exec()

    def _backup_metami_data(self) -> None:
        database = self.database
        if database is None:
            self._show_data_management_error(
                "METAMIデータを利用できないため、バックアップできません。"
            )
            return
        QMessageBox.information(
            self,
            "METAMIデータのバックアップ",
            "METAMIデータには、タイトル、星評価、タグ、メモなどの\n"
            "登録情報が保存されています。\n\n"
            "画像・動画ファイル本体はバックアップ対象に含まれません。",
        )
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "METAMIデータのバックアップ先を選択",
            timestamped_database_name(),
            "METAMIデータベース (*.db);;すべてのファイル (*.*)",
        )
        if not selected:
            return
        destination = with_database_suffix(Path(selected))
        self._set_status("METAMIデータを確認しています…", "active")
        self._set_data_operation_active(True)
        try:
            self._set_status("バックアップを作成しています…", "active")
            saved_path = backup_database(database.path, destination)
        except DataManagementError as error:
            self._set_status("バックアップを作成できませんでした", "error")
            QMessageBox.warning(
                self,
                "バックアップに失敗しました",
                "METAMIデータのバックアップ作成に失敗しました。\n\n"
                f"保存先:\n{destination}\n\n"
                "元のMETAMIデータは変更されていません。\n\n"
                f"詳細:\n{error}",
            )
            return
        finally:
            self._set_data_operation_active(False)
        self._set_status("バックアップが完了しました", "active")
        QMessageBox.information(
            self,
            "バックアップ完了",
            "METAMIデータのバックアップを作成しました。\n\n"
            f"保存先:\n{saved_path}\n\n"
            "このバックアップには、タイトル、評価、タグ、メモなどの\n"
            "METAMI登録情報が含まれます。\n"
            "画像・動画ファイル本体は含まれません。",
        )

    def _restore_metami_data(self) -> None:
        database = self.database
        if database is None:
            self._show_data_management_error(
                "METAMIデータを利用できないため、復元できません。"
            )
            return
        if not self._resolve_unsaved_memo():
            return
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "復元するMETAMIデータを選択",
            "",
            "METAMIデータベース (*.db);;すべてのファイル (*.*)",
        )
        if not selected:
            return
        source = Path(selected)
        self._set_status("復元元を確認しています…", "active")
        try:
            validate_restore_source(source, database.path)
        except DataManagementError as error:
            self._set_status("復元元を確認できませんでした", "error")
            QMessageBox.warning(
                self,
                "復元できません",
                "選択したファイルは、METAMIデータとして確認できませんでした。\n"
                "復元は行われていません。\n\n"
                f"詳細:\n{error}",
            )
            return
        if not self._confirm_database_restore():
            self._set_status("復元をキャンセルしました", "ready")
            return

        selected_path = self._current_path
        self._set_data_operation_active(True)
        try:
            self._set_status("復元前データを退避しています…", "active")
            self._set_status("METAMIデータを復元しています…", "active")
            result = restore_database(source, database.path)
        except DataManagementError as error:
            try:
                validate_metami_database(database.path)
            except DataManagementError:
                self._set_database_state("error")
            else:
                self._set_database_state("online")
            self._set_status("METAMIデータを復元できませんでした", "error")
            QMessageBox.warning(
                self,
                "復元に失敗しました",
                f"{error}\n\n画像・動画ファイル本体は変更されていません。",
            )
            return
        finally:
            self._set_data_operation_active(False)

        self._set_database_state("online")
        self._reload_after_database_restore(selected_path)
        self._set_status("復元が完了しました", "active")
        QMessageBox.information(
            self,
            "復元完了",
            "METAMIデータを復元しました。\n\n"
            f"復元元:\n{result.source_path}\n\n"
            f"復元前のデータ:\n{result.before_restore_path}\n\n"
            "画像・動画ファイル本体は変更されていません。",
        )

    def _confirm_database_restore(self) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("METAMIデータの復元")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("METAMIデータを復元します。")
        box.setInformativeText(
            "現在のタイトル、星評価、タグ、メモなどの登録情報は、\n"
            "選択したバックアップの内容に置き換わります。\n\n"
            "画像・動画ファイル本体は変更されません。\n\n"
            "復元前に、現在のMETAMIデータを自動的に退避します。\n"
            "復元後は一覧と詳細表示を更新します。\n\n"
            "続行しますか？"
        )
        restore_button = box.addButton(
            "復元する", QMessageBox.ButtonRole.AcceptRole
        )
        cancel_button = box.addButton(
            "キャンセル", QMessageBox.ButtonRole.RejectRole
        )
        box.setDefaultButton(cancel_button)
        box.setEscapeButton(cancel_button)
        box.exec()
        return box.clickedButton() is restore_button

    def _reload_after_database_restore(
        self, selected_path: Path | None
    ) -> None:
        if self._current_folder is not None:
            folder = self._current_folder
            if self._open_folder(
                folder,
                add_to_history=False,
                resolve_unsaved=False,
                select_first=False,
            ):
                if (
                    selected_path is not None
                    and self.file_list_pane.select_path(selected_path)
                ):
                    return
            self._current_path = None
            self.preview_pane.clear("ファイルを選択してください。")
            self.metadata_pane.clear()
            return
        if selected_path is not None and selected_path.is_file():
            self._set_paths([selected_path], select_first=False)
            if self.file_list_pane.select_path(selected_path):
                return
        self._current_path = None
        self.file_list_pane.clear()
        self.preview_pane.clear("ファイルを選択してください。")
        self.metadata_pane.clear()

    def _open_data_location(self) -> None:
        database = self.database
        if database is None:
            self._show_data_management_error(
                "METAMIデータの保存場所を確認できません。"
            )
            return
        folder = database.path.parent
        if not folder.exists() or not folder.is_dir():
            self._show_data_management_error(
                "METAMIデータの保存フォルダが見つかりません。"
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve()))):
            self._show_data_management_error(
                "METAMIデータの保存場所を開けませんでした。"
            )

    def _show_database_info(self) -> None:
        database = self.database
        if database is None:
            self._show_data_management_error(
                "データベース情報を確認できません。"
            )
            return
        self._set_status("METAMIデータを確認しています…", "active")
        try:
            info = get_database_info(database.path)
        except DataManagementError as error:
            self._show_data_management_error(str(error))
            return
        self._set_status("データベース情報を確認しました", "ready")
        dialog = DatabaseInfoDialog(info, self)
        dialog.exec()

    def _set_data_operation_active(self, active: bool) -> None:
        self._data_operation_in_progress = active
        enabled = self.database is not None and not active
        for action in (
            self.backup_data_action,
            self.restore_data_action,
            self.open_data_location_action,
            self.database_info_action,
        ):
            action.setEnabled(enabled)

    def _show_data_management_error(self, message: str) -> None:
        self._set_status(message, "error")
        QMessageBox.warning(self, "データ管理", message)

    def _show_missing_items(self) -> None:
        if self.database is None:
            self._set_status(
                "ユーザー情報DBを使用できないため確認できません。", "error"
            )
            return
        dialog = MissingItemsDialog(self.database, self)
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
            self._open_folder(Path(selected))

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
        if path.is_file():
            if not self._resolve_unsaved_memo():
                return
            self._current_folder = None
            self.reload_folder_action.setEnabled(False)
            self._set_paths([path])
            return
        self._open_folder(path)

    def _open_folder(
        self,
        path: Path,
        *,
        add_to_history: bool = True,
        resolve_unsaved: bool = True,
        select_first: bool = True,
        include_registered_missing: bool = True,
    ) -> bool:
        """既存の一覧更新経路を使ってフォルダを開く。"""
        if not path.exists() or not path.is_dir():
            self._set_status(
                "指定されたフォルダが見つかりません。"
                "履歴からは削除していません。",
                "error",
            )
            return False
        if resolve_unsaved and not self._resolve_unsaved_memo():
            return False
        try:
            result = find_media_files(
                path, include_subfolders=self._include_subfolders
            )
        except MediaSearchError:
            self._show_input_error(
                "フォルダを読み取れませんでした。アクセス権を確認してください。"
            )
            return False
        self._current_folder = path
        self.reload_folder_action.setEnabled(True)
        if add_to_history:
            self._record_recent_folder(path)
        display_paths = self._set_paths(
            list(result.paths),
            source_directory=path,
            select_first=select_first,
            include_registered_missing=include_registered_missing,
        )
        if not display_paths:
            self.preview_pane.clear(
                "対応するPNG、WEBP、JPG/JPEG、MP4がありません。"
            )
            self.metadata_pane.clear()
            if result.unreadable_directories:
                self._set_status(
                    "対象ファイル 0件"
                    "（一部のフォルダを読み取れませんでした）",
                    "error",
                )
            else:
                self._set_status(
                    f"対象ファイル 0件: {path.resolve()}", "ready"
                )
        elif result.unreadable_directories:
            self._set_status(
                f"対象ファイル {len(display_paths)}件"
                "（一部のフォルダを読み取れませんでした）",
                "error",
            )
        return True

    def _reload_current_folder(self) -> None:
        """現在フォルダを履歴へ触れず、同じ探索設定で読み直す。"""
        folder = self._current_folder
        if folder is None:
            return
        try:
            folder_available = folder.is_dir()
        except OSError:
            folder_available = False
        if not folder_available:
            self._current_folder = None
            self.reload_folder_action.setEnabled(False)
            self._set_status(
                "現在のフォルダが見つかりません。"
                "フォルダが移動または削除された可能性があります。",
                "error",
            )
            return
        if not self._resolve_unsaved_memo():
            return
        selected_path = self._current_path
        if not self._open_folder(
            folder,
            add_to_history=False,
            resolve_unsaved=False,
            select_first=False,
            include_registered_missing=False,
        ):
            return
        if (
            selected_path is not None
            and selected_path.is_file()
            and self.file_list_pane.select_path(selected_path)
        ):
            return
        self.file_list_pane.select_path(Path(""))
        self._show_no_search_results(
            "フォルダを再読み込みしました。ファイルを選択してください。"
        )

    @staticmethod
    def _folder_key(path: str | Path) -> str:
        return os.path.normcase(
            os.path.normpath(os.path.abspath(path))
        ).casefold()

    def _setting_bool(self, key: str, default: bool) -> bool:
        value = self.settings.value(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() in {"1", "true", "yes", "on"}

    def _load_recent_folders(self) -> list[str]:
        value = self.settings.value(RECENT_FOLDERS_KEY, [])
        if isinstance(value, str):
            values = [value] if value else []
        else:
            values = [str(item) for item in (value or [])]
        unique: list[str] = []
        keys: set[str] = set()
        for item in values:
            key = self._folder_key(item)
            if key not in keys:
                keys.add(key)
                unique.append(os.path.normpath(item))
        return unique[:MAX_RECENT_FOLDERS]

    def _record_recent_folder(self, path: Path) -> None:
        display_path = os.path.normpath(os.path.abspath(path))
        key = self._folder_key(display_path)
        self._recent_folders = [
            item
            for item in self._recent_folders
            if self._folder_key(item) != key
        ]
        self._recent_folders.insert(0, display_path)
        del self._recent_folders[MAX_RECENT_FOLDERS:]
        self.settings.setValue(RECENT_FOLDERS_KEY, self._recent_folders)
        self.settings.sync()
        self._refresh_recent_folders_menu()

    def _refresh_recent_folders_menu(self) -> None:
        menu = self.recent_folders_menu
        menu.clear()
        if not self._recent_folders:
            empty = menu.addAction("履歴はありません")
            empty.setEnabled(False)
        else:
            for folder in self._recent_folders:
                action = menu.addAction(folder)
                action.setToolTip(folder)
                action.setStatusTip(folder)
                action.triggered.connect(
                    lambda checked=False, folder=folder:
                    self._open_folder(Path(folder))
                )
        menu.addSeparator()
        clear_action = menu.addAction("履歴を消去")
        clear_action.setEnabled(bool(self._recent_folders))
        clear_action.triggered.connect(self._clear_recent_folders)

    def _clear_recent_folders(self) -> None:
        self._recent_folders.clear()
        self.settings.remove(RECENT_FOLDERS_KEY)
        self.settings.sync()
        self._refresh_recent_folders_menu()

    def _set_include_subfolders(self, enabled: bool) -> None:
        previous = self._include_subfolders
        if self._current_folder is not None and not self._resolve_unsaved_memo():
            self.include_subfolders_action.blockSignals(True)
            self.include_subfolders_action.setChecked(previous)
            self.include_subfolders_action.blockSignals(False)
            return
        self._include_subfolders = enabled
        self.settings.setValue(INCLUDE_SUBFOLDERS_KEY, enabled)
        self.settings.sync()
        if self._current_folder is not None:
            self._open_folder(
                self._current_folder,
                add_to_history=False,
                resolve_unsaved=False,
            )

    def _set_paths(
        self,
        paths: list[Path],
        source_directory: Path | None = None,
        *,
        select_first: bool = True,
        include_registered_missing: bool = True,
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
        newly_registered: list[Path] = []
        if self.database is not None:
            try:
                newly_registered = self.database.register_files(paths)
                missing_states = self.database.check_registered_files()
                if source_directory is not None and include_registered_missing:
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
            select_first=select_first,
            source_directory=source_directory,
            ratings=ratings,
            tags_by_path=tags_by_path,
            all_tags=all_tags,
            tag_usage_counts=tag_usage_counts,
            memo_paths=memo_paths,
            memos_by_path=memos_by_path,
            titles_by_path=titles_by_path,
            missing_paths=missing_paths,
        )
        self._current_display_paths = tuple(display_paths)
        if database_message:
            self._set_status(
                f"ユーザー情報DBへの保存をスキップしました: {database_message}",
                "error",
            )
        elif newly_registered:
            self._maybe_auto_register_identities(newly_registered)
        return display_paths

    def _show_identity_registration_settings(self) -> None:
        dialog = AutoRegistrationSettingsDialog(self._identity_auto_mode, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_identity_auto_mode(dialog.selected_mode())
        label = (
            "新しく登録した画像を自動登録"
            if self._identity_auto_mode == AUTO_IMAGES_ONLY
            else "自動登録しない"
        )
        self._set_status(f"ファイル識別情報の設定を保存しました: {label}", "active")

    def _save_identity_auto_mode(self, mode: str) -> None:
        self._identity_auto_mode = normalized_auto_registration_mode(mode)
        self.settings.setValue(AUTO_REGISTRATION_KEY, self._identity_auto_mode)
        self.settings.sync()

    def _current_identity_status(self) -> HashStatus | None:
        if self.database is None or self._current_path is None:
            return None
        try:
            self.database.refresh_file_hash_status(self._current_path)
            stored = self.database.get_file_hash(self._current_path)
        except DatabaseError:
            return None
        return stored.status if stored is not None else None

    def _update_identity_actions(self) -> None:
        if not hasattr(self, "identity_register_action"):
            return
        status = self._current_identity_status()
        available = (
            self.database is not None
            and self._current_path is not None
            and self._current_path.is_file()
            and self._identity_thread is None
        )
        self.identity_register_action.setEnabled(
            available and can_register_status(status)
        )
        self.identity_reregister_action.setEnabled(
            available and can_reregister_status(status)
        )
        self.identity_bulk_action.setEnabled(
            self.database is not None
            and self._current_folder is not None
            and self._identity_thread is None
        )
        self.identity_settings_action.setEnabled(self.database is not None)
        self.identity_status_action.setEnabled(self.database is not None)

    def _register_selected_identity(self) -> None:
        path = self._current_path
        if path is None or not can_register_status(self._current_identity_status()):
            return
        self._start_identity_registration([path], "画像" if path.suffix.lower() in IMAGE_SUFFIXES else "動画")

    def _reregister_selected_identity(self) -> None:
        path = self._current_path
        if path is None or not can_reregister_status(self._current_identity_status()):
            return
        self._start_identity_registration([path], "画像" if path.suffix.lower() in IMAGE_SUFFIXES else "動画")

    def _register_folder_identities(self) -> None:
        if self.database is None or self._current_folder is None:
            return
        dialog = BulkRegistrationDialog(self._include_subfolders, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        options = dialog.options()
        try:
            found = find_media_files(
                self._current_folder,
                include_subfolders=options.include_subfolders,
            )
            self.database.refresh_file_hash_statuses(found.paths)
            stored = self.database.get_file_hashes(found.paths)
        except (MediaSearchError, DatabaseError) as error:
            self._show_data_management_error(str(error))
            return
        suffixes = (
            IMAGE_SUFFIXES if options.kinds == "images" else
            VIDEO_SUFFIXES if options.kinds == "videos" else
            IMAGE_SUFFIXES | VIDEO_SUFFIXES
        )
        targets = [
            path for path in found.paths
            if path.suffix.lower() in suffixes
            and (record := stored.get(self._path_key(path))) is not None
            and is_bulk_registration_status(record.status)
        ]
        total_size = sum(self._safe_file_size(path) for path in targets)
        if not targets:
            QMessageBox.information(
                self, "ファイル識別情報", "選択した条件に該当する項目はありません。"
            )
            return
        kind_label = {
            "images": "画像", "videos": "動画", "both": "画像と動画"
        }[options.kinds]
        answer = QMessageBox.question(
            self,
            "ファイル識別情報を登録",
            f"対象: {kind_label} {len(targets)}件\n"
            f"合計サイズ: {self._format_file_size(total_size)}\n\n"
            "未登録・再登録が必要・前回登録に失敗した項目が対象です。\n\n"
            "登録を開始しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_identity_registration(targets, kind_label, show_progress=True)

    def _show_identity_status(self) -> None:
        if self.database is None:
            return
        try:
            info = get_database_info(self.database.path)
        except DataManagementError as error:
            self._show_data_management_error(str(error))
            return
        IdentityStatusDialog(
            info,
            self,
            info_provider=lambda: get_database_info(self.database.path),
            is_processing=lambda: self._identity_thread is not None,
        ).exec()

    def _maybe_auto_register_identities(self, paths: list[Path]) -> None:
        targets = automatic_image_candidates(paths, self._identity_auto_mode)
        if not targets:
            return
        if requires_large_registration_confirmation(len(targets)):
            box = QMessageBox(self)
            box.setWindowTitle("ファイル識別情報を登録")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(f"新しい画像が {len(targets)}件 見つかりました。")
            box.setInformativeText(
                "ファイル識別情報を登録しますか？\n\n"
                "登録には時間がかかる場合があります。\n"
                "新しい画像は、登録しない場合でも通常どおり一覧へ追加されます。"
            )
            register = box.addButton("登録する", QMessageBox.ButtonRole.AcceptRole)
            skip = box.addButton("今回は登録しない", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(skip)
            box.setEscapeButton(skip)
            box.exec()
            if box.clickedButton() is not register:
                QMessageBox.information(
                    self,
                    "ファイル識別情報",
                    "今回はファイル識別情報を登録しませんでした。\n"
                    "新しい画像は通常どおり一覧へ登録されていますが、\n"
                    "ファイル識別情報は未登録です。\n"
                    "後から「データ管理」→「ファイル識別情報」から\n"
                    "手動で登録できます。",
                )
                return
        self._start_identity_registration(
            targets, "画像", show_progress=len(targets) >= 500, automatic=True
        )

    def _start_identity_registration(
        self,
        paths: list[Path],
        kinds: str,
        *,
        show_progress: bool = False,
        automatic: bool = False,
    ) -> None:
        if (
            self.database is None
            or self._identity_thread is not None
        ):
            self._set_status("ファイル識別情報の登録はすでに実行中です。", "error")
            return
        unique = [
            path for path in dict.fromkeys(Path(path) for path in paths)
            if self._path_key(path) not in self._identity_processing_paths
        ]
        if not unique:
            return
        self._identity_processing_paths.update(self._path_key(path) for path in unique)
        self._identity_operation_automatic = automatic
        self._identity_successful_file_ids = ()
        if self._reuse_detection_thread is not None:
            # 独立読取workerは完走させるが、登録開始前の古い結果は通知しない。
            self._reuse_detection_stale = True
        thread = QThread(self)
        worker = FileIdentityRegistrationWorker(self.database.path, unique)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._identity_progress)
        worker.finished.connect(self._identity_finished)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._identity_thread_finished)
        self._identity_thread = thread
        self._identity_worker = worker
        if show_progress:
            progress = RegistrationProgressDialog(len(unique), kinds, self)
            progress.cancelRequested.connect(lambda: worker.request_cancel())
            self._identity_progress_dialog = progress
            progress.show()
        self._set_status(
            f"ファイル識別情報を登録しています… 0 / {len(unique)}", "active"
        )
        self._update_identity_actions()
        thread.start()

    def _identity_progress(self, progress: RegistrationProgress) -> None:
        if self._identity_progress_dialog is not None:
            self._identity_progress_dialog.update_progress(progress)
        self._set_status(
            f"ファイル識別情報を登録しています… "
            f"{progress.completed} / {progress.total}",
            "active",
        )

    def _identity_finished(self, summary: RegistrationSummary) -> None:
        self._identity_successful_file_ids = summary.successful_file_ids
        if self._identity_progress_dialog is not None:
            self._identity_progress_dialog.hide()
            self._identity_progress_dialog.deleteLater()
            self._identity_progress_dialog = None
        elapsed = self._format_elapsed(summary.elapsed_seconds)
        if summary.cancelled:
            heading = "ファイル識別情報の登録を中止しました。"
            text = (
                f"{heading}\n\n登録済み: {summary.succeeded}件\n"
                f"登録失敗: {summary.failed}件\n未処理: {summary.unprocessed}件\n\n"
                "完了済みの登録情報は保存されています。"
            )
        else:
            heading = "ファイル識別情報の登録が完了しました。"
            text = (
                f"{heading}\n\n対象: {summary.total}件\n登録済み: {summary.succeeded}件\n"
                f"登録失敗: {summary.failed}件\n処理時間: {elapsed}"
            )
        if summary.errors:
            text += "\n\n失敗内容:\n" + "\n".join(summary.errors[:10])
        if (
            not self._identity_exit_pending
            and (
                not self._identity_operation_automatic
                or summary.cancelled
                or summary.failed
            )
        ):
            QMessageBox.information(self, "ファイル識別情報", text)
        self._set_status(heading, "error" if summary.failed else "active")
        self._identity_processing_paths.clear()
        self._update_identity_actions()
        # 成功IDを保持した後に終了させ、thread.finished側が先行して
        # 候補検出対象を取りこぼさないようにする。
        if self._identity_thread is not None:
            self._identity_thread.quit()

    def _identity_thread_finished(self) -> None:
        successful_file_ids = self._identity_successful_file_ids
        self._identity_successful_file_ids = ()
        self._identity_thread = None
        self._identity_worker = None
        self._identity_operation_automatic = False
        self._update_identity_actions()
        if self._identity_exit_pending:
            self._identity_exit_pending = False
            QTimer.singleShot(0, self.close)
            return
        if successful_file_ids:
            QTimer.singleShot(
                0, lambda ids=successful_file_ids: self._start_reuse_detection(ids)
            )

    def _start_reuse_detection(self, target_file_ids: tuple[int, ...]) -> None:
        if (
            self.database is None
            or not target_file_ids
            or self._identity_thread is not None
        ):
            if target_file_ids:
                self._reuse_pending_target_ids.update(target_file_ids)
            return
        if self._reuse_detection_thread is not None:
            self._reuse_pending_target_ids.update(target_file_ids)
            return
        self._reuse_detection_stale = False
        thread = QThread(self)
        worker = ReuseCandidateDetectionWorker(self.database.path, target_file_ids)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._reuse_detection_finished)
        worker.failed.connect(self._reuse_detection_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._reuse_detection_thread_finished)
        self._reuse_detection_thread = thread
        self._reuse_detection_worker = worker
        thread.start()

    def _reuse_detection_finished(
        self, results: tuple[ReuseCandidateResult, ...]
    ) -> None:
        if self._reuse_detection_stale:
            return
        new_results = tuple(
            result for result in results
            if result.notification_key not in self._reuse_notified_keys
        )
        available = tuple(
            result for result in new_results
            if result.assessment.decision is CopyDecision.COPYABLE
        )
        if not available or self._identity_exit_pending or self._reuse_exit_pending:
            return
        self._reuse_notified_keys.update(
            result.notification_key for result in new_results
        )
        self._show_reuse_notification(new_results, len(available))

    def _reuse_detection_failed(self, _message: str) -> None:
        if self._reuse_exit_pending:
            return
        self._set_status(
            "以前のMETAMIデータ候補を確認できませんでした。", "ready"
        )

    def _reuse_detection_thread_finished(self) -> None:
        self._reuse_detection_thread = None
        self._reuse_detection_worker = None
        self._reuse_detection_stale = False
        self._update_identity_actions()
        if self._reuse_exit_pending:
            self._reuse_exit_pending = False
            QTimer.singleShot(0, self.close)
            return
        if self._reuse_pending_target_ids and self._identity_thread is None:
            pending = tuple(sorted(self._reuse_pending_target_ids))
            self._reuse_pending_target_ids.clear()
            QTimer.singleShot(0, lambda ids=pending: self._start_reuse_detection(ids))

    def _show_reuse_notification(
        self, results: tuple[ReuseCandidateResult, ...], available_count: int
    ) -> None:
        if self._reuse_notification is not None:
            self._reuse_notification.close()
        box = QMessageBox(self)
        box.setWindowTitle("以前のMETAMIデータ")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            "以前のMETAMIデータを利用できるファイルが\n"
            f"{available_count}件見つかりました。"
        )
        confirm = box.addButton("確認する", QMessageBox.ButtonRole.AcceptRole)
        ignore = box.addButton("今回は何もしない", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(confirm)
        box.setEscapeButton(ignore)
        box.setModal(False)
        box.finished.connect(
            lambda _code, values=results, button=confirm, notice=box:
            self._reuse_notification_closed(values, button, notice)
        )
        self._reuse_notification = box
        box.show()

    def _reuse_notification_closed(
        self,
        results: tuple[ReuseCandidateResult, ...],
        confirm_button: QAbstractButton,
        notice: QMessageBox,
    ) -> None:
        clicked = notice.clickedButton()
        if self._reuse_notification is notice:
            self._reuse_notification = None
        if clicked is confirm_button:
            dialog = ReuseCandidatesDialog(results, self)
            self._reuse_candidates_dialog = dialog
            dialog.batchCopyRequested.connect(self._request_batch_user_data_copy)
            dialog.finished.connect(
                lambda _code, value=dialog: self._clear_reuse_dialog(value)
            )
            dialog.show()

    def _clear_reuse_dialog(self, dialog: ReuseCandidatesDialog) -> None:
        if self._reuse_candidates_dialog is dialog:
            self._reuse_candidates_dialog = None

    def _request_batch_user_data_copy(self) -> None:
        if self.database is None or self._current_folder is None:
            self._set_status("現在開いているフォルダがありません。", "error")
            return
        if self._identity_thread is not None or self._reuse_detection_thread is not None or self._copy_thread is not None:
            self._set_status("別のファイル処理が実行中です。", "error")
            return
        paths = tuple(self._current_display_paths)
        try:
            target_ids = self.database.get_file_ids_by_paths(paths)
            current = detect_reuse_candidates(self.database, target_ids)
        except DatabaseError as error:
            self._set_status(f"コピー候補を再確認できません: {error}", "error")
            return
        count = sum(item.assessment.decision is CopyDecision.COPYABLE for item in current)
        if not count:
            QMessageBox.information(
                self, "以前のMETAMIデータを一括コピー",
                "現在のフォルダにコピー可能なファイルはありません。",
            )
            return
        message = (
            f"現在のフォルダで、以前のMETAMIデータを利用できるファイルが{count}件あります。\n\n"
            "メモ・タグ・星評価・利用者タイトルをまとめてコピーします。\n"
            "すでにMETAMIデータがあるファイルや、候補が競合するファイルは変更されません。"
        )
        confirmation = QMessageBox(self)
        confirmation.setWindowTitle("以前のMETAMIデータを一括コピー")
        confirmation.setIcon(QMessageBox.Icon.Question)
        confirmation.setText(message)
        execute = confirmation.addButton(
            f"{count}件へ一括コピー", QMessageBox.ButtonRole.AcceptRole
        )
        cancel = confirmation.addButton(
            "キャンセル", QMessageBox.ButtonRole.RejectRole
        )
        confirmation.setDefaultButton(cancel)
        confirmation.exec()
        if confirmation.clickedButton() is not execute:
            return
        self._start_batch_user_data_copy(tuple(target_ids), paths)

    def _start_batch_user_data_copy(
        self, target_ids: tuple[int, ...], allowed_paths: tuple[Path, ...]
    ) -> None:
        if self.database is None or self._copy_thread is not None:
            return
        thread = QThread(self)
        worker = UserDataCopyWorker(self.database.path, target_ids, allowed_paths)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._copy_progress)
        worker.finished.connect(self._copy_finished)
        worker.failed.connect(self._copy_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._copy_thread_finished)
        self._copy_thread = thread
        self._copy_worker = worker
        progress = UserDataCopyProgressDialog(len(target_ids), self)
        progress.cancelRequested.connect(worker.request_cancel)
        self._copy_progress_dialog = progress
        if self._reuse_candidates_dialog is not None:
            self._reuse_candidates_dialog.close()
        progress.show()
        thread.start()

    def _copy_progress(self, done: int, total: int, path: str) -> None:
        if self._copy_progress_dialog is not None:
            self._copy_progress_dialog.update_progress(done, total, path)

    def _copy_finished(self, summary) -> None:
        if self._copy_progress_dialog is not None:
            self._copy_progress_dialog.hide()
            self._copy_progress_dialog.deleteLater()
            self._copy_progress_dialog = None
        if summary.copied_count and self._current_folder is not None:
            self._reload_current_folder()
        dialog = UserDataCopyResultDialog(summary, self)
        self._copy_result_dialog = dialog
        dialog.finished.connect(lambda _code: setattr(self, "_copy_result_dialog", None))
        dialog.show()

    def _copy_failed(self, message: str) -> None:
        if self._copy_progress_dialog is not None:
            self._copy_progress_dialog.hide()
            self._copy_progress_dialog.deleteLater()
            self._copy_progress_dialog = None
        QMessageBox.warning(self, "以前のMETAMIデータを一括コピー", f"処理を完了できませんでした。\n{message}")

    def _copy_thread_finished(self) -> None:
        self._copy_thread = None
        self._copy_worker = None
        if self._copy_exit_pending:
            self._copy_exit_pending = False
            QTimer.singleShot(0, self.close)

    @staticmethod
    def _safe_file_size(path: Path) -> int:
        try:
            return max(0, path.stat().st_size)
        except OSError:
            return 0

    @staticmethod
    def _format_file_size(size: int) -> str:
        value = float(max(0, size))
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        total = max(0, int(seconds))
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"

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
        self._update_identity_actions()
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
                "登録された場所にファイルが見つかりません。"
            )
            self._set_status(
                f"登録された場所にファイルが見つかりません: {path}", "error"
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
        self._update_identity_actions()
        self._cancel_rating_nudge()
        self.file_list_pane.clear()
        self.preview_pane.clear("入力を受け付けられませんでした。")
        self.metadata_pane.show_error(message)
        self._set_status(f"エラー: {message}", "error")

    def _show_no_search_results(self, message: str) -> None:
        if not self._resolve_unsaved_memo():
            return
        self._current_path = None
        self._update_identity_actions()
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
            self.preview_pane.clear("登録された場所にファイルが見つかりません。")
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
        """利用者が選んだファイルへ、既存DB記録の登録パスを変更する。"""
        old_path = self._current_path
        if old_path is None or old_path.is_file() or self.database is None:
            self._set_status("登録パスを変更するmissing記録がありません。", "error")
            return
        if not self._resolve_unsaved_memo():
            return
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "新しい登録先ファイルを選択",
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
                "登録パスを変更できません",
                "PNG、WEBP、MP4の実在するファイルを選択してください。",
            )
            return
        answer = QMessageBox.question(
            self,
            "登録パスを変更",
            "見つからない記録の登録パスを、次のファイルへ変更しますか？\n\n"
            f"現在の登録パス: {old_path}\n新しい登録先: {new_path}\n\n"
            "この操作は既存のDB記録のパスを変更します。\n"
            "フォルダやドライブの自動検索、別レコードへのデータコピーは行いません。\n"
            "保存済みのタイトル、評価、タグ、メモは維持されます。\n"
            "ファイル識別情報がある場合は、選択したファイルの内容が一致するか確認します。\n"
            "METAMIは画像・動画ファイル本体を変更しません。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            stored_hash = self.database.get_file_hash(old_path)
            has_verifiable_hash = bool(
                stored_hash
                and stored_hash.status is HashStatus.MISSING
                and stored_hash.algorithm in SUPPORTED_HASH_ALGORITHMS
                and is_valid_hash(
                    stored_hash.content_hash, stored_hash.algorithm or ""
                )
                and stored_hash.calculated_at
                and stored_hash.file_size is not None
                and stored_hash.modified_ns is not None
            )
            verified_hash = None
            expected_content_hash = None
            expected_hash_algorithm = None
            if has_verifiable_hash and stored_hash is not None:
                verified_hash = calculate_file_hash(
                    new_path, algorithm=stored_hash.algorithm or ""
                )
                if not verified_hash.success:
                    QMessageBox.warning(
                        self,
                        "ファイル内容を確認できません",
                        "選択したファイルの内容を確認できませんでした。\n"
                        "読み取り権限やドライブの接続状態を確認し、もう一度選択してください。",
                    )
                    return
                expected_content_hash = stored_hash.content_hash
                expected_hash_algorithm = stored_hash.algorithm
            self.database.relink_missing_file(
                old_path,
                new_path,
                verified_hash=verified_hash,
                expected_content_hash=expected_content_hash,
                expected_hash_algorithm=expected_hash_algorithm,
            )
        except DatabaseError as error:
            self._set_database_state("error")
            QMessageBox.warning(self, "登録パスの変更に失敗しました", str(error))
            self._set_status(f"登録パスを変更できません: {error}", "error")
            return
        paths = [
            new_path if self._same_path(path, old_path) else path
            for path in self.file_list_pane.all_paths()
        ]
        self._current_path = None
        self._set_database_state("online")
        self._set_paths(paths)
        self.file_list_pane.select_path(new_path)
        self._set_status(f"登録パスを変更しました: {new_path}", "active")

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
        if self._data_operation_in_progress:
            event.ignore()
            return
        if self._copy_thread is not None and self._copy_worker is not None:
            if not self.isVisible():
                self._copy_worker.request_cancel()
                self._copy_thread.quit()
                self._copy_thread.wait(5000)
                self._copy_thread = None
                self._copy_worker = None
            else:
                self._copy_exit_pending = True
                self._copy_worker.request_cancel()
                self._set_status("コピー処理の中止後に終了します。", "ready")
                event.ignore()
                return
        if self._identity_thread is not None and self._identity_worker is not None:
            if not self.isVisible():
                # 非表示のテスト用ウィンドウ等は対話不能なので安全に待機する。
                self._identity_worker.request_cancel()
                self._identity_thread.quit()
                self._identity_thread.wait(5000)
                self._identity_thread = None
                self._identity_worker = None
                self._identity_processing_paths.clear()
            else:
                answer = QMessageBox.question(
                    self,
                    "ファイル識別情報を登録しています",
                    "終了すると、未処理のファイルは登録されません。\n"
                    "完了済みの登録情報は保存されます。\n\n"
                    "登録を中止して終了しますか？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._identity_exit_pending = True
                    self._identity_worker.request_cancel()
                event.ignore()
                return
        if self._reuse_detection_thread is not None:
            if not self.isVisible():
                self._reuse_detection_thread.quit()
                self._reuse_detection_thread.wait(5000)
                self._reuse_detection_thread = None
                self._reuse_detection_worker = None
            else:
                self._reuse_exit_pending = True
                self._set_status(
                    "以前のMETAMIデータ候補の確認終了後に閉じます。", "ready"
                )
                event.ignore()
                return
        if not self._resolve_unsaved_memo():
            event.ignore()
            return
        self.file_list_pane.release_media()
        self.preview_pane.release_media()
        self._rating_prompt_timer.stop()
        super().closeEvent(event)
