"""右ページの情報・Workflow・JSON・メモ表示を担当する。"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from models.display_data import DisplayData
from ui.decorations import tab_pixmap


PROMPT_FIELDS = {"jp_prompt", "Prompt", "Negative Prompt"}
TECHNICAL_FIELD_MARKERS = (
    "path",
    "パス",
    "prompt",
    "model",
    "モデル",
    "seed",
    "sampler",
    "scheduler",
    "vae",
    "lora",
    "workflow",
    "json",
)


class RatingControl(QWidget):
    """詳細タブを切り替えても見える3段階評価コントロール。"""

    ratingChangeRequested = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("persistentRating")
        self.rating = 0
        label = QLabel("🏅 評価")
        label.setObjectName("ratingLabel")
        self.button = QToolButton()
        self.button.setObjectName("ratingButton")
        self.button.setAccessibleName("選択中ファイルの評価")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self._advance_rating)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 0, 4, 0)
        layout.setSpacing(2)
        layout.addWidget(label)
        layout.addWidget(self.button)
        self.set_rating(0, False)

    def set_rating(self, rating: int, enabled: bool = True) -> None:
        self.rating = max(0, min(3, rating))
        self.button.setText("★" * self.rating + "☆" * (3 - self.rating))
        self.button.setProperty("rating", self.rating)
        self.button.setEnabled(enabled)
        self.button.setToolTip(
            f"現在: 星{self.rating}（クリックで変更）"
            if self.rating
            else "現在: 星なし（クリックで変更）"
        )
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)

    def _advance_rating(self) -> None:
        previous = self.rating
        rating = (previous + 1) % 4
        self.set_rating(rating)
        self.ratingChangeRequested.emit(rating, previous)


class MetadataPane(QWidget):
    """右ページ下部の情報表示を、見出し直下の付箋タブで切り替える。"""

    ratingChangeRequested = Signal(int, int)
    tagAddRequested = Signal(str)
    tagRemoveRequested = Signal(str)
    organizerSaveRequested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metadataPane")

        self.tabs = QTabWidget()
        self.tabs.setObjectName("metadataTabs")
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.tabs.setIconSize(QSize(8, 8))
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        for button in self.tabs.tabBar().findChildren(QToolButton):
            if button.objectName() == "ScrollLeftButton":
                button.setArrowType(Qt.ArrowType.NoArrow)
                button.setIcon(_tab_scroll_icon("left"))
                button.setToolTip("左のタブを表示")
            elif button.objectName() == "ScrollRightButton":
                button.setArrowType(Qt.ArrowType.NoArrow)
                button.setIcon(_tab_scroll_icon("right"))
                button.setToolTip("右のタブを表示")
            else:
                continue
            button.setIconSize(QSize(12, 12))
            button.setMinimumWidth(20)
            button.setStyleSheet(
                "QToolButton {"
                " background: #ffffff;"
                " border: 1px solid #303238;"
                " border-radius: 3px;"
                " padding: 0;"
                " margin: 0;"
                "}"
                "QToolButton:hover { background: #e2e4e7; }"
                "QToolButton:pressed { background: #c7cacf; }"
                "QToolButton:disabled {"
                " background: #eeeeee;"
                " border-color: #777b82;"
                "}"
            )
        self.rating_control = RatingControl()
        self.rating_control.ratingChangeRequested.connect(
            self.ratingChangeRequested
        )
        self.overview_tab = OverviewTab()
        self.organizer_tab = OrganizerTab()
        self.title_editor = self.organizer_tab.title_editor
        self.memo_tab = self.organizer_tab.memo_tab
        self.tag_editor = self.organizer_tab.tag_editor
        self.tag_editor.tagAddRequested.connect(self.tagAddRequested)
        self.tag_editor.tagRemoveRequested.connect(self.tagRemoveRequested)
        self.organizer_tab.saveRequested.connect(self.organizerSaveRequested)
        self.ltx_tab = PlaceholderTab("LTX表示項目は調査中です")
        self.wan_tab = PlaceholderTab("WAN対応は準備中です")
        self.image_generation_tab = PlaceholderTab(
            "画像生成用の表示項目は今後追加予定です"
        )
        self.workflow_tab = TextTab("Workflowをコピー")
        self.json_tab = TextTab("JSON全文をコピー")
        self.tabs.addTab(self.overview_tab, "基本情報")
        self.tabs.addTab(self.organizer_tab, "タグ・メモ")
        self.tabs.addTab(self.ltx_tab, "LTX")
        self.tabs.addTab(self.wan_tab, "WAN")
        self.tabs.addTab(self.image_generation_tab, "画像生成")
        self.tabs.addTab(self.workflow_tab, "Workflow")
        self.tabs.addTab(self.json_tab, "JSON全文")
        colors = (
            "#e9defc",
            "#f3d8ea",
            "#f7dfaa",
            "#d7dde8",
            "#f4d9c7",
            "#cfe8df",
            "#d9e1f3",
        )
        for index, color in enumerate(colors):
            self.tabs.setTabIcon(index, QIcon(tab_pixmap(color)))
        for index in range(self.tabs.count()):
            self.tabs.setTabToolTip(index, self.tabs.tabText(index))
        wan_index = self.tabs.indexOf(self.wan_tab)
        self.tabs.setTabEnabled(wan_index, False)
        self.tabs.setTabToolTip(wan_index, "WAN対応は準備中です")
        self._optional_tabs = {
            "ltx": self.ltx_tab,
            "wan": self.wan_tab,
            "image_generation": self.image_generation_tab,
            "workflow": self.workflow_tab,
            "json": self.json_tab,
        }

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tabs, 1)
        self.clear()

    def optional_tab_visibility(self) -> dict[str, bool]:
        return {
            key: self.tabs.isTabVisible(self.tabs.indexOf(widget))
            for key, widget in self._optional_tabs.items()
        }

    def set_optional_tab_visible(self, key: str, visible: bool) -> None:
        widget = self._optional_tabs[key]
        index = self.tabs.indexOf(widget)
        self.tabs.setTabVisible(index, visible)
        if self.tabs.currentWidget() is widget and not visible:
            self.tabs.setCurrentWidget(self.overview_tab)

    def reset_optional_tabs(self) -> None:
        for key in self._optional_tabs:
            self.set_optional_tab_visible(key, True)
        self.tabs.setTabEnabled(self.tabs.indexOf(self.wan_tab), False)

    def set_data(self, data: DisplayData) -> None:
        self.overview_tab.set_data(data)
        self.workflow_tab.set_text(data.workflow_text)
        self.json_tab.set_text(data.json_text)

    def set_rating(self, rating: int, enabled: bool = True) -> None:
        self.rating_control.set_rating(rating, enabled)

    def set_tags(self, tags: list[str], enabled: bool = True) -> None:
        self.tag_editor.set_tags(tags, enabled)

    def set_memo(self, memo: str, enabled: bool = True) -> None:
        self.memo_tab.set_memo(memo, enabled)

    def set_user_title(self, title: str, enabled: bool = True) -> None:
        self.title_editor.set_title(title, enabled)
        self.organizer_tab.sync_dirty_state()

    def is_memo_dirty(self) -> bool:
        return self.memo_tab.is_dirty()

    def is_organizer_dirty(self) -> bool:
        return self.organizer_tab.is_dirty()

    def memo_text(self) -> str:
        return self.memo_tab.text()

    def user_title_text(self) -> str:
        return self.title_editor.text()

    def mark_memo_saved(self) -> None:
        self.memo_tab.mark_saved()

    def mark_organizer_saved(self, title: str) -> None:
        self.title_editor.mark_saved(title)
        self.memo_tab.mark_saved()
        self.organizer_tab.sync_dirty_state()

    def revert_organizer(self) -> None:
        self.organizer_tab.revert()

    def is_organizer_editing(self) -> bool:
        """評価案内を控えるべき、タグ・メモ入力中かを返す。"""
        focus = QApplication.focusWidget()
        if focus is None:
            return False
        return (
            focus is self.tag_editor.input
            or focus is self.title_editor.input
            or focus is self.memo_tab.editor
            or self.tag_editor.isAncestorOf(focus)
        )

    def show_error(self, message: str) -> None:
        self.overview_tab.show_error(message)
        self.workflow_tab.set_text("Workflow情報を表示できません。")
        self.json_tab.set_text("メタデータを表示できません。")

    def clear(self) -> None:
        self.overview_tab.clear()
        self.workflow_tab.set_text("Workflow情報はありません。")
        self.json_tab.set_text("メタデータはありません。")
        self.set_rating(0, False)
        self.set_tags([], False)
        self.set_user_title("", False)
        self.set_memo("", False)


class PlaceholderTab(QWidget):
    """将来機能の予定を短文だけで示す仮タブ。"""

    def __init__(self, message: str) -> None:
        super().__init__()
        label = QLabel(message)
        label.setObjectName("placeholderTabMessage")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addStretch(1)


class OverviewTab(QScrollArea):
    """基本情報・Prompt・生成パラメータを1ページにまとめる。"""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("detailOverview")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.basic_card = InfoCard("基本情報", 2)
        self.prompt_card = InfoCard("生成情報", 1)
        self.parameters_card = InfoCard("生成パラメータ", 3)
        content = QWidget()
        content.setObjectName("paperPage")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(9, 9, 9, 9)
        layout.setSpacing(9)
        layout.addWidget(self.basic_card)
        layout.addWidget(self.prompt_card)
        layout.addWidget(self.parameters_card)
        layout.addStretch(1)
        self.setWidget(content)

    def set_data(self, data: DisplayData) -> None:
        prompts = tuple(item for item in data.generation_info if item[0] in PROMPT_FIELDS)
        parameters = tuple(
            item for item in data.generation_info if item[0] not in PROMPT_FIELDS
        )
        self.basic_card.set_items(data.basic_info)
        self.prompt_card.set_items(prompts)
        self.parameters_card.set_items(parameters)

    def show_error(self, message: str) -> None:
        self.basic_card.set_items((("エラー", message),))
        self.prompt_card.set_items(())
        self.parameters_card.set_items(())

    def clear(self) -> None:
        self.basic_card.set_items(())
        self.prompt_card.set_items(())
        self.parameters_card.set_items(())


class TitleEditor(QFrame):
    """40文字までのユーザータイトルを編集する。"""

    dirtyChanged = Signal(bool)
    MAX_LENGTH = 40

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("titleCard")
        self._saved_title = ""
        heading = QLabel("タイトル")
        heading.setObjectName("cardHeading")
        self.count_label = QLabel(f"0 / {self.MAX_LENGTH}文字")
        self.count_label.setObjectName("titleCount")
        header = QHBoxLayout()
        header.addWidget(heading)
        header.addStretch(1)
        header.addWidget(self.count_label)

        self.input = QLineEdit()
        self.input.setObjectName("titleInput")
        self.input.setPlaceholderText("未設定時はファイル名を表示します")
        self.input.setMaxLength(self.MAX_LENGTH)
        self.input.textChanged.connect(self._on_text_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 9)
        layout.setSpacing(5)
        layout.addLayout(header)
        layout.addWidget(self.input)
        self.set_title("", False)

    def set_title(self, title: str, enabled: bool = True) -> None:
        self._saved_title = title.strip()[: self.MAX_LENGTH]
        self.input.blockSignals(True)
        self.input.setText(self._saved_title)
        self.input.blockSignals(False)
        self.input.setEnabled(enabled)
        self._update_count()
        self.dirtyChanged.emit(False)

    def text(self) -> str:
        return self.input.text()

    def is_dirty(self) -> bool:
        return self.text().strip() != self._saved_title

    def mark_saved(self, title: str) -> None:
        normalized = title.strip()[: self.MAX_LENGTH]
        self._saved_title = normalized
        if self.input.text() != normalized:
            self.input.blockSignals(True)
            self.input.setText(normalized)
            self.input.blockSignals(False)
        self._update_count()
        self.dirtyChanged.emit(False)

    def revert(self) -> None:
        self.set_title(self._saved_title, self.input.isEnabled())

    def _on_text_changed(self) -> None:
        self._update_count()
        self.dirtyChanged.emit(self.is_dirty())

    def _update_count(self) -> None:
        self.count_label.setText(
            f"{len(self.text())} / {self.MAX_LENGTH}文字"
        )


class TagEditor(QFrame):
    """選択ファイルの手動タグを付箋状に表示・編集する。"""

    tagAddRequested = Signal(str)
    tagRemoveRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tagCard")
        heading = QLabel("タグ")
        heading.setObjectName("cardHeading")
        help_label = QLabel("クリックで削除")
        help_label.setObjectName("tagHelp")
        header = QHBoxLayout()
        header.addWidget(heading)
        header.addStretch(1)
        header.addWidget(help_label)

        self.tag_container = QWidget()
        self.tag_container.setObjectName("tagContainer")
        self.tag_layout = QHBoxLayout(self.tag_container)
        self.tag_layout.setContentsMargins(2, 2, 2, 2)
        self.tag_layout.setSpacing(5)
        self.tag_layout.addStretch(1)
        self.tag_scroll = QScrollArea()
        self.tag_scroll.setObjectName("tagScroll")
        self.tag_scroll.setWidgetResizable(True)
        self.tag_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.tag_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.tag_scroll.setFixedHeight(52)
        self.tag_scroll.setWidget(self.tag_container)

        self.input = QLineEdit()
        self.input.setObjectName("tagInput")
        self.input.setPlaceholderText("新しいタグを入力")
        self.input.setMaxLength(100)
        self.input.returnPressed.connect(self._request_add)
        self.add_button = QToolButton()
        self.add_button.setObjectName("tagAddButton")
        self.add_button.setText("追加")
        self.add_button.clicked.connect(self._request_add)
        input_row = QHBoxLayout()
        input_row.setSpacing(5)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.add_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 9)
        layout.setSpacing(5)
        layout.addLayout(header)
        layout.addWidget(self.tag_scroll)
        layout.addLayout(input_row)

    def set_tags(self, tags: list[str], enabled: bool = True) -> None:
        self.input.clear()
        while self.tag_layout.count() > 1:
            item = self.tag_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if tags:
            for tag in tags:
                button = QToolButton()
                button.setObjectName("tagChip")
                button.setText(f"{tag}  ×")
                button.setToolTip(f"タグ「{tag}」を外す")
                button.clicked.connect(
                    lambda _checked=False, name=tag: self.tagRemoveRequested.emit(
                        name
                    )
                )
                self.tag_layout.insertWidget(self.tag_layout.count() - 1, button)
        else:
            empty = QLabel("タグなし")
            empty.setObjectName("tagEmpty")
            self.tag_layout.insertWidget(0, empty)
        self.input.setEnabled(enabled)
        self.add_button.setEnabled(enabled)

    def clear_input(self) -> None:
        self.input.clear()

    def _request_add(self) -> None:
        name = self.input.text().strip()
        if name:
            self.tagAddRequested.emit(name)


class OrganizerTab(QWidget):
    """手動タグとメモを1枚の整理ページへまとめる。"""

    saveRequested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("organizerTab")
        self.title_editor = TitleEditor()
        self.tag_editor = TagEditor()
        self.memo_tab = MemoTab()
        self.setMinimumHeight(270)
        self.title_editor.dirtyChanged.connect(
            lambda _dirty: self.sync_dirty_state()
        )
        self.memo_tab.saveRequested.connect(self._request_save)
        self.memo_tab.revert_button.clicked.disconnect(self.memo_tab.revert)
        self.memo_tab.revert_button.clicked.connect(self.revert)
        self.memo_tab.save_button.setText("変更を保存")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self.title_editor)
        layout.addWidget(self.tag_editor)
        layout.addWidget(self.memo_tab, 1)
        self.sync_dirty_state()

    def is_dirty(self) -> bool:
        return self.title_editor.is_dirty() or self.memo_tab.is_dirty()

    def sync_dirty_state(self) -> None:
        self.memo_tab.set_external_dirty(self.title_editor.is_dirty())

    def revert(self) -> None:
        self.title_editor.revert()
        self.memo_tab.revert()
        self.sync_dirty_state()

    def _request_save(self, memo: str) -> None:
        self.saveRequested.emit(self.title_editor.text(), memo)


class InfoCard(QFrame):
    """まとまり単位で表示・コピーできる情報カード。"""

    ratingChangeRequested = Signal(int, int)

    def __init__(
        self,
        title: str,
        columns: int = 1,
        parent: QWidget | None = None,
        rating_control: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("infoCard")
        self._columns = columns
        self._items: list[tuple[str, str]] = []
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(8)
        shadow.setOffset(0, 1)
        shadow.setColor(QColor(73, 55, 42, 24))
        self.setGraphicsEffect(shadow)

        heading = QLabel(title)
        heading.setObjectName("cardHeading")
        self.copy_button = QToolButton()
        self.copy_button.setObjectName("cardCopyButton")
        self.copy_button.setIcon(_copy_icon())
        self.copy_button.setToolTip(f"{title}をコピー")
        self.copy_button.setAccessibleName(f"{title}をコピー")
        self.copy_button.clicked.connect(self.copy_items)

        header = QHBoxLayout()
        header.addWidget(heading)
        header.addStretch(1)
        self.rating_label: QLabel | None = None
        self.rating_button: QToolButton | None = None
        self.rating = 0
        if rating_control:
            self.rating_label = QLabel("🏅 評価")
            self.rating_label.setObjectName("ratingLabel")
            self.rating_button = QToolButton()
            self.rating_button.setObjectName("ratingButton")
            self.rating_button.setAccessibleName("選択中ファイルの評価")
            self.rating_button.clicked.connect(self._advance_rating)
            header.addWidget(self.rating_label)
            header.addWidget(self.rating_button)
            self.set_rating(0, False)
        header.addWidget(self.copy_button)
        self.fields = QGridLayout()
        self.fields.setHorizontalSpacing(12)
        self.fields.setVerticalSpacing(4)
        for column in range(columns):
            self.fields.setColumnStretch(column, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 9)
        layout.setSpacing(5)
        layout.addLayout(header)
        layout.addLayout(self.fields)

    def set_items(self, items: Iterable[tuple[str, str]]) -> None:
        while self.fields.count():
            item = self.fields.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._items = list(items)
        self.copy_button.setEnabled(bool(self._items))
        if not self._items:
            empty = QLabel("情報なし")
            empty.setObjectName("emptyInfo")
            self.fields.addWidget(empty, 0, 0, 1, self._columns)
            return
        for index, (key, value) in enumerate(self._items):
            field = QWidget()
            field.setObjectName("infoField")
            key_label = QLabel(key)
            key_label.setObjectName("fieldName")
            value_label = WrappingValueLabel(value or "情報なし")
            value_label.setObjectName("fieldValue")
            normalized_key = key.casefold()
            value_label.setProperty(
                "technical",
                any(marker in normalized_key for marker in TECHNICAL_FIELD_MARKERS),
            )
            value_label.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
            )
            value_label.setMinimumWidth(1)
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            value_label.setAlignment(
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
            )
            field_layout = QVBoxLayout(field)
            field_layout.setContentsMargins(2, 3, 2, 6)
            field_layout.setSpacing(2)
            field_layout.addWidget(key_label)
            field_layout.addWidget(value_label)
            row, column = divmod(index, self._columns)
            self.fields.addWidget(field, row, column)

    def copy_items(self) -> None:
        QApplication.clipboard().setText(
            "\n".join(f"{key}: {value or '情報なし'}" for key, value in self._items)
        )

    def set_rating(self, rating: int, enabled: bool = True) -> None:
        if self.rating_button is None:
            return
        self.rating = max(0, min(3, rating))
        self.rating_button.setText(
            "★" * self.rating + "☆" * (3 - self.rating)
        )
        self.rating_button.setProperty("rating", self.rating)
        self.rating_button.setEnabled(enabled)
        self.rating_button.setToolTip(
            f"現在: 星{self.rating}（クリックで変更）"
            if self.rating
            else "現在: 星なし（クリックで変更）"
        )
        self.rating_button.style().unpolish(self.rating_button)
        self.rating_button.style().polish(self.rating_button)

    def _advance_rating(self) -> None:
        previous = self.rating
        rating = (previous + 1) % 4
        self.set_rating(rating)
        self.ratingChangeRequested.emit(rating, previous)


class WrappingValueLabel(QLabel):
    """幅変更に合わせて必要な高さを確保し、長文の欠落を防ぐ。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = max(1, event.size().width())
        bounds = self.fontMetrics().boundingRect(
            QRect(0, 0, width, 1_000_000),
            Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs,
            self.text(),
        )
        required = min(50_000, max(self.fontMetrics().height(), bounds.height() + 2))
        if self.minimumHeight() != required:
            self.setMinimumHeight(required)


