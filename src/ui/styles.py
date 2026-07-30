"""アプリ全体のテーマ、余白、寸法を一か所で管理する。"""

from string import Template

from ui.skin import build_skin_style


# 将来のテーマ差し替えでは、まずこの配色表を置き換える。
THEME = {
    "ink": "#2e2933",
    "muted_ink": "#6f6875",
    "window": "#faf9f5",
    "paper": "#fffefa",
    "paper_soft": "#fbfaf6",
    "paper_line": "#ddd8cc",
    "lavender": "#82538f",
    "lavender_dark": "#553260",
    "lavender_soft": "#ead9ed",
    "lavender_border": "#aa82b2",
    "mint_soft": "#e4f3eb",
    "mint_border": "#add4bd",
    "yellow_soft": "#fff2d8",
    "yellow_border": "#e9ca91",
    "pink_soft": "#fbe8ef",
    "pink_border": "#e5b8c9",
    "control": "#ffffff",
    "control_hover": "#f2ecfc",
    "divider": "#d8d0df",
    "preview": "#fcfcfa",
    "video": "#17151a",
    "page_edge": "#cdbb9f",
    "page_depth": "#e5d8c4",
    "binding": "#eadfce",
    "leather": "#684137",
    "leather_dark": "#3f261f",
    "leather_light": "#9b6951",
}

# フォントは役割ごとに分け、先頭のフォントがない環境でも
# Yu Gothic UI / Meiryo UIで日本語表示を維持する。
TYPOGRAPHY = {
    "font_display": '"UD Digi Kyokasho NP", "Yu Gothic UI", "Meiryo UI"',
    "font_tab": '"Zen Maru Gothic", "UD Digi Kyokasho NP", "Yu Gothic UI", "Meiryo UI"',
    "font_ui": '"Yu Gothic UI", "Meiryo UI", "Meiryo"',
    "font_technical": '"Cascadia Mono", "Consolas", "BIZ UDGothic", "Yu Gothic UI", "Meiryo UI"',
    "size_body": "10pt",
    "size_small": "8.5pt",
    "size_heading": "10.5pt",
    "size_card_heading": "11pt",
    "size_character": "19pt",
    "size_technical": "9pt",
    "weight_medium": "600",
    "weight_bold": "700",
}

METRICS = {
    "radius_small": 5,
    "radius_card": 9,
    "pane_gap": 10,
}


