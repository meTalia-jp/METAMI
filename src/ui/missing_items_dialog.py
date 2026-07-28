"""DBを変更せず、登録場所で見つからないファイルを確認する画面。"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from metadata.file_presence import FilePresenceResult, check_registered_file
from storage.database import DatabaseError, MetadataDatabase, RegisteredFileRecord


class FilePresenceWorker(QObject):
    """時間のかかる可能性があるパス確認をUIスレッド外で行う。"""

    progress = Signal(int, int, object)
    finished = Signal()

    def __init__(self, records: list[RegisteredFileRecord]) -> None:
        super().__init__()
        self.records = records

    @Slot()
    def run(self) -> None:
        total = len(self.records)
        for index, record in enumerate(self.records, 1):
            self.progress.emit(
                index, total, check_registered_file(record)
            )
        self.finished.emit()


class MissingItemsDialog(QDialog):
    """登録情報と一時的な存在確認結果だけを表示する読み取り専用画面。"""

    HEADERS = ("状態", "ファイル名", "登録パス", "種類", "評価", "確認日時")

    def __init__(
        self, database: MetadataDatabase, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.database = database
        self._results: dict[int, FilePresenceResult] = {}
        self._pending_results: list[FilePresenceResult] = []
        self._thread: QThread | None = None
        self._worker: FilePresenceWorker | None = None
        self.setWindowTitle("見つからない項目の確認・整理")
        self.resize(1040, 650)
        self.setMinimumSize(780, 480)

        layout = QVBoxLayout(self)
        description = QLabel(
            "登録済みのファイルが、現在の登録場所に存在するか確認します。\n"
            "この画面を表示しただけでは、DBや実ファイルは変更されません。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        self.result_label = QLabel("確認を開始します…")
        self.result_label.setObjectName("missingResultLabel")
        layout.addWidget(self.result_label)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setObjectName("missingItemsTable")
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(2, 390)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(4, 70)
        self.table.setColumnWidth(5, 155)
        self.table.itemSelectionChanged.connect(self._show_selected_details)

        self.details = QTextBrowser()
        self.details.setObjectName("missingItemDetails")
        self.details.setOpenExternalLinks(False)
        self.details.setPlaceholderText("一覧から項目を選択してください。")
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.details)
        splitter.setSizes([390, 180])
        layout.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("再確認")
        self.refresh_button.clicked.connect(self.start_check)
        self.detail_button = QPushButton("選択項目の詳細")
        self.detail_button.setEnabled(False)
        self.detail_button.clicked.connect(self._focus_details)
        self.close_button = QPushButton("閉じる")
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.detail_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.start_check()

    def start_check(self) -> None:
        if self._thread is not None:
            return
        selected_id = self._selected_file_id()
        try:
            records = self.database.get_registered_file_records()
        except DatabaseError as error:
            self.result_label.setText(f"登録情報を確認できません: {error}")
            return
        self._pending_results = []
        self.refresh_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.detail_button.setEnabled(False)
        self.result_label.setText(f"確認中… 0 / {len(records)}件")

        thread = QThread(self)
        worker = FilePresenceWorker(records)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._receive_result)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(
            lambda selected_id=selected_id: self._finish_check(selected_id)
        )
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(int, int, object)
    def _receive_result(
        self, completed: int, total: int, result: FilePresenceResult
    ) -> None:
        self._pending_results.append(result)
        self.result_label.setText(f"確認中… {completed} / {total}件")

    def _finish_check(self, selected_id: int | None) -> None:
        results = [
            result
            for result in self._pending_results
            if not result.is_available
        ]
        results.sort(
            key=lambda item: (
                item.status.casefold(),
                item.record.path.casefold(),
                item.record.filename.casefold(),
                item.record.file_id,
            )
        )
        self._results = {
            result.record.file_id: result for result in results
        }
        self._populate_table(results, selected_id)
        self._thread = None
        self._worker = None
        self.refresh_button.setEnabled(True)
        self.close_button.setEnabled(True)
        if results:
            unavailable = sum(
                result.status == "確認できません" for result in results
            )
            self.result_label.setText(
                f"見つからない・確認できない項目: {len(results)}件"
                + (f"（確認できません: {unavailable}件）" if unavailable else "")
            )
        else:
            self.result_label.setText("見つからない項目はありません。")

    def _populate_table(
        self,
        results: list[FilePresenceResult],
        selected_id: int | None,
    ) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        selected_row = -1
        for row, result in enumerate(results):
            record = result.record
            self.table.insertRow(row)
            kind = (
                "動画"
                if record.format_name.casefold() == "mp4"
                else "画像"
            )
            values = (
                result.status,
                record.filename,
                record.path,
                kind,
                "★" * record.rating if record.rating else "☆",
                result.checked_at,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(
                        Qt.ItemDataRole.UserRole, record.file_id
                    )
                self.table.setItem(row, column, item)
            if record.file_id == selected_id:
                selected_row = row
        self.table.setSortingEnabled(True)
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif results:
            self.table.selectRow(0)
        else:
            self.details.clear()
            self.detail_button.setEnabled(False)

    def _selected_file_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return int(value) if value is not None else None

    def _show_selected_details(self) -> None:
        file_id = self._selected_file_id()
        result = self._results.get(file_id) if file_id is not None else None
        self.detail_button.setEnabled(result is not None)
        if result is None:
            self.details.clear()
            return
        record = result.record
        kind = "動画" if record.format_name.casefold() == "mp4" else "画像"
        memo = record.memo or "なし"
        tags = "、".join(record.tags) or "なし"
        error = result.error or "なし"
        self.details.setPlainText(
            "\n".join(
                (
                    f"状態: {result.status}",
                    f"ファイル名: {record.filename or '情報なし'}",
                    f"登録パス: {record.path or '情報なし'}",
                    f"ファイル種別: {kind}",
                    f"拡張子: {record.format_name or '情報なし'}",
                    f"登録時のファイルサイズ: {record.size_bytes} bytes",
                    f"タグ: {tags}",
                    f"評価: {record.rating}",
                    f"メモ: {memo}",
                    f"登録日時: {record.created_at or '情報なし'}",
                    f"今回の確認日時: {result.checked_at}",
                    f"確認時の補足: {error}",
                    "",
                    "現在、登録された場所でファイルを確認できません。",
                    "ファイルが移動されたか、保存先が一時的に"
                    "利用できない可能性があります。",
                )
            )
        )

    def _focus_details(self) -> None:
        self._show_selected_details()
        self.details.setFocus()

    def closeEvent(self, event) -> None:
        if self._thread is not None:
            QMessageBox.information(
                self,
                "確認中",
                "登録ファイルの確認が完了するまでお待ちください。",
            )
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        """Escキーでも確認スレッド実行中の破棄を防ぐ。"""
        if self._thread is not None:
            return
        super().reject()