class TextTab(QWidget):
    """長文表示とアイコンによるコピー操作を持つタブ。"""

    def __init__(self, copy_tooltip: str) -> None:
        super().__init__()
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("technicalText")
        self.editor.setReadOnly(True)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.copy_button = QToolButton()
        self.copy_button.setObjectName("cardCopyButton")
        self.copy_button.setIcon(_copy_icon())
        self.copy_button.setToolTip(copy_tooltip)
        self.copy_button.setAccessibleName(copy_tooltip)
        self.copy_button.clicked.connect(self.copy_text)
        toolbar = QHBoxLayout()
        toolbar.addStretch(1)
        toolbar.addWidget(self.copy_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(toolbar)
        layout.addWidget(self.editor, 1)

    def set_text(self, text: str) -> None:
        self.editor.setPlainText(text)
        self.editor.moveCursor(self.editor.textCursor().MoveOperation.Start)

    def copy_text(self) -> None:
        QApplication.clipboard().setText(self.editor.toPlainText())


class MemoTab(QWidget):
    """400文字までの一般メモを明示操作で保存する。"""

    saveRequested = Signal(str)

    MAX_LENGTH = 400
    X_GUIDE_LENGTH = 280

    def __init__(self) -> None:
        super().__init__()
        self._saved_text = ""
        self._adjusting = False
        self._external_dirty = False
        title = QLabel("メモ")
        title.setObjectName("cardHeading")
        self.count_label = QLabel("0 / 400文字")
        self.count_label.setObjectName("memoCount")
        self.state_label = QLabel("保存済み")
        self.state_label.setObjectName("memoState")
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.state_label)
        header.addWidget(self.count_label)

        self.editor = MemoEditor()
        self.editor.setObjectName("memoEditor")
        # 起動直後でも2～3行を編集できる高さを確保する。
        self.editor.setMinimumHeight(72)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.editor.textChanged.connect(self._on_text_changed)
        self.guide_label = QLabel("X通常投稿の目安: 280文字")
        self.guide_label.setObjectName("memoGuide")

        self.revert_button = QToolButton()
        self.revert_button.setObjectName("memoRevertButton")
        self.revert_button.setText("元に戻す")
        self.revert_button.clicked.connect(self.revert)
        self.save_button = QToolButton()
        self.save_button.setObjectName("memoSaveButton")
        self.save_button.setText("保存")
        self.save_button.clicked.connect(
            lambda: self.saveRequested.emit(self.text())
        )
        actions = QHBoxLayout()
        actions.addWidget(self.guide_label)
        actions.addStretch(1)
        actions.addWidget(self.revert_button)
        actions.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(7)
        layout.addLayout(header)
        layout.addWidget(self.editor, 1)
        layout.addLayout(actions)
        self.setMinimumHeight(126)
        self.set_memo("", False)

    def set_memo(self, memo: str, enabled: bool = True) -> None:
        self._saved_text = memo[: self.MAX_LENGTH]
        self.editor.blockSignals(True)
        self.editor.setPlainText(self._saved_text)
        self.editor.blockSignals(False)
        self.editor.setEnabled(enabled)
        self._update_state()

    def text(self) -> str:
        return self.editor.toPlainText()

    def is_dirty(self) -> bool:
        return self.text() != self._saved_text

    def mark_saved(self) -> None:
        self._saved_text = self.text()
        self._update_state()

    def set_external_dirty(self, dirty: bool) -> None:
        self._external_dirty = dirty
        self._update_state()

    def revert(self) -> None:
        self.set_memo(self._saved_text, self.editor.isEnabled())

    def _on_text_changed(self) -> None:
        if self._adjusting:
            return
        text = self.text()
        if len(text) > self.MAX_LENGTH:
            self._adjusting = True
            self.editor.setPlainText(text[: self.MAX_LENGTH])
            cursor = self.editor.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.editor.setTextCursor(cursor)
            self._adjusting = False
        self._update_state()

    def _update_state(self) -> None:
        length = len(self.text())
        dirty = self.is_dirty() or self._external_dirty
        self.count_label.setText(f"{length} / {self.MAX_LENGTH}文字")
        self.count_label.setProperty(
            "level",
            "limit"
            if length >= self.MAX_LENGTH
            else "warning"
            if length > self.X_GUIDE_LENGTH
            else "normal",
        )
        if length > self.X_GUIDE_LENGTH:
            self.guide_label.setText(
                "X通常投稿の目安280文字を超えています"
            )
            self.guide_label.setProperty("warning", True)
        else:
            self.guide_label.setText("X通常投稿の目安: 280文字")
            self.guide_label.setProperty("warning", False)
        self.state_label.setText("未保存" if dirty else "保存済み")
        self.state_label.setProperty("dirty", dirty)
        self.save_button.setEnabled(self.editor.isEnabled() and dirty)
        self.revert_button.setEnabled(self.editor.isEnabled() and dirty)
        for widget in (self.count_label, self.guide_label, self.state_label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class MemoEditor(QPlainTextEdit):
    """IME入力中も案内文が重ならないメモ入力欄。"""

    PLACEHOLDER = "生成結果の感想、修正点、再利用方法などを入力してください。"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(self.PLACEHOLDER)

    def focusInEvent(self, event) -> None:
        # 日本語IMEの未確定文字は文書へ入る前に描画されるため、
        # Qt標準のプレースホルダーが残る環境がある。選択時点で消す。
        self.setPlaceholderText("")
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if not self.toPlainText():
            self.setPlaceholderText(self.PLACEHOLDER)


def _copy_icon() -> QIcon:
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#4d5966"), 1.6))
    painter.drawRoundedRect(4, 3, 10, 11, 1.5, 1.5)
    painter.drawRoundedRect(7, 6, 10, 11, 1.5, 1.5)
    painter.end()
    return QIcon(pixmap)


def _tab_scroll_icon(direction: str) -> QIcon:
    """アプリのQSSに埋もれない濃色三角アイコンを返す。"""
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#17191d"))
    points = (
        QPolygon([QPoint(10, 2), QPoint(4, 7), QPoint(10, 12)])
        if direction == "left"
        else QPolygon([QPoint(4, 2), QPoint(10, 7), QPoint(4, 12)])
    )
    painter.drawPolygon(points)
    painter.end()
    icon = QIcon()
    icon.addPixmap(pixmap, QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(pixmap, QIcon.Mode.Disabled, QIcon.State.Off)
    icon.addPixmap(pixmap, QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(pixmap, QIcon.Mode.Selected, QIcon.State.Off)
    return icon