BASE_STYLE = Template(
    """
QMainWindow, QWidget {
    background: $window;
    color: $ink;
    font-family: $font_ui;
    font-size: $size_body;
}
QWidget#applicationWorkspace {
    background: #f2eff8;
}
QWidget#previewPane, QWidget#metadataPane {
    background: transparent;
}
QWidget#characterHeader {
    background: rgba(255, 252, 241, 235);
    border: 1px solid #c9ad8f;
    border-radius: 13px;
}
QWidget#characterHeader[side="left"] {
    border-left: 5px solid #9d7bd6;
}
QWidget#characterHeader[side="right"] {
    border-left: 5px solid #c783a8;
}
QLabel#characterAvatar {
    background: #eee6fb;
    border: 2px solid #cbb5e7;
    border-radius: 34px;
    color: $lavender_dark;
    font-size: 22pt;
    font-weight: 700;
}
QLabel#characterName {
    background: transparent;
    color: $lavender_dark;
    font-family: $font_display;
    font-size: $size_character;
    font-weight: $weight_bold;
}
QLabel#characterRole {
    background: transparent;
    color: #55485d;
    font-family: $font_ui;
    font-size: $size_body;
    font-weight: $weight_medium;
}
QMenuBar, QMenu {
    background: $paper;
    color: $ink;
}
QMenuBar {
    border-bottom: 1px solid $lavender_border;
}
QMenuBar::item:selected, QMenu::item:selected {
    background: $lavender_soft;
    color: $lavender_dark;
}
QLabel#paneTitle {
    background: $lavender_soft;
    border-left: 4px solid $lavender;
    border-radius: 5px;
    color: $lavender_dark;
    font-family: $font_display;
    font-size: $size_heading;
    font-weight: $weight_bold;
    padding: 6px 9px;
}
QLabel#headingIcon {
    background: transparent;
    padding-left: 3px;
}
QLabel#paneTitle[accent="mint"] {
    background: $mint_soft;
    border-left-color: $mint_border;
    color: #37684d;
}
QLabel#paneTitle[accent="pink"] {
    background: $pink_soft;
    border-left-color: $pink_border;
    color: #87516a;
}
QListWidget, QPlainTextEdit, QScrollArea, QTabWidget::pane {
    background: $paper;
    border: 1px solid $paper_line;
    border-radius: 7px;
}
QStackedWidget#previewSurface {
    background: $preview;
    border: 2px solid #cfc5d7;
    border-radius: 10px;
}
QWidget#mediaControls {
    background: #f4f0f7;
    border: 1px solid #d4cbd9;
    border-radius: 7px;
}
QWidget#missingPreview {
    background: #fffaf5;
}
QLabel#missingPreviewTitle {
    color: #934f45;
    font-family: $font_display;
    font-size: 15pt;
    font-weight: $weight_bold;
}
QLabel#missingInfoHeading {
    color: $muted_ink;
    font-weight: $weight_bold;
    min-width: 110px;
}
QLabel#missingInfoValue {
    background: #fffefd;
    border-bottom: 1px solid $paper_line;
    font-family: $font_technical;
    padding: 4px 6px;
}
QPushButton#missingAction, QPushButton#deleteRecordButton {
    border-radius: 7px;
    min-height: 30px;
    padding: 4px 12px;
}
QPushButton#missingAction {
    background: $lavender_soft;
    border: 1px solid $lavender_border;
    color: $lavender_dark;
}
QPushButton#deleteRecordButton {
    background: #fff4f1;
    border: 1px solid #d5a092;
    color: #934f45;
}
QVideoWidget {
    background: $video;
}
QListWidget#cardList {
    background: $paper_soft;
    border-color: $lavender_border;
    padding: 7px;
}
QFrame#searchPanel {
    background: rgba(255, 250, 247, 232);
    border: 1px solid $paper_line;
    border-radius: 8px;
}
QToolButton#sourceButton {
    background: #faf3ff;
    border: 1px solid #cdb7e6;
    color: $lavender_dark;
    min-height: 27px;
    padding: 2px 10px;
    font-weight: 600;
}
QToolButton#sourceButton:hover {
    background: #eee3fb;
    border-color: $lavender;
}
QFrame#searchPanel[filterActive="true"] {
    background: $lavender_soft;
    border-color: $lavender;
}
QLineEdit#searchEdit, QComboBox#searchTarget, QComboBox#formatFilter,
QComboBox#stateFilter {
    background: $control;
    border: 1px solid #cfc4d8;
    border-radius: 6px;
    min-height: 25px;
    padding: 2px 7px;
}
QLineEdit#searchEdit:focus, QComboBox#searchTarget:focus,
QComboBox#formatFilter:focus, QComboBox#stateFilter:focus {
    border: 2px solid $lavender;
}
QComboBox#stateFilter {
    min-width: 128px;
}
QLabel#missingCount {
    background: #fbe3dc;
    border: 1px solid #d5a092;
    border-radius: 6px;
    color: #934f45;
    font-size: $size_small;
    padding: 3px 6px;
}
QToolButton#searchReset {
    min-height: 25px;
    padding: 2px 8px;
}
QComboBox#ratingFilter {
    min-width: 78px;
    min-height: 25px;
    padding: 2px 8px;
}
QComboBox#tagFilter {
    min-width: 105px;
    max-width: 175px;
    min-height: 25px;
    padding: 2px 7px;
}
QLabel#searchResult {
    color: $muted_ink;
    font-size: $size_small;
    padding-left: 4px;
}
QListWidget#cardList::item {
    background: transparent;
    border: none;
    padding: 0;
}
QListWidget#cardList::item:selected {
    background: transparent;
}
QFrame#fileCard {
    background: $paper;
    border: 1px solid #ddd2c2;
    border-radius: 9px;
}
QFrame#fileCard[selected="true"] {
    background: $lavender_soft;
    border: 2px solid $lavender_dark;
}
QFrame#fileCard[missing="true"] {
    background: #fff8f3;
    border: 1px dashed #c98272;
}
QFrame#fileCard[selected="true"][missing="true"] {
    background: #f4eaff;
    border: 2px solid $lavender_dark;
}
QFrame#fileCard[landscape="true"] {
    border-radius: 3px;
}
QLabel#missingBadge {
    background: #fbe3dc;
    border: 1px solid #d5a092;
    border-radius: 6px;
    color: #934f45;
    font-size: 8pt;
    font-weight: 700;
    padding: 2px 4px;
}
QToolButton#favoriteButton {
    background: transparent;
    border: none;
    color: #9a8d78;
    font-size: 10pt;
    min-width: 56px;
    max-width: 56px;
    min-height: 20px;
    padding: 0;
}
QToolButton#favoriteButton[rating="0"] {
    color: #928777;
}
QToolButton#favoriteButton[rating="1"],
QToolButton#favoriteButton[rating="2"],
QToolButton#favoriteButton[rating="3"] {
    color: #d39b16;
}
QLabel#ratingLabel {
    color: $muted_ink;
    font-size: 8.5pt;
    padding-right: 2px;
}
QToolButton#ratingButton {
    background: transparent;
    border: none;
    color: #928777;
    font-size: 15pt;
    min-width: 84px;
    max-width: 84px;
    min-height: 24px;
    padding: 0;
}
QToolButton#ratingButton:hover {
    color: #bd8b18;
    background: #fff7d9;
}
QToolButton#ratingButton[rating="1"],
QToolButton#ratingButton[rating="2"],
QToolButton#ratingButton[rating="3"] {
    color: #d39b16;
}
QToolButton#favoriteButton:hover {
    color: #bd8b18;
    background: #fff7d9;
}
QLabel#cardTag {
    background: $mint_soft;
    border: 1px solid $mint_border;
    border-radius: 6px;
    color: #405f4b;
    font-size: 8pt;
    max-width: 104px;
    padding: 1px 4px;
}
QWidget#cardTagBar {
    background: transparent;
    font-size: 8pt;
}
QLabel#cardTagExtra {
    color: $lavender_dark;
    font-size: 8pt;
    font-weight: 700;
    padding: 1px 2px;
}
QLabel#cardMemoIndicator {
    color: #8b6d37;
    font-size: 10pt;
    min-width: 20px;
    max-width: 20px;
}
QLabel#cardMemoExcerpt {
    background: rgba(255, 255, 255, 105);
    border: none;
    border-left: 2px solid #c9a9cf;
    color: #6e6171;
    font-size: 7.5pt;
    min-height: 13px;
    max-height: 13px;
    padding-left: 4px;
}
QLabel#cardThumbnail {
    background: #ebe8e3;
    border: 1px solid #d5cec5;
    border-radius: 6px;
    color: $muted_ink;
    font-size: 8pt;
}
QWidget#portraitCardInfo {
    background: rgba(255, 255, 255, 120);
    border: none;
}
QWidget#portraitCardInfo QLabel#cardDetails {
    font-size: 7.5pt;
}
QWidget#portraitCardInfo QToolButton#favoriteButton {
    min-width: 52px;
    max-width: 52px;
}
QLabel#cardFileName {
    color: $ink;
    font-family: $font_technical;
    font-weight: $weight_medium;
}
QLabel#cardDetails, QLabel#itemCount {
    color: $muted_ink;
    font-size: $size_small;
}
QLabel#landscapeThumbnail {
    background: #ebe8e3;
    border: 1px solid #d5cec5;
    border-radius: 6px;
    color: $muted_ink;
    font-size: 8pt;
}
QWidget#landscapeInfo {
    background: transparent;
}
QLabel#landscapeFileName {
    color: $ink;
    font-family: $font_technical;
    font-size: 10pt;
    font-weight: $weight_medium;
}
QLabel#landscapeMetadata {
    color: $muted_ink;
    font-size: 8pt;
}
QLabel#landscapeFieldHeading {
    color: $ink;
    font-size: 8pt;
    font-weight: $weight_bold;
}
QLabel#landscapeTags {
    color: #405f4b;
    font-size: 8pt;
    min-height: 16px;
}
QLabel#landscapeMemo {
    background: rgba(255, 255, 255, 105);
    border: none;
    border-left: 2px solid #c9a9cf;
    color: #6e6171;
    font-size: 8pt;
    padding: 2px 0 2px 8px;
}
QLabel#formatBadge {
    background: $lavender_soft;
    border: 1px solid $lavender_border;
    border-radius: 7px;
    color: $lavender_dark;
    font-size: 8pt;
    font-weight: 600;
    padding: 2px 3px;
}
QLabel#formatBadge[format="PNG"] {
    background: $mint_soft;
    border-color: $mint_border;
    color: #316c49;
}
QLabel#formatBadge[format="WEBP"] {
    background: $yellow_soft;
    border-color: $yellow_border;
    color: #805420;
}
QTabWidget#metadataTabs::pane {
    background: #fffefa;
    border: 1px solid #b99876;
    border-radius: 0 8px 8px 8px;
    top: -2px;
}
QTabWidget#metadataTabs QTabBar::tab {
    background: #e9d9bc;
    border: 1px solid #b99876;
    border-bottom-color: #8c684c;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    color: #4d392f;
    font-family: $font_tab;
    font-size: 8.5pt;
    font-weight: $weight_medium;
    min-height: 20px;
    max-height: 20px;
    min-width: 64px;
    margin: 0 2px 0 0;
    padding: 2px 6px;
}
QTabWidget#metadataTabs QTabBar::tab:selected {
    background: #fff3cf;
    border: 2px solid #7d4d43;
    border-bottom-color: #fff3cf;
    color: #65372f;
    font-weight: $weight_bold;
    margin-top: 0;
    padding-bottom: 2px;
}
QTabWidget#metadataTabs QTabBar::tab:hover:!selected {
    background: #f5e6c8;
    border-color: $leather_light;
}
QWidget#persistentRating {
    background: #fff8dc;
    border: 1px solid #ead29b;
    border-radius: 9px;
    min-height: 34px;
}
QWidget#characterHeader QLabel#ratingLabel {
    font-size: 10pt;
    font-weight: 600;
}
QWidget#characterHeader QToolButton#ratingButton {
    font-size: 17pt;
    min-width: 96px;
    max-width: 96px;
    min-height: 30px;
}
QWidget#organizerTab {
    background: $paper_soft;
}
QLabel#memoPlaceholder {
    background: $yellow_soft;
    border: 1px dashed $yellow_border;
    border-radius: 8px;
    color: $muted_ink;
    padding: 20px;
}
QTabBar::tab {
    background: #f6f0e8;
    border: 1px solid $paper_line;
    border-bottom-color: #cfc4b7;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    color: #51495a;
    margin-right: 3px;
    padding: 8px 11px;
}
QTabBar::tab:hover {
    background: $yellow_soft;
}
QTabBar::tab:selected {
    background: $lavender_soft;
    border-color: $lavender_border;
    border-bottom-color: $lavender_soft;
    color: $lavender_dark;
    font-weight: 700;
}
QWidget#paperPage {
    background: #fffefa;
}
QFrame#infoCard {
    background: $paper;
    border: 1px solid #d9c9b3;
    border-radius: 9px;
}
QFrame#titleCard, QFrame#tagCard {
    background: $paper;
    border: 1px solid $paper_line;
    border-radius: 9px;
}
QLineEdit#titleInput {
    background: $control;
    border: 1px solid #cfc4d8;
    border-radius: 6px;
    min-height: 27px;
    padding: 2px 7px;
}
QLineEdit#titleInput:focus {
    border-color: $lavender_border;
}
QLabel#titleCount {
    color: $muted_ink;
    font-size: $size_small;
}
QScrollArea#tagScroll {
    background: transparent;
    border: 1px solid #ded4c6;
    border-radius: 6px;
}
QWidget#tagContainer {
    background: $paper;
}
QToolButton#tagChip {
    background: #eef7e6;
    border: 1px solid #b9d5a6;
    border-radius: 9px;
    color: #40563a;
    min-height: 23px;
    padding: 1px 8px;
}
QToolButton#tagChip:hover {
    background: #fbe6e9;
    border-color: #dbabb2;
    color: #78434b;
}
QLabel#tagHelp, QLabel#tagEmpty {
    color: $muted_ink;
    font-size: $size_small;
}
QLineEdit#tagInput {
    background: $control;
    border: 1px solid #cfc4d8;
    border-radius: 6px;
    min-height: 25px;
    padding: 2px 7px;
}
QToolButton#tagAddButton {
    min-height: 25px;
    padding: 2px 10px;
}
QPlainTextEdit#memoEditor {
    background: #fffaf0;
    border: 1px solid $yellow_border;
    border-radius: 8px;
    color: $ink;
    font-family: $font_ui;
    font-size: $size_body;
    padding: 8px;
    selection-background-color: $lavender_soft;
}
QLabel#memoCount, QLabel#memoState, QLabel#memoGuide {
    color: $muted_ink;
    font-size: 9pt;
}
QLabel#memoState[dirty="true"] {
    color: #a66028;
    font-weight: 700;
}
QLabel#memoCount[level="warning"], QLabel#memoGuide[warning="true"] {
    color: #a66028;
}
QLabel#memoCount[level="limit"] {
    color: #b33b47;
    font-weight: 700;
}
QToolButton#memoSaveButton, QToolButton#memoRevertButton {
    min-height: 27px;
    padding: 2px 11px;
}
QToolButton#memoSaveButton:enabled {
    background: $lavender_soft;
    border-color: $lavender_border;
    color: $lavender_dark;
}
QLabel#cardHeading {
    background: $lavender_soft;
    border-left: 4px solid $lavender;
    border-radius: 5px;
    color: $lavender_dark;
    font-family: $font_display;
    font-size: $size_card_heading;
    font-weight: $weight_bold;
    padding: 4px 7px;
}
QWidget#infoField {
    border-bottom: 1px solid #eadfd2;
}
QLabel#fieldName {
    color: #58505f;
    font-size: 9pt;
    font-weight: $weight_medium;
}
QLabel#fieldValue {
    color: $ink;
    font-size: 9.25pt;
}
QLabel#fieldValue[technical="true"] {
    font-family: $font_technical;
    font-size: $size_technical;
}
QPlainTextEdit#technicalText {
    font-family: $font_technical;
    font-size: $size_technical;
}
QLabel#emptyInfo {
    color: #89818d;
}
QPushButton, QToolButton {
    background: $control;
    border: 1px solid #cfc4d8;
    border-radius: 6px;
    padding: 5px 10px;
}
QPushButton:hover, QToolButton:hover {
    background: $control_hover;
    border-color: $lavender;
}
QToolButton#cardCopyButton {
    background: #f7f7f7;
    border: 1px solid #777b82;
    padding: 3px;
}
QToolButton#cardCopyButton:hover {
    background: #e1e3e6;
    border-color: #44484e;
}
QToolButton#cardCopyButton:pressed {
    background: #c8cbd0;
    border-color: #25282d;
}
QToolButton#mediaButton {
    min-width: 28px;
    min-height: 28px;
    padding: 3px;
}
QToolButton#mediaButton:checked {
    background: $lavender_soft;
    border-color: $lavender;
}
QSlider::groove:horizontal {
    background: $divider;
    border-radius: 2px;
    height: 4px;
}
QSlider::sub-page:horizontal {
    background: $lavender;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: $control;
    border: 1px solid $lavender;
    border-radius: 6px;
    height: 12px;
    margin: -5px 0;
    width: 12px;
}
QSplitter#detailSplitter::handle {
    background: $divider;
    margin: 2px 1px;
    width: 4px;
}
QSplitter#detailSplitter::handle:hover {
    background: $lavender_border;
}
QScrollBar:vertical {
    background: transparent;
    margin: 2px;
    width: 11px;
}
QScrollBar::handle:vertical {
    background: #c9bfd2;
    border-radius: 4px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover {
    background: $lavender_border;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QStatusBar {
    background: $paper;
    border-top: 1px solid $lavender_border;
    color: $muted_ink;
}

QLabel#statusIcon {
    background: transparent;
    padding-left: 5px;
    padding-right: 2px;
}
"""
).substitute({**THEME, **TYPOGRAPHY})

# レイアウト・可読性の基礎と、交換可能な表面スキンを最後に合成する。
APP_STYLE = BASE_STYLE + build_skin_style()


WINDOW_SIZE = (1400, 850)
PAGE_SPLITTER_SIZES = [760, 640]
# 起動時はプレビュー55%、編集領域45%。実寸の下限はMainWindow側で保証する。
DETAIL_SPLITTER_SIZES = [55, 45]
CARD_WIDTH = 210
CARD_THUMBNAIL_SIZE = (184, 104)
CARD_HEIGHT = 210
LANDSCAPE_CARD_HEIGHT = 124
LANDSCAPE_THUMBNAIL_SIZE = (150, 100)
