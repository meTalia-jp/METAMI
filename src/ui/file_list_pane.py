"""ネタカード型のファイル一覧を担当する左ペイン。"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QSize, QStringListModel, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QImageReader, QMouseEvent, QPixmap
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QCompleter,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from metadata.mp4_reader import Mp4ReadError, read_mp4_info
from metadata.search_service import SearchRecord, build_search_record
from ui.decorations import CharacterHeader, memo_pixmap
from ui.surface_widgets import MagnetPin, StickyNoteFrame
from ui.styles import CARD_HEIGHT, CARD_THUMBNAIL_SIZE, CARD_WIDTH


class ResponsiveTagBar(QWidget):
    """利用可能な横幅に合わせ、タグと残数を1行へ収める。"""

    CHIP_SPACING = 4
    MAX_TEXT_WIDTH = 92
    CHIP_PADDING = 12

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cardTagBar")
        self.setMinimumWidth(0)
        self.setFixedHeight(25)
        # 内部チップのsizeHintでカード自体の最小幅を押し広げない。
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._tags: list[str] = []
        self.labels: list[QLabel] = []
        self.extra_label = QLabel()
        self.extra_label.setObjectName("cardTagExtra")
        self.extra_label.hide()
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(self.CHIP_SPACING)
        self._layout.addWidget(self.extra_label)
        self._layout.addStretch(1)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh)

    def set_tags(self, tags: list[str]) -> None:
        self._tags = list(tags)
        self._refresh()
        # レイアウト確定後に再計算する。親カード破棄時はタイマーも破棄される。
        self._refresh_timer.start(0)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 25)

    def sizeHint(self) -> QSize:
        return QSize(120, 25)

    def visible_tag_count(self) -> int:
        return sum(not label.isHidden() for label in self.labels)

    def hidden_tags(self) -> list[str]:
        return self._tags[self.visible_tag_count() :]

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        displays = [self._display_text(tag) for tag in self._tags]
        widths = [self._chip_width(text) for text in displays]
        visible_count = self._fit_count(widths)
        self._ensure_labels(visible_count)

        for index, label in enumerate(self.labels):
            if index < visible_count:
                tag = self._tags[index]
                label.setText(displays[index])
                label.setFixedWidth(widths[index])
                label.setToolTip(tag)
                label.setAccessibleName(f"タグ {tag}")
                label.show()
            else:
                label.hide()

        hidden = self._tags[visible_count:]
        if hidden:
            self.extra_label.setText(f"+{len(hidden)}")
            self.extra_label.setToolTip(
                "省略されたタグ:\n" + "\n".join(hidden)
            )
            self.extra_label.setFixedWidth(self._extra_width(len(hidden)))
            self.extra_label.show()
        else:
            self.extra_label.clear()
            self.extra_label.setToolTip("")
            self.extra_label.hide()

    def _fit_count(self, widths: list[int]) -> int:
        available = max(0, self.width())
        total_tags = len(widths)
        prefix_width = 0
        best = 0
        for count in range(total_tags + 1):
            if count > 0:
                prefix_width += widths[count - 1]
            hidden_count = total_tags - count
            component_count = count + int(hidden_count > 0)
            required = prefix_width
            if hidden_count:
                required += self._extra_width(hidden_count)
            if component_count > 1:
                required += self.CHIP_SPACING * (component_count - 1)
            if required <= available:
                best = count
        return best

    def _ensure_labels(self, count: int) -> None:
        while len(self.labels) < count:
            label = QLabel()
            label.setObjectName("cardTag")
            self._layout.insertWidget(len(self.labels), label)
            self.labels.append(label)

    def _display_text(self, tag: str) -> str:
        return self.fontMetrics().elidedText(
            f"[{tag}]", Qt.TextElideMode.ElideRight, self.MAX_TEXT_WIDTH
        )

    def _chip_width(self, text: str) -> int:
        return min(
            self.MAX_TEXT_WIDTH + self.CHIP_PADDING,
            self.fontMetrics().horizontalAdvance(text) + self.CHIP_PADDING,
        )

    def _extra_width(self, hidden_count: int) -> int:
        return self.fontMetrics().horizontalAdvance(f"+{hidden_count}") + 8


class FileCard(StickyNoteFrame):
    """一覧内に表示する、1ファイル分の読み取り専用カード。"""

    activated = Signal()
    ratingChanged = Signal(str, int)

    def __init__(
        self,
        path: Path,
        rating: int = 0,
        tags: list[str] | None = None,
        has_memo: bool = False,
        missing: bool = False,
        memo: str = "",
        user_title: str = "",
        relative_hint: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.path = path
        self.setObjectName("fileCard")
        self.setProperty("selected", False)
        tone = ("lavender", "pink", "mint", "yellow", "blue")[
            sum(path.name.encode("utf-8")) % 5
        ]
        self.setProperty("noteTone", tone)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(12)
        shadow.setOffset(2, 3)
        shadow.setColor(QColor(42, 35, 28, 55))
        self.setGraphicsEffect(shadow)
        self.pin_label = MagnetPin(tone, self)

        self.thumbnail = QLabel("読込中")
        self.thumbnail.setObjectName("cardThumbnail")
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setFixedSize(QSize(*CARD_THUMBNAIL_SIZE))
        self._portrait_mode = False
        self._aspect_label = ""
        self._card_tags: list[str] = []

        self._user_title = ""
        self._name_text = path.name
        self.name_label = QLabel(path.name)
        self.name_label.setObjectName("cardFileName")
        self.name_label.setWordWrap(False)
        self.name_label.setToolTip(str(path))
        self.set_user_title(user_title)

        badge = QLabel(path.suffix.removeprefix(".").upper())
        badge.setObjectName("formatBadge")
        badge.setProperty("format", path.suffix.removeprefix(".").upper())
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedWidth(48)

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name_row.addWidget(self.name_label, 1)
        self.missing_badge = QLabel("ファイルなし")
        self.missing_badge.setObjectName("missingBadge")
        self.missing_badge.setToolTip("登録済みの原本ファイルが見つかりません")
        self.missing_badge.hide()
        self.favorite_button = QToolButton()
        self.favorite_button.setObjectName("favoriteButton")
        self.favorite_button.setToolTip("星ランクを変更")
        self.favorite_button.setAccessibleName("星ランク")
        self.favorite_button.clicked.connect(self._advance_rating)
        self.set_rating(rating)
        name_row.addWidget(self.missing_badge, 0, Qt.AlignmentFlag.AlignTop)
        name_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        self._available_details = self._file_details(path)
        if relative_hint:
            self._available_details = (
                f"場所: {relative_hint}\n{self._available_details}"
            )
        self.details = QLabel(self._available_details)
        self.details.setObjectName("cardDetails")
        self.details.setWordWrap(True)

        self.tag_bar = ResponsiveTagBar()
        self.tag_labels = self.tag_bar.labels
        self.extra_tags_label = self.tag_bar.extra_label
        self.memo_indicator = QLabel()
        self.memo_indicator.setObjectName("cardMemoIndicator")
        self.memo_indicator.setFixedSize(20, 25)
        self._memo_pixmap = memo_pixmap()
        self._empty_memo_pixmap = QPixmap(18, 18)
        self._empty_memo_pixmap.fill(Qt.GlobalColor.transparent)
        self._memo_text = ""
        self.memo_excerpt = QLabel()
        self.memo_excerpt.setObjectName("cardMemoExcerpt")
        self.memo_excerpt.setToolTip("")
        self.memo_excerpt.hide()

        self.summary_widget = QWidget()
        self.summary_widget.setObjectName("cardSummary")
        self._summary_row = QHBoxLayout(self.summary_widget)
        self._summary_row.setContentsMargins(0, 0, 0, 0)
        self._summary_row.setSpacing(4)

        self.side_info = QWidget()
        self.side_info.setObjectName("portraitCardInfo")
        side_layout = QVBoxLayout(self.side_info)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(2)
        self._side_details_slot = QVBoxLayout()
        self._side_details_slot.setContentsMargins(0, 0, 0, 0)
        self._side_tag_slot = QVBoxLayout()
        self._side_tag_slot.setContentsMargins(0, 0, 0, 0)
        self._side_rating_slot = QHBoxLayout()
        self._side_rating_slot.setContentsMargins(0, 0, 0, 0)
        self._side_memo_slot = QHBoxLayout()
        self._side_memo_slot.setContentsMargins(0, 0, 0, 0)
        self._side_memo_text_slot = QVBoxLayout()
        self._side_memo_text_slot.setContentsMargins(0, 0, 0, 0)
        side_footer = QHBoxLayout()
        side_footer.setContentsMargins(0, 0, 0, 0)
        side_footer.addLayout(self._side_rating_slot)
        side_footer.addStretch(1)
        side_footer.addLayout(self._side_memo_slot)
        side_layout.addLayout(self._side_details_slot)
        side_layout.addLayout(self._side_tag_slot)
        side_layout.addLayout(self._side_memo_text_slot)
        side_layout.addStretch(1)
        side_layout.addLayout(side_footer)

        self.below_info = QWidget()
        self.below_info.setObjectName("belowCardInfo")
        below_layout = QVBoxLayout(self.below_info)
        below_layout.setContentsMargins(0, 0, 0, 0)
        below_layout.setSpacing(2)
        self._below_details_slot = QVBoxLayout()
        self._below_details_slot.setContentsMargins(0, 0, 0, 0)
        self._below_summary_slot = QVBoxLayout()
        self._below_summary_slot.setContentsMargins(0, 0, 0, 0)
        self._below_memo_text_slot = QVBoxLayout()
        self._below_memo_text_slot.setContentsMargins(0, 0, 0, 0)
        below_layout.addLayout(self._below_details_slot)
        below_layout.addLayout(self._below_memo_text_slot)
        below_layout.addLayout(self._below_summary_slot)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 20, 8, 6)
        layout.setSpacing(5)
        layout.addLayout(name_row)
        media_row = QHBoxLayout()
        media_row.setContentsMargins(0, 0, 0, 0)
        media_row.setSpacing(6)
        media_row.addWidget(self.thumbnail)
        media_row.addWidget(self.side_info, 1)
        layout.addLayout(media_row)
        layout.addWidget(self.below_info)
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self._apply_info_layout(False)
        self.set_tags(tags or [])
        self.set_memo(memo)
        if has_memo and not memo:
            self.set_memo_present(True)
        self.set_missing(missing)

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            self.thumbnail.setText("画像なし")
            return
        self.thumbnail.setText("")
        image_width = pixmap.width()
        image_height = pixmap.height()
        aspect_ratio = image_width / max(1, image_height)
        aspect_class = self._classify_aspect(image_width, image_height)
        portrait = aspect_class == "portrait"
        self._portrait_mode = portrait
        self._aspect_label = (
            "9:16"
            if abs(aspect_ratio - (9 / 16)) <= 0.035
            else "9:16型"
        )
        if portrait:
            portrait_height = 148
            portrait_width = max(72, int(portrait_height * 9 / 16))
            self.thumbnail.setFixedSize(
                portrait_width, portrait_height
            )
        else:
            self.thumbnail.setFixedSize(QSize(*CARD_THUMBNAIL_SIZE))
        self._apply_info_layout(portrait)
        self.thumbnail.setProperty("portrait", portrait)
        self.thumbnail.setProperty("aspectClass", aspect_class)
        self.thumbnail.style().unpolish(self.thumbnail)
        self.thumbnail.style().polish(self.thumbnail)
        self.thumbnail.setPixmap(
            pixmap.scaled(
                self.thumbnail.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.pin_label.move((self.width() - self.pin_label.width()) // 2, 0)
        self.pin_label.raise_()
        available = max(40, self.width() - 76)
        self.name_label.setText(
            self.name_label.fontMetrics().elidedText(
                self._name_text,
                Qt.TextElideMode.ElideRight,
                available,
            )
        )
        self._update_memo_excerpt()

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_user_title(self, title: str) -> None:
        """設定時はユーザータイトル、未設定時は実ファイル名を表示する。"""
        self._user_title = title.strip()
        self._name_text = self._user_title or self.path.name
        self.name_label.setToolTip(
            self._user_title if self._user_title else str(self.path)
        )
        available = max(40, self.width() - 76)
        self.name_label.setText(
            self.name_label.fontMetrics().elidedText(
                self._name_text,
                Qt.TextElideMode.ElideRight,
                available,
            )
        )

    def set_rating(self, rating: int) -> None:
        self.rating = max(0, min(3, rating))
        self.favorite_button.setText(
            "★" * self.rating + "☆" * (3 - self.rating)
        )
        self.favorite_button.setProperty("rating", self.rating)
        self.favorite_button.style().unpolish(self.favorite_button)
        self.favorite_button.style().polish(self.favorite_button)
        self.favorite_button.setToolTip(
            f"現在: 星{self.rating}（クリックで変更）"
            if self.rating
            else "現在: 星なし（クリックで変更）"
        )

    def set_tags(self, tags: list[str]) -> None:
        self._card_tags = list(tags)
        self.tag_bar.set_tags(tags)

    def set_memo_present(self, present: bool) -> None:
        self.has_memo = present
        self.memo_indicator.setPixmap(
            self._memo_pixmap if present else self._empty_memo_pixmap
        )
        self.memo_indicator.setToolTip(
            "保存済みメモがあります" if present else ""
        )
        self.memo_indicator.setAccessibleName(
            "メモあり" if present else "メモなし"
        )
        if not present:
            self._memo_text = ""
            self.memo_excerpt.clear()
            self.memo_excerpt.hide()

    def set_memo(self, memo: str) -> None:
        """保存済みメモを、カード高を変えず冒頭1行へ要約する。"""
        self._memo_text = re.sub(r"\s+", " ", memo).strip()
        self.set_memo_present(bool(self._memo_text))
        self.memo_excerpt.setToolTip(memo if self._memo_text else "")
        self._update_memo_excerpt()

    def _update_memo_excerpt(self) -> None:
        if not self._memo_text:
            self.memo_excerpt.hide()
            return
        available = (
            max(45, self.side_info.width() - 2)
            if self._portrait_mode
            else max(60, self.width() - 16)
        )
        self.memo_excerpt.setText(
            self.memo_excerpt.fontMetrics().elidedText(
                self._memo_text,
                Qt.TextElideMode.ElideRight,
                available,
            )
        )
        self.memo_excerpt.show()

    def set_missing(self, missing: bool) -> None:
        self.missing = missing
        self.setProperty("missing", missing)
        self.missing_badge.setVisible(missing)
        self.details.setText(
            "原本ファイルが見つかりません"
            if missing
            else self._available_details
        )
        if missing:
            self.thumbnail.setPixmap(QPixmap())
            self.thumbnail.setText("ファイルなし")
        self.style().unpolish(self)
        self.style().polish(self)

    def _apply_info_layout(self, portrait: bool) -> None:
        """縦長では情報を右へ、それ以外では画像の下へ移動する。"""
        for slot in (
            self._side_details_slot,
            self._below_details_slot,
            self._side_memo_text_slot,
            self._below_memo_text_slot,
            self._below_summary_slot,
        ):
            slot.removeWidget(self.details)
            slot.removeWidget(self.memo_excerpt)
            slot.removeWidget(self.summary_widget)
        for layout in (
            self._summary_row,
            self._side_tag_slot,
            self._side_rating_slot,
            self._side_memo_slot,
        ):
            layout.removeWidget(self.tag_bar)
            layout.removeWidget(self.favorite_button)
            layout.removeWidget(self.memo_indicator)
        if portrait:
            self._side_details_slot.addWidget(self.details)
            self._side_tag_slot.addWidget(self.tag_bar)
            self._side_memo_text_slot.addWidget(self.memo_excerpt)
            self._side_rating_slot.addWidget(self.favorite_button)
            self._side_memo_slot.addWidget(self.memo_indicator)
            self.summary_widget.hide()
            self.side_info.show()
            self.below_info.hide()
        else:
            self._below_details_slot.addWidget(self.details)
            self._below_memo_text_slot.addWidget(self.memo_excerpt)
            self._below_summary_slot.addWidget(self.summary_widget)
            self._summary_row.addWidget(self.favorite_button)
            self._summary_row.addWidget(self.tag_bar, 1)
            self._summary_row.addWidget(self.memo_indicator)
            self.summary_widget.show()
            self.side_info.hide()
            self.below_info.show()
        self._update_memo_excerpt()

    @staticmethod
    def _classify_aspect(width: int, height: int) -> str:
        """1:1だけを独立させ、他は16:9型か9:16型へ分類する。"""
        if width == height:
            return "square"
        return "portrait" if height > width else "landscape"

    def _advance_rating(self) -> None:
        rating = (self.rating + 1) % 4
        self.set_rating(rating)
        self.ratingChanged.emit(str(self.path), rating)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mousePressEvent(event)

    @staticmethod
    def _file_details(path: Path) -> str:
        size = "サイズ不明"
        try:
            size = _format_file_size(path.stat().st_size)
        except OSError:
            pass

        resolution = "解像度不明"
        duration = ""
        if path.suffix.lower() == ".mp4":
            try:
                info = read_mp4_info(path)
                if info.width is not None and info.height is not None:
                    resolution = f"{info.width}×{info.height}"
                if info.duration_seconds is not None:
                    duration = _format_duration(info.duration_seconds)
            except Mp4ReadError:
                pass
        else:
            image_size = QImageReader(str(path)).size()
            if image_size.isValid():
                resolution = f"{image_size.width()}×{image_size.height()}"

        values = [resolution]
        if duration:
            values.append(duration)
        values.append(size)
        return "  ·  ".join(values)


class FileListPane(QWidget):
    """対応ファイルを実サムネイル付きネタカードで一覧表示する。"""

    fileSelected = Signal(str)
    selectionCleared = Signal(str)
    ratingChangeRequested = Signal(str, int, int)
    openFileRequested = Signal()
    openFolderRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("taliaWorkspace")
        self._video_queue: list[tuple[QListWidgetItem, Path]] = []
        self._current_video_item: QListWidgetItem | None = None
        self._all_paths: list[Path] = []
        self._search_records: list[SearchRecord] = []
        self._ratings: dict[str, int] = {}
        self._tags_by_path: dict[str, dict[str, str]] = {}
        self._tag_usage_counts: dict[str, int] = {}
        self._selected_tag = ""
        self._memo_paths: set[str] = set()
        self._memos_by_path: dict[str, str] = {}
        self._titles_by_path: dict[str, str] = {}
        self._missing_paths: set[str] = set()

        self.character_header = CharacterHeader(
            "タリア",
            "創作のひらめき\n素材ブラウジング",
            "netami.png",
            "left",
        )
        self.count_label = QLabel("0件")
        self.count_label.setObjectName("itemCount")

        self.search_panel = QFrame()
        self.search_panel.setObjectName("searchPanel")
        self.search_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchEdit")
        self.search_edit.setPlaceholderText("検索キーワード")
        self.search_edit.setClearButtonEnabled(True)
        self.target_combo = QComboBox()
        self.target_combo.setObjectName("searchTarget")
        for label, value in (
            ("すべて", "all"),
            ("ファイル名", "filename"),
            ("タイトル", "title"),
            ("Prompt", "prompt"),
            ("Model", "model"),
            ("Seed", "seed"),
        ):
            self.target_combo.addItem(label, value)
        self.format_combo = QComboBox()
        self.format_combo.setObjectName("formatFilter")
        for label, value in (
            ("全形式", "all"),
            ("PNG", "png"),
            ("WEBP", "webp"),
            ("JPG/JPEG", "jpeg"),
            ("MP4", "mp4"),
        ):
            self.format_combo.addItem(label, value)
        self.state_filter = QComboBox()
        self.state_filter.setObjectName("stateFilter")
        for label, value in (
            ("存在するファイル", "existing"),
            ("見つからないファイル", "missing"),
            ("すべて", "all"),
        ):
            self.state_filter.addItem(label, value)
        self.state_filter.setToolTip("原本ファイルの状態で絞り込み")
        self.reset_button = QToolButton()
        self.reset_button.setObjectName("searchReset")
        self.reset_button.setText("リセット")
        self.reset_button.setToolTip("検索条件をすべて解除")
        self.reset_button.setEnabled(False)
        self.rating_filter = QComboBox()
        self.rating_filter.setObjectName("ratingFilter")
        for label, value in (
            ("すべて", -1),
            ("☆", 0),
            ("★", 1),
            ("★★", 2),
            ("★★★", 3),
        ):
            self.rating_filter.addItem(label, value)
        gold = QColor("#d39b16")
        muted = QColor("#8f8474")
        self.rating_filter.setItemData(1, muted, Qt.ItemDataRole.ForegroundRole)
        for index in range(2, 5):
            self.rating_filter.setItemData(
                index, gold, Qt.ItemDataRole.ForegroundRole
            )
        self.rating_filter.setToolTip("星ランクで絞り込み")
        self.tag_filter = QComboBox()
        self.tag_filter.setObjectName("tagFilter")
        self.tag_filter.setEditable(True)
        self.tag_filter.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.tag_filter.setToolTip("タグ名を入力して候補を検索し、1件選択")
        self.tag_filter.lineEdit().setPlaceholderText("タグを検索")
        self.tag_filter.lineEdit().setClearButtonEnabled(True)
        self._tag_completion_model = QStringListModel(self)
        self.tag_completer = QCompleter(self._tag_completion_model, self)
        self.tag_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.tag_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.tag_completer.setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion
        )
        self.tag_completer.setMaxVisibleItems(16)
        self.tag_filter.setCompleter(self.tag_completer)
        self._set_tag_filter_items([])
        self.result_label = QLabel("すべて表示")
        self.result_label.setObjectName("searchResult")
        self.missing_count_label = QLabel("見つからないファイル: 0件")
        self.missing_count_label.setObjectName("missingCount")
        self.missing_count_label.hide()

        self.open_file_button = QToolButton()
        self.open_file_button.setObjectName("sourceButton")
        self.open_file_button.setText("▤  ファイルを開く")
        self.open_file_button.setToolTip("PNG、WEBP、JPG/JPEG、MP4を1件開く")
        self.open_file_button.clicked.connect(self.openFileRequested.emit)
        self.open_folder_button = QToolButton()
        self.open_folder_button.setObjectName("sourceButton")
        self.open_folder_button.setText("▰  フォルダを開く")
        self.open_folder_button.setToolTip("対応ファイルを含むフォルダを開く")
        self.open_folder_button.clicked.connect(self.openFolderRequested.emit)

        source_actions = QHBoxLayout()
        source_actions.setContentsMargins(0, 0, 0, 0)
        source_actions.setSpacing(5)
        source_actions.addWidget(self.open_file_button, 1)
        source_actions.addWidget(self.open_folder_button, 1)

        self.identity_panel = QWidget()
        self.identity_panel.setObjectName("taliaIdentityPanel")
        self.identity_panel.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        identity_layout = QVBoxLayout(self.identity_panel)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(5)
        identity_layout.addWidget(self.character_header)
        identity_layout.addLayout(source_actions)

        count_row = QHBoxLayout()
        count_row.setContentsMargins(0, 0, 0, 0)
        count_row.addStretch(1)
        count_row.addWidget(self.count_label)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(5)
        search_row.addWidget(self.search_edit, 1)
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(5)
        filter_row.addWidget(self.target_combo)
        filter_row.addWidget(self.format_combo)
        filter_row.addWidget(self.state_filter, 1)
        filter_row.addWidget(self.rating_filter)
        filter_row2 = QHBoxLayout()
        filter_row2.setContentsMargins(0, 0, 0, 0)
        filter_row2.setSpacing(5)
        filter_row2.addWidget(self.tag_filter)
        filter_row2.addWidget(self.result_label, 1)
        filter_row2.addWidget(self.missing_count_label)
        filter_row2.addWidget(self.reset_button)
        search_layout = QVBoxLayout(self.search_panel)
        search_layout.setContentsMargins(7, 6, 7, 6)
        search_layout.setSpacing(5)
        search_layout.addLayout(count_row)
        search_layout.addLayout(search_row)
        search_layout.addLayout(filter_row)
        search_layout.addLayout(filter_row2)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._apply_filters)
        self.search_edit.textChanged.connect(self._queue_filter)
        self.target_combo.currentIndexChanged.connect(self._apply_filters)
        self.format_combo.currentIndexChanged.connect(self._apply_filters)
        self.state_filter.currentIndexChanged.connect(self._apply_filters)
        self.rating_filter.currentIndexChanged.connect(self._apply_filters)
        self.tag_filter.activated.connect(self._on_tag_filter_activated)
        self.tag_filter.lineEdit().textEdited.connect(
            self._on_tag_filter_text_edited
        )
        self.tag_completer.activated[str].connect(
            self._on_tag_completion_activated
        )
        self.reset_button.clicked.connect(self.reset_filters)
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("cardList")
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setFlow(QListWidget.Flow.LeftToRight)
        self.list_widget.setWrapping(True)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setGridSize(
            QSize(CARD_WIDTH + 12, CARD_HEIGHT + 12)
        )
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list_widget.setSpacing(7)
        self.list_widget.currentItemChanged.connect(self._on_current_item_changed)

        self._thumbnail_player = QMediaPlayer(self)
        self._video_sink = QVideoSink(self)
        self._thumbnail_player.setVideoOutput(self._video_sink)
        self._video_sink.videoFrameChanged.connect(self._on_video_frame)
        self._thumbnail_player.errorOccurred.connect(self._on_thumbnail_error)

        self.top_panel = QWidget()
        self.top_panel.setObjectName("taliaTopPanel")
        self.top_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.top_layout = QBoxLayout(
            QBoxLayout.Direction.LeftToRight, self.top_panel
        )
        self.top_layout.setContentsMargins(0, 0, 0, 0)
        self.top_layout.setSpacing(8)
        self.top_layout.addWidget(
            self.identity_panel, 0, Qt.AlignmentFlag.AlignTop
        )
        self.top_layout.addWidget(
            self.search_panel, 1, Qt.AlignmentFlag.AlignTop
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self.top_panel)
        layout.addWidget(self.list_widget, 1)
        self._update_top_layout(760)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_top_layout(event.size().width())

    def _update_top_layout(self, width: int) -> None:
        """狭い時だけ上下配置へ戻し、操作部品の潰れを防止する。"""
        wide = width >= 720
        direction = (
            QBoxLayout.Direction.LeftToRight
            if wide
            else QBoxLayout.Direction.TopToBottom
        )
        if self.top_layout.direction() != direction:
            self.top_layout.setDirection(direction)
        self.identity_panel.setMaximumWidth(285 if wide else 16777215)
        self.identity_panel.setMinimumWidth(255 if wide else 0)
        self.search_panel.setMinimumWidth(410 if wide else 0)
        self.top_layout.invalidate()
        self.top_panel.updateGeometry()
        if self.layout() is not None:
            self.layout().invalidate()
            self.layout().activate()
        QTimer.singleShot(0, self._activate_top_layout)

    def _activate_top_layout(self) -> None:
        """方向変更後のsizeHintを次のイベント周期で親へ反映する。"""
        self.top_layout.activate()
        self.top_panel.updateGeometry()
        if self.layout() is not None:
            self.layout().activate()

    def set_paths(
        self,
        paths: list[Path],
        select_first: bool = True,
        source_directory: Path | None = None,
        ratings: dict[str, int] | None = None,
        tags_by_path: dict[str, list[str]] | None = None,
        all_tags: list[str] | None = None,
        tag_usage_counts: dict[str, int] | None = None,
        memo_paths: set[str] | None = None,
        memos_by_path: dict[str, str] | None = None,
        titles_by_path: dict[str, str] | None = None,
        missing_paths: set[str] | None = None,
    ) -> None:
        self._all_paths = list(paths)
        self._source_directory = source_directory
        self._ratings = dict(ratings or {})
        self._tags_by_path = {
            path.casefold(): {tag.casefold(): tag for tag in tags}
            for path, tags in (tags_by_path or {}).items()
        }
        self._tag_usage_counts = dict(tag_usage_counts or {})
        self._memos_by_path = {
            path.casefold(): memo
            for path, memo in (memos_by_path or {}).items()
            if memo.strip()
        }
        self._memo_paths = {
            path.casefold() for path in (memo_paths or set())
        } | set(self._memos_by_path)
        self._titles_by_path = {
            path.casefold(): title.strip()
            for path, title in (titles_by_path or {}).items()
            if title.strip()
        }
        self._missing_paths = {
            path.casefold() for path in (missing_paths or set())
        }
        self._search_records = [
            self._build_search_record(path) for path in paths
        ]
        self._set_tag_filter_items(
            all_tags or [], usage_counts=self._tag_usage_counts
        )
        self._set_filter_controls("", "all", "all", -1, "", "existing")
        missing_count = sum(
            self._path_key(path) in self._missing_paths for path in paths
        )
        self.missing_count_label.setText(
            f"見つからないファイル: {missing_count}件"
        )
        self.missing_count_label.setVisible(missing_count > 0)
        self._apply_filters(select_first=select_first)

    def _populate_paths(
        self, paths: list[Path], select_first: bool = True
    ) -> None:
        selected_item = self.list_widget.currentItem()
        selected_path = (
            selected_item.data(Qt.ItemDataRole.UserRole)
            if selected_item is not None
            else None
        )
        self._cancel_video_thumbnails()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        name_counts: dict[str, int] = {}
        for path in paths:
            key = path.name.casefold()
            name_counts[key] = name_counts.get(key, 0) + 1
        for path in paths:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            item.setSizeHint(QSize(CARD_WIDTH, CARD_HEIGHT))
            key = self._path_key(path)
            relative_hint = ""
            if (
                self._source_directory is not None
                and name_counts[path.name.casefold()] > 1
            ):
                try:
                    relative_hint = str(
                        path.relative_to(self._source_directory)
                    )
                except ValueError:
                    relative_hint = str(path)
            card = FileCard(
                path,
                self._ratings.get(key, 0),
                self.get_tags(path),
                key in self._memo_paths,
                key in self._missing_paths,
                memo=self._memos_by_path.get(key, ""),
                user_title=self._titles_by_path.get(key, ""),
                relative_hint=relative_hint,
            )
            card.activated.connect(
                lambda item=item: self.list_widget.setCurrentItem(item)
            )
            card.ratingChanged.connect(self._on_rating_changed)
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, card)
            if key not in self._missing_paths:
                pixmap = self._image_thumbnail(path)
                if not pixmap.isNull():
                    card.set_thumbnail(pixmap)
                elif path.suffix.lower() == ".mp4":
                    self._video_queue.append((item, path))
                else:
                    card.set_thumbnail(QPixmap())
        self.list_widget.blockSignals(False)
        if paths and select_first:
            selected_row = next(
                (
                    index
                    for index, path in enumerate(paths)
                    if str(path) == selected_path
                ),
                0,
            )
            self.list_widget.setCurrentRow(selected_row)
        elif not paths:
            self.selectionCleared.emit("検索条件に一致するネタがありません。")
        self._advance_video_thumbnail()

    def clear(self) -> None:
        self.set_paths([], select_first=False)

    def release_media(self) -> None:
        self._cancel_video_thumbnails()

    def reset_filters(self) -> None:
        self._search_timer.stop()
        self._set_filter_controls("", "all", "all", -1, "", "existing")
        self._apply_filters()

    def _set_filter_controls(
        self,
        query: str,
        target: str,
        format_name: str,
        rating: int,
        tag: str,
        state: str,
    ) -> None:
        widgets = (
            self.search_edit,
            self.target_combo,
            self.format_combo,
            self.rating_filter,
            self.tag_filter,
            self.state_filter,
        )
        for widget in widgets:
            widget.blockSignals(True)
        self.search_edit.setText(query)
        self.target_combo.setCurrentIndex(
            max(0, self.target_combo.findData(target))
        )
        self.format_combo.setCurrentIndex(
            max(0, self.format_combo.findData(format_name))
        )
        self.rating_filter.setCurrentIndex(
            max(0, self.rating_filter.findData(rating))
        )
        self.state_filter.setCurrentIndex(
            max(0, self.state_filter.findData(state))
        )
        tag_index = self.tag_filter.findData(tag)
        self.tag_filter.setCurrentIndex(tag_index if tag_index >= 0 else 0)
        self._selected_tag = tag if tag_index >= 0 else ""
        for widget in widgets:
            widget.blockSignals(False)

    def _queue_filter(self) -> None:
        self._search_timer.start()

    def _apply_filters(self, _index: int = -1, select_first: bool = True) -> None:
        self._search_timer.stop()
        query = self.search_edit.text().strip()
        target = str(self.target_combo.currentData())
        format_name = str(self.format_combo.currentData())
        rating = int(self.rating_filter.currentData())
        tag = self._selected_tag
        state = str(self.state_filter.currentData())
        active = (
            bool(query)
            or target != "all"
            or format_name != "all"
            or rating >= 0
            or bool(tag)
            or state != "existing"
        )
        matching = [
            record.path
            for record in self._search_records
            if record.matches(query, target, format_name)
            and (
                state == "all"
                or (state == "missing")
                == (self._path_key(record.path) in self._missing_paths)
            )
            and (
                rating < 0
                or self._ratings.get(self._path_key(record.path), 0) == rating
            )
            and (
                not tag
                or tag.casefold()
                in self._tags_by_path.get(self._path_key(record.path), {})
            )
        ]
        self._populate_paths(matching, select_first=select_first)
        self._update_result_display(len(matching), active)

    def _update_result_display(self, result_count: int, active: bool) -> None:
        total = len(self._all_paths)
        self.count_label.setText(
            f"{result_count} / {total}件"
            if active or result_count != total
            else f"{total}件"
        )
        if active and result_count == 0:
            message = "該当するネタはありません"
        elif active:
            message = f"検索結果 {result_count}件"
        else:
            message = f"存在するファイル {result_count}件"
        self.result_label.setText(message)
        self.search_panel.setProperty("filterActive", active)
        self.search_panel.style().unpolish(self.search_panel)
        self.search_panel.style().polish(self.search_panel)
        self.reset_button.setEnabled(active)

    def all_paths(self) -> list[Path]:
        """現在の一覧ソース（フィルター適用前）を返す。"""
        return list(self._all_paths)

    def set_rating(self, path: Path, rating: int) -> None:
        """表示と絞り込みが参照する星ランクを更新する。"""
        key = self._path_key(path)
        if rating:
            self._ratings[key] = rating
        else:
            self._ratings.pop(key, None)
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if self._path_key(Path(item.data(Qt.ItemDataRole.UserRole))) == key:
                card = self.list_widget.itemWidget(item)
                if isinstance(card, FileCard):
                    card.set_rating(rating)
                break
        if int(self.rating_filter.currentData()) >= 0:
            self._apply_filters()

    def get_rating(self, path: Path) -> int:
        return self._ratings.get(self._path_key(path), 0)

    def get_tags(self, path: Path) -> list[str]:
        key = self._path_key(path)
        tags = self._tags_by_path.get(key, {})
        return sorted(tags.values(), key=str.casefold)

    def select_path(self, path: Path) -> bool:
        key = self._path_key(path)
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            item_path = Path(item.data(Qt.ItemDataRole.UserRole))
            if self._path_key(item_path) == key:
                self.list_widget.setCurrentItem(item)
                return True
        self.list_widget.setCurrentItem(None)
        return False

    def set_tags(
        self,
        path: Path,
        tags: list[str],
        all_tags: list[str],
        tag_usage_counts: dict[str, int] | None = None,
    ) -> None:
        key = self._path_key(path)
        if tags:
            self._tags_by_path[key] = {tag.casefold(): tag for tag in tags}
        else:
            self._tags_by_path.pop(key, None)
        selected = self._selected_tag
        self._tag_usage_counts = dict(tag_usage_counts or {})
        self._set_tag_filter_items(
            all_tags, selected, self._tag_usage_counts
        )
        self._refresh_search_record(path)
        if selected or self.search_edit.text().strip():
            self._apply_filters()
        else:
            card = self._visible_card(path)
            if card is not None:
                card.set_tags(tags)

    def set_memo_presence(self, path: Path, present: bool) -> None:
        """保存済みメモの有無を保持し、表示中カードへ即時反映する。"""
        key = self._path_key(path)
        if present:
            self._memo_paths.add(key)
        else:
            self._memo_paths.discard(key)
            self._memos_by_path.pop(key, None)
        card = self._visible_card(path)
        if card is not None:
            card.set_memo_present(present)

    def set_memo(self, path: Path, memo: str) -> None:
        """保存結果をメモ有無とカード抜粋へ即時反映する。"""
        key = self._path_key(path)
        if memo.strip():
            self._memo_paths.add(key)
            self._memos_by_path[key] = memo
        else:
            self._memo_paths.discard(key)
            self._memos_by_path.pop(key, None)
        self._refresh_search_record(path)
        if self.search_edit.text().strip():
            self._apply_filters()
            return
        card = self._visible_card(path)
        if card is not None:
            card.set_memo(memo)

    def set_user_title(self, path: Path, title: str) -> None:
        """保存済みタイトルをカードと検索索引へ即時反映する。"""
        key = self._path_key(path)
        normalized = title.strip()
        if normalized:
            self._titles_by_path[key] = normalized
        else:
            self._titles_by_path.pop(key, None)
        self._refresh_search_record(path)
        if self.search_edit.text().strip():
            self._apply_filters()
            return
        card = self._visible_card(path)
        if card is not None:
            card.set_user_title(normalized)

    def get_user_title(self, path: Path) -> str:
        return self._titles_by_path.get(self._path_key(path), "")

    def _build_search_record(self, path: Path) -> SearchRecord:
        key = self._path_key(path)
        return build_search_record(
            path,
            user_title=self._titles_by_path.get(key, ""),
            tags=self.get_tags(path),
            memo=self._memos_by_path.get(key, ""),
        )

    def _refresh_search_record(self, path: Path) -> None:
        key = self._path_key(path)
        for index, record in enumerate(self._search_records):
            if self._path_key(record.path) == key:
                self._search_records[index] = self._build_search_record(path)
                return

    def _visible_card(self, path: Path) -> FileCard | None:
        key = self._path_key(path)
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            item_path = Path(item.data(Qt.ItemDataRole.UserRole))
            if self._path_key(item_path) == key:
                card = self.list_widget.itemWidget(item)
                return card if isinstance(card, FileCard) else None
        return None

    def _set_tag_filter_items(
        self,
        tags: list[str],
        selected: str = "",
        usage_counts: dict[str, int] | None = None,
    ) -> None:
        counts = usage_counts or {}
        count_by_key = {name.casefold(): count for name, count in counts.items()}
        ordered = sorted(
            tags,
            key=lambda name: (
                -count_by_key.get(name.casefold(), 0),
                name.casefold(),
            ),
        )
        self.tag_filter.blockSignals(True)
        self.tag_filter.clear()
        self.tag_filter.addItem("全タグ", "")
        for tag in ordered:
            self.tag_filter.addItem(tag, tag)
        index = self.tag_filter.findData(selected)
        self.tag_filter.setCurrentIndex(index if index >= 0 else 0)
        self._selected_tag = selected if index >= 0 else ""
        self.tag_filter.blockSignals(False)
        self._tag_completion_model.setStringList(ordered)

    def _on_tag_filter_activated(self, index: int) -> None:
        self._selected_tag = str(self.tag_filter.itemData(index) or "")
        self._apply_filters()

    def _on_tag_completion_activated(self, text: str) -> None:
        index = next(
            (
                item_index
                for item_index in range(1, self.tag_filter.count())
                if self.tag_filter.itemText(item_index).casefold()
                == text.casefold()
            ),
            -1,
        )
        if index < 0:
            return
        self.tag_filter.blockSignals(True)
        self.tag_filter.setCurrentIndex(index)
        self.tag_filter.blockSignals(False)
        self._selected_tag = str(self.tag_filter.itemData(index) or "")
        self._apply_filters()

    def _on_tag_filter_text_edited(self, _text: str) -> None:
        if not self._selected_tag:
            return
        self._selected_tag = ""
        self._apply_filters()

    def _on_rating_changed(self, path_text: str, rating: int) -> None:
        path = Path(path_text)
        previous_rating = self._ratings.get(self._path_key(path), 0)
        self.set_rating(path, rating)
        self.ratingChangeRequested.emit(path_text, rating, previous_rating)

    @staticmethod
    def _path_key(path: Path) -> str:
        try:
            return str(path.resolve()).casefold()
        except OSError:
            return str(path.absolute()).casefold()

    def _on_current_item_changed(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        if previous is not None:
            previous_card = self.list_widget.itemWidget(previous)
            if isinstance(previous_card, FileCard):
                previous_card.set_selected(False)
        if current is not None:
            current_card = self.list_widget.itemWidget(current)
            if isinstance(current_card, FileCard):
                current_card.set_selected(True)
            self.fileSelected.emit(current.data(Qt.ItemDataRole.UserRole))

    @staticmethod
    def _image_thumbnail(path: Path) -> QPixmap:
        if path.suffix.lower() not in {".png", ".webp", ".jpg", ".jpeg"}:
            return QPixmap()
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid():
            size.scale(QSize(*CARD_THUMBNAIL_SIZE), Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(size)
        return QPixmap.fromImage(reader.read())

    def _advance_video_thumbnail(self) -> None:
        if self._current_video_item is not None or not self._video_queue:
            return
        self._current_video_item, path = self._video_queue.pop(0)
        self._thumbnail_player.setSource(QUrl.fromLocalFile(str(path)))
        self._thumbnail_player.play()

    def _on_video_frame(self, frame: QVideoFrame) -> None:
        if self._current_video_item is None or not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        card = self.list_widget.itemWidget(self._current_video_item)
        if isinstance(card, FileCard):
            card.set_thumbnail(QPixmap.fromImage(image))
        self._finish_current_video_thumbnail()

    def _on_thumbnail_error(
        self, _error: QMediaPlayer.Error, _message: str
    ) -> None:
        if self._current_video_item is not None:
            card = self.list_widget.itemWidget(self._current_video_item)
            if isinstance(card, FileCard):
                card.set_thumbnail(QPixmap())
            self._finish_current_video_thumbnail()

    def _finish_current_video_thumbnail(self) -> None:
        self._thumbnail_player.stop()
        self._thumbnail_player.setSource(QUrl())
        self._current_video_item = None
        QTimer.singleShot(0, self._advance_video_thumbnail)

    def _cancel_video_thumbnails(self) -> None:
        self._thumbnail_player.stop()
        self._thumbnail_player.setSource(QUrl())
        self._current_video_item = None
        self._video_queue.clear()


def _format_file_size(size: int) -> str:
    if size < 1024:
        return f"{size:,} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _format_duration(seconds: float) -> str:
    total = round(seconds)
    minutes, remaining = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return (
        f"{hours}:{minutes:02d}:{remaining:02d}"
        if hours
        else f"{minutes}:{remaining:02d}"
    )
