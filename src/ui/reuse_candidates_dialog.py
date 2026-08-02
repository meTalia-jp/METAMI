"""以前のMETAMIデータ候補を参照専用で表示する画面。"""

from __future__ import annotations

from html import escape
from pathlib import Path
import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
    QProgressBar,
)

from storage.database import MetadataDatabase
from storage.user_data_candidate_detection import (
    ReuseCandidateResult,
    detect_reuse_candidates,
)
from storage.user_data_copy import BatchCopySummary, copy_user_data_batch
from storage.user_data_reuse import CopyDecision


class ReuseCandidateDetectionWorker(QObject):
    """独立DBインスタンスで候補を一括検出する読み取り専用worker。"""

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, database_path: Path, target_file_ids: tuple[int, ...]) -> None:
        super().__init__()
        self.database_path = Path(database_path)
        self.target_file_ids = target_file_ids

    @Slot()
    def run(self) -> None:
        try:
            results = detect_reuse_candidates(
                MetadataDatabase(self.database_path), self.target_file_ids
            )
        except Exception as error:
            self.failed.emit(str(error))
            return
        self.finished.emit(results)


class ReuseCandidatesDialog(QDialog):
    """候補と全sourceの保存情報を表示する。編集操作は持たない。"""

    HEADERS = ("現在のファイル", "以前の登録場所", "利用できるデータ", "状態")
    batchCopyRequested = Signal()

    def __init__(
        self, results: tuple[ReuseCandidateResult, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.results = results
        self.setWindowTitle("以前のMETAMIデータ")
        self.resize(1050, 650)
        self.setMinimumSize(760, 460)
        layout = QVBoxLayout(self)
        description = QLabel(
            "同じ内容のファイルに、以前のメモ・タグ・星評価・タイトルが"
            "登録されています。\nこの画面では登録済みデータを確認できます。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 190)
        self.table.setColumnWidth(1, 470)
        self.table.setColumnWidth(2, 170)
        self.table.setColumnWidth(3, 190)
        self.details = QTextBrowser()
        self.details.setPlaceholderText("一覧から候補を選択してください。")
        self.details.setOpenExternalLinks(False)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.details)
        splitter.setSizes([390, 210])
        layout.addWidget(splitter, 1)
        buttons = QDialogButtonBox()
        copyable_count = sum(
            item.assessment.decision is CopyDecision.COPYABLE for item in results
        )
        self.copy_button = QPushButton(
            f"コピー可能な{copyable_count}件へ一括コピー"
        )
        self.copy_button.setEnabled(copyable_count > 0)
        self.copy_button.clicked.connect(self.batchCopyRequested)
        buttons.addButton(self.copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(self.accept)
        buttons.addButton(close_button, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)
        self.table.itemSelectionChanged.connect(self._show_details)
        self._populate()

    def _populate(self) -> None:
        self.table.setRowCount(len(self.results))
        for row, result in enumerate(self.results):
            source_paths = [item.record.canonical_path for item in result.sources.candidates]
            values = (
                result.target_filename,
                source_paths[0] if len(source_paths) == 1 else f"{len(source_paths)}件の登録場所",
                "・".join(result.available_items) or "なし",
                result.display_status,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, row)
                if column == 0:
                    item.setToolTip(result.target.canonical_path)
                elif column == 1:
                    item.setToolTip("\n".join(source_paths))
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        if self.results:
            self.table.selectRow(0)

    def _show_details(self) -> None:
        selected = self.table.selectedItems()
        if not selected:
            self.details.clear()
            return
        result = self.results[int(selected[0].data(Qt.ItemDataRole.UserRole))]
        sections = [
            f"<h3>{escape(result.target_filename)}</h3>",
            f"<p><b>現在の登録パス:</b> {escape(result.target.canonical_path)}<br>"
            f"<b>状態:</b> {escape(result.display_status)}</p>",
        ]
        for index, source in enumerate(result.sources.candidates, 1):
            data = source.user_data
            tags = "、".join(data.tags) if data.tags else "なし"
            memo = data.memo if data.memo.strip() else "なし"
            title = data.title if data.title.strip() else "なし"
            sections.append(
                f"<hr><h4>以前の登録 {index}</h4>"
                f"<p><b>登録パス:</b> {escape(source.record.canonical_path)}<br>"
                f"<b>ファイル状態:</b> {'見つからない' if source.record.missing else '登録場所に存在'}<br>"
                f"<b>タイトル:</b> {escape(title)}<br>"
                f"<b>星評価:</b> {data.rating if data.rating else 'なし'}<br>"
                f"<b>タグ:</b> {escape(tags)}<br>"
                f"<b>メモ:</b><br>{escape(memo).replace(chr(10), '<br>')}</p>"
            )
        self.details.setHtml("".join(sections))


class UserDataCopyWorker(QObject):
    """独立DB接続で再判定・ハッシュ検証・保存を行うworker。"""

    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self, database_path: Path, target_file_ids: tuple[int, ...],
        allowed_paths: tuple[Path, ...],
    ) -> None:
        super().__init__()
        self.database_path = Path(database_path)
        self.target_file_ids = target_file_ids
        self.allowed_paths = allowed_paths
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        try:
            summary = copy_user_data_batch(
                MetadataDatabase(self.database_path), self.target_file_ids,
                allowed_paths=self.allowed_paths, cancel_event=self._cancel,
                progress=lambda done, total, path: self.progress.emit(
                    done, total, str(path)
                ),
            )
        except Exception as error:
            self.failed.emit(str(error))
            return
        self.finished.emit(summary)


class UserDataCopyProgressDialog(QDialog):
    cancelRequested = Signal()

    def __init__(self, total: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("以前のMETAMIデータを一括コピー")
        self.setMinimumWidth(520)
        self.setModal(False)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("以前のMETAMIデータをコピーしています"))
        self.current = QLabel("処理中: 準備中")
        self.count = QLabel(f"0 / {total}")
        self.bar = QProgressBar()
        self.bar.setRange(0, max(1, total))
        layout.addWidget(self.current)
        layout.addWidget(self.count)
        layout.addWidget(self.bar)
        self.cancel_button = QPushButton("キャンセル")
        self.cancel_button.clicked.connect(self._cancel)
        layout.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignRight)

    def update_progress(self, done: int, total: int, path: str) -> None:
        self.current.setText(f"処理中: {Path(path).name}")
        self.count.setText(f"{done} / {total}")
        self.bar.setMaximum(max(1, total))
        self.bar.setValue(done)

    def _cancel(self) -> None:
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("中止しています…")
        self.cancelRequested.emit()

    def closeEvent(self, event) -> None:
        if self.cancel_button.isEnabled():
            self._cancel()
        event.ignore()


class UserDataCopyResultDialog(QDialog):
    def __init__(self, summary: BatchCopySummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("METAMIデータのコピー結果")
        self.resize(720, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "METAMIデータのコピーが完了しました。\n"
            f"コピー成功: {summary.copied_count}件　"
            f"既存データあり: {summary.skipped_existing_data_count}件　"
            f"候補競合: {summary.skipped_conflict_count}件\n"
            f"安全確認による除外: "
            f"{summary.skipped_invalid_hash_count + summary.skipped_changed_file_count}件　"
            f"DB更新失敗: {summary.failed_count}件"
        ))
        details = QTextBrowser()
        details.setPlainText("\n".join(
            f"{item.target_path.name}: {item.reason}"
            + (f" ({item.error_message})" if item.error_message else "")
            for item in summary.results
        ))
        layout.addWidget(details, 1)
        close = QPushButton("閉じる")
        close.clicked.connect(self.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
