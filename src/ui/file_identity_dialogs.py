"""ファイル識別情報の設定・一括登録・進捗表示。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QStyleFactory,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from storage.database import DatabaseError, MetadataDatabase
from storage.file_hash import CURRENT_HASH_ALGORITHM, HashStatus, calculate_file_hash


AUTO_REGISTRATION_KEY = "fileIdentity/autoRegistrationMode"
AUTO_IMAGES_ONLY = "images_only"
AUTO_OFF = "off"
AUTO_MODES = frozenset({AUTO_IMAGES_ONLY, AUTO_OFF})
IMAGE_SUFFIXES = frozenset({".png", ".webp", ".jpg", ".jpeg"})
VIDEO_SUFFIXES = frozenset({".mp4"})

STATUS_LABELS = {
    HashStatus.NOT_CALCULATED: "未登録",
    HashStatus.CALCULATED: "登録済み",
    HashStatus.STALE: "再登録が必要",
    HashStatus.FAILED: "登録失敗",
    HashStatus.MISSING: "ファイルが見つかりません",
}


def normalized_auto_registration_mode(value: object) -> str:
    """不明値は、安全な既定の画像のみへ戻す。"""
    mode = str(value or "").strip().casefold()
    return mode if mode in AUTO_MODES else AUTO_IMAGES_ONLY


def identity_status_label(status: HashStatus) -> str:
    return STATUS_LABELS.get(status, "不明状態")


def automatic_image_candidates(paths: list[Path], mode: str) -> list[Path]:
    """今回新規登録された通常画像だけを自動対象にする。"""
    if normalized_auto_registration_mode(mode) != AUTO_IMAGES_ONLY:
        return []
    return [
        Path(path) for path in paths
        if Path(path).suffix.lower() in IMAGE_SUFFIXES and Path(path).is_file()
    ]


def requires_large_registration_confirmation(count: int) -> bool:
    return count >= 500


def can_register_status(status: HashStatus | None) -> bool:
    return status in {HashStatus.NOT_CALCULATED, HashStatus.FAILED}


def can_reregister_status(status: HashStatus | None) -> bool:
    return status in {HashStatus.CALCULATED, HashStatus.STALE, HashStatus.FAILED}


def use_standard_indicator_style(*widgets: QWidget) -> None:
    """全体QSSの影響を避け、Qt標準の選択インジケーターを描画する。"""
    for widget in widgets:
        style = QStyleFactory.create("Fusion")
        if style is not None:
            widget.setStyle(style)


class AutoRegistrationSettingsDialog(QDialog):
    """新規画像だけを対象とする自動登録設定。"""

    def __init__(self, mode: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ファイル識別情報の登録設定")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        title = QLabel("ファイル識別情報の自動登録")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)
        self.images_only = QRadioButton(
            "新しく登録した画像の識別情報を自動登録する"
        )
        self.off = QRadioButton("自動登録しない")
        use_standard_indicator_style(self.images_only, self.off)
        group = QButtonGroup(self)
        group.addButton(self.images_only)
        group.addButton(self.off)
        self.images_only.setChecked(
            normalized_auto_registration_mode(mode) == AUTO_IMAGES_ONLY
        )
        self.off.setChecked(not self.images_only.isChecked())
        layout.addWidget(self.images_only)
        layout.addWidget(self.off)
        description = QLabel(
            "新しくMETAMIへ登録された画像のファイル識別情報を自動的に登録します。\n\n"
            "すでにMETAMIへ登録済みの未登録ファイルは、この設定を変更しても"
            "自動では登録されません。既存ファイルは「フォルダ内の未登録項目を登録」"
            "から手動で登録できます。\n\n"
            "ファイル数が非常に多い環境や、低速なストレージ、ネットワークドライブを"
            "使用する場合は、自動登録を無効にできます。\n\n"
            "動画の識別情報は自動登録されません。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_mode(self) -> str:
        return AUTO_IMAGES_ONLY if self.images_only.isChecked() else AUTO_OFF


@dataclass(frozen=True)
class BulkRegistrationOptions:
    kinds: str
    include_subfolders: bool


class BulkRegistrationDialog(QDialog):
    """今回だけ適用する一括登録条件を選択する。"""

    def __init__(
        self, include_subfolders: bool, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("ファイル識別情報をまとめて登録")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)

        introduction = QLabel(
            "現在開いているフォルダから、\n"
            "ファイル識別情報を登録する項目を選択します。"
        )
        introduction.setWordWrap(True)
        layout.addWidget(introduction)

        kind_box = QGroupBox("対象ファイル")
        kind_layout = QVBoxLayout(kind_box)
        self.images = QRadioButton("画像")
        self.videos = QRadioButton("動画")
        self.both = QRadioButton("画像と動画")
        self.images.setChecked(True)
        use_standard_indicator_style(self.images, self.videos, self.both)
        kinds = QButtonGroup(self)
        for button in (self.images, self.videos, self.both):
            kinds.addButton(button)
            kind_layout.addWidget(button)
        layout.addWidget(kind_box)

        range_box = QGroupBox("対象範囲")
        range_layout = QVBoxLayout(range_box)
        range_layout.addWidget(QLabel("現在開いているフォルダ"))
        self.include_subfolders = QCheckBox("サブフォルダを含む")
        self.include_subfolders.setChecked(include_subfolders)
        use_standard_indicator_style(self.include_subfolders)
        range_layout.addWidget(self.include_subfolders)
        layout.addWidget(range_box)

        note = QLabel(
            "ファイル識別情報が未登録、再登録が必要、または前回の登録に失敗した"
            "ファイルをまとめて処理します。\n"
            "ファイルが見つからない項目は対象になりません。\n\n"
            "動画やネットワークドライブ上のファイルは、登録に時間がかかる場合が"
            "あります。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "対象を確認して次へ"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> BulkRegistrationOptions:
        kinds = "images" if self.images.isChecked() else (
            "videos" if self.videos.isChecked() else "both"
        )
        return BulkRegistrationOptions(kinds, self.include_subfolders.isChecked())


@dataclass(frozen=True)
class RegistrationProgress:
    completed: int
    total: int
    path: Path
    succeeded: int
    failed: int
    processed_bytes: int
    total_bytes: int
    error: str = ""


@dataclass(frozen=True)
class RegistrationSummary:
    total: int
    succeeded: int
    failed: int
    unprocessed: int
    processed_bytes: int
    total_bytes: int
    elapsed_seconds: float
    cancelled: bool
    errors: tuple[str, ...]
    successful_file_ids: tuple[int, ...] = ()


class FileIdentityRegistrationWorker(QObject):
    """別スレッド・別SQLite接続で1件ずつ安全に保存する。"""

    progress = Signal(object)
    finished = Signal(object)

    def __init__(self, database_path: Path, paths: list[Path]) -> None:
        super().__init__()
        self.database_path = Path(database_path)
        self.paths = list(dict.fromkeys(Path(path) for path in paths))
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        started = time.monotonic()
        database = MetadataDatabase(self.database_path)
        succeeded = failed = processed_bytes = 0
        successful_paths: list[Path] = []
        errors: list[str] = []
        total_bytes = sum(_safe_size(path) for path in self.paths)
        for path in self.paths:
            if self._cancel.is_set():
                break
            result = calculate_file_hash(path)
            error = result.error or ""
            try:
                database.save_file_hash_result(result)
            except DatabaseError as exc:
                error = str(exc)
            if result.status is HashStatus.CALCULATED and not error:
                succeeded += 1
                successful_paths.append(path)
            else:
                failed += 1
                errors.append(f"{path.name}: {error or identity_status_label(result.status)}")
            processed_bytes += max(0, result.file_size or _safe_size(path))
            self.progress.emit(
                RegistrationProgress(
                    succeeded + failed, len(self.paths), path, succeeded, failed,
                    processed_bytes, total_bytes, error,
                )
            )
        completed = succeeded + failed
        try:
            successful_file_ids = database.get_file_ids_by_paths(successful_paths)
        except DatabaseError as exc:
            successful_file_ids = ()
            if successful_paths:
                errors.append(f"候補確認対象を取得できません: {exc}")
        self.finished.emit(
            RegistrationSummary(
                len(self.paths), succeeded, failed, len(self.paths) - completed,
                processed_bytes, total_bytes, time.monotonic() - started,
                self._cancel.is_set(), tuple(errors[:100]),
                successful_file_ids,
            )
        )


class RegistrationProgressDialog(QDialog):
    """一括処理の進捗と安全なキャンセル要求を表示する。"""

    cancelRequested = Signal()

    def __init__(self, total: int, kinds: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ファイル識別情報の登録")
        self.setMinimumWidth(560)
        self.setModal(False)
        layout = QVBoxLayout(self)
        title = QLabel("ファイル識別情報を登録しています")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)
        self.target = QLabel(f"対象: {kinds}")
        self.progress_label = QLabel(f"進捗: 0 / {total}")
        self.current = QLabel("現在のファイル: 準備中")
        self.counts = QLabel(f"成功: 0件　失敗: 0件　未処理: {total}件")
        self.bytes = QLabel("処理済み容量: 0 B")
        for label in (self.target, self.progress_label, self.current, self.counts, self.bytes):
            label.setWordWrap(True)
            layout.addWidget(label)
        self.bar = QProgressBar()
        self.bar.setRange(0, max(1, total))
        layout.addWidget(self.bar)
        self.cancel_button = QPushButton("キャンセル")
        self.cancel_button.clicked.connect(self._request_cancel)
        layout.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignRight)

    def update_progress(self, progress: RegistrationProgress) -> None:
        self.bar.setValue(progress.completed)
        self.progress_label.setText(f"進捗: {progress.completed} / {progress.total}")
        self.current.setText(f"現在のファイル: {progress.path.name}")
        self.counts.setText(
            f"成功: {progress.succeeded}件　失敗: {progress.failed}件　"
            f"未処理: {progress.total - progress.completed}件"
        )
        self.bytes.setText(
            f"処理済み容量: {_format_size(progress.processed_bytes)} / "
            f"{_format_size(progress.total_bytes)}"
        )

    def _request_cancel(self) -> None:
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("中止しています…")
        self.cancelRequested.emit()

    def closeEvent(self, event) -> None:
        if self.cancel_button.isEnabled():
            self._request_cancel()
        event.ignore()


class IdentityStatusDialog(QDialog):
    """既存DB情報集計を利用者向け用語で表示する。"""

    def __init__(
        self,
        info,
        parent: QWidget | None = None,
        *,
        info_provider=None,
        is_processing=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("ファイル識別情報の登録状況")
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "未登録とは、ファイル識別情報がまだ登録されていない状態です。\n"
            "画像・動画自体はMETAMIへ登録されています。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.processing_label = QLabel()
        self.processing_label.setStyleSheet("font-weight: bold; color: #553260;")
        layout.addWidget(self.processing_label)
        form = QFormLayout()
        self.value_labels: dict[str, QLabel] = {}
        for heading in (
            "標準方式", "登録済み", "未登録", "再登録が必要", "登録失敗",
            "ファイルが見つかりません", "不明状態",
        ):
            label = QLabel()
            self.value_labels[heading] = label
            form.addRow(f"{heading}:", label)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("閉じる")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._info_provider = info_provider
        self._is_processing = is_processing
        self._set_info(info)
        self._refresh_processing()
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh)
        if info_provider is not None:
            self._timer.start()

    def _set_info(self, info) -> None:
        values = {
            "標準方式": info.standard_hash_algorithm,
            "登録済み": f"{info.hash_calculated_files}件",
            "未登録": f"{info.hash_not_calculated_files}件",
            "再登録が必要": f"{info.hash_stale_files}件",
            "登録失敗": f"{info.hash_failed_files}件",
            "ファイルが見つかりません": f"{info.hash_missing_files}件",
            "不明状態": f"{info.hash_unknown_files}件",
        }
        for heading, value in values.items():
            self.value_labels[heading].setText(value)

    def _refresh_processing(self) -> None:
        processing = bool(self._is_processing and self._is_processing())
        self.processing_label.setText(
            "ファイル識別情報を登録しています…" if processing else ""
        )
        self.processing_label.setVisible(processing)

    def refresh(self) -> None:
        if self._info_provider is not None:
            try:
                self._set_info(self._info_provider())
            except Exception:
                pass
        self._refresh_processing()


def _safe_size(path: Path) -> int:
    try:
        return max(0, path.stat().st_size)
    except OSError:
        return 0


def _format_size(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
