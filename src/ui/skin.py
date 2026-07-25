"""交換可能な表面スキン定義とQSS生成を担当する。"""

from __future__ import annotations

from dataclasses import dataclass
from string import Template


WHITEBOARD_SURFACE_COLOR = "#f8faf9"


@dataclass(frozen=True)
class SkinDefinition:
    """レイアウトへ影響させない、表面材質と配色の定義。"""

    key: str
    display_name: str
    colors: dict[str, str]


WHITEBOARD_MECHA_SKIN = SkinDefinition(
    key="whiteboard_mecha",
    display_name="ホワイトボード・解析装置",
    colors={
        "workspace": "#c9cbd1",
        "board_frame_light": "#eef1f3",
        "board_frame_mid": "#8f969c",
        "board_frame_dark": "#5e656b",
        # 付箋の捲れ下に見えるホワイトボード色（StickyNoteFrame）と統一。
        "board_surface_top": WHITEBOARD_SURFACE_COLOR,
        "board_surface_bottom": WHITEBOARD_SURFACE_COLOR,
        "console_top": "#454b57",
        "console_bottom": "#20252e",
        "console_edge_light": "#7e8794",
        "console_edge_dark": "#11151c",
        "console_accent": "#8c78db",
        "console_screen": "#11151b",
        "console_panel": "#e5e7eb",
        "selected": "#7652ce",
    },
)

SKINS = {WHITEBOARD_MECHA_SKIN.key: WHITEBOARD_MECHA_SKIN}
DEFAULT_SKIN_KEY = WHITEBOARD_MECHA_SKIN.key


_SURFACE_QSS = Template(
    """
/* 表面材質だけを定義する。寸法と機能レイアウトはstyles.py側に置く。 */
QWidget#applicationWorkspace {
    background: $workspace;
}
QWidget#taliaWorkspace {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $board_frame_light, stop:0.05 #c5cbd0,
        stop:0.95 #a2a8ad, stop:1 $board_frame_dark);
    border: 2px solid $board_frame_dark;
    border-top-color: $board_frame_light;
    border-left-color: $board_frame_light;
    border-radius: 12px;
}
QWidget#taliaWorkspace QWidget#characterHeader {
    background: rgba(255, 255, 255, 235);
    border: 1px solid #aeb5ba;
    border-left: 5px solid #8c72d1;
    border-radius: 8px;
}
QWidget#taliaWorkspace QFrame#searchPanel {
    background: rgba(248, 250, 250, 245);
    border: 1px solid #a9b0b5;
    border-top-color: #ffffff;
    border-radius: 7px;
}
QWidget#taliaWorkspace QListWidget#cardList {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $board_surface_top, stop:0.52 #f8faf9,
        stop:1 $board_surface_bottom);
    border: 7px solid $board_frame_mid;
    border-top-color: $board_frame_light;
    border-left-color: #dce1e4;
    border-right-color: $board_frame_dark;
    border-bottom-color: #555c61;
    border-radius: 10px;
    padding: 12px;
}
QWidget#taliaWorkspace QListWidget#cardList::item {
    background: transparent;
    border: none;
    padding: 0;
}

QFrame#fileCard,
QFrame#fileCard[selected="true"] {
    background: transparent;
    border: none;
}
QWidget#belowCardInfo,
QWidget#cardSummary,
QLabel#cardMemoIndicator,
QLabel#cardFileName,
QLabel#cardDetails {
    background: transparent;
    border: none;
}
MagnetPin#cardPin {
    background: transparent;
    border: none;
}

QWidget#analysisConsole {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 $console_top, stop:0.45 #343a46, stop:1 $console_bottom);
    border: 3px solid $console_edge_dark;
    border-top-color: $console_edge_light;
    border-left-color: #68717e;
    border-radius: 10px;
}
CharacterHeader#analysisCharacterHeader {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #343a46, stop:1 #242934);
    border: 1px solid #79828e;
    border-left: 5px solid $console_accent;
    border-bottom-color: #141820;
    border-radius: 6px;
}
CharacterHeader#analysisCharacterHeader QLabel#characterName { color: #f3f1ff; }
CharacterHeader#analysisCharacterHeader QLabel#characterRole { color: #bfc5d0; }
QLabel#consoleStatus {
    background: transparent;
    border: none;
    color: #71e59a;
    font-family: "Cascadia Mono", "Consolas", "BIZ UDGothic";
    font-size: 8pt;
    font-weight: 700;
    padding: 1px 2px;
}
QLabel#consoleStatus[state="error"] {
    color: #ff8e9a;
}
QLabel#consoleStatus[state="offline"] { color: #c4c9d1; }
QLabel#databaseStateTitle {
    background: transparent;
    border: none;
    color: #aeb7c4;
    font-family: "Cascadia Mono", "Consolas";
    font-size: 6.5pt;
    letter-spacing: 1px;
}
QWidget#analysisStatusPanel {
    background: transparent;
    border: none;
}
QWidget#analysisConsole QWidget#previewDeviceHeader {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #8e97a4, stop:0.45 #535c69, stop:1 #282e38);
    border: 1px solid #10141a;
    border-bottom: none;
    border-radius: 7px 7px 0 0;
    min-height: 23px;
    max-height: 23px;
}
QLabel#previewDeviceTitle {
    color: #d7d0f3;
    background: transparent;
    border: none;
    font-family: "Cascadia Mono", "Consolas", "BIZ UDGothic";
    font-size: 7.5pt;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#previewDeviceMode {
    color: #8ce7dc;
    background: transparent;
    border: none;
    font-family: "Cascadia Mono", "Consolas";
    font-size: 6.5pt;
}
QWidget#analysisConsole QStackedWidget#previewSurface {
    background: $console_screen;
    border: 8px solid #5f6874;
    border-top-color: #858e9a;
    border-right-color: #2a3039;
    border-bottom-color: #171b22;
    border-radius: 9px;
}
QWidget#analysisConsole QWidget#previewDeviceFooter {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3d4551, stop:1 #1a1f27);
    border: 1px solid #10141a;
    border-top: none;
    border-radius: 0 0 7px 7px;
    min-height: 22px;
    max-height: 22px;
}
QLabel#previewDeviceData,
QLabel#previewDeviceSignal {
    color: #d7dce5;
    background: transparent;
    border: none;
    font-family: "Cascadia Mono", "Consolas", "BIZ UDGothic";
    font-size: 6.5pt;
}
QLabel#previewDeviceSignal { color: #9bea72; font-weight: 700; }
QWidget#analysisConsole QSplitter#detailSplitter::handle {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #232934, stop:0.42 #596270, stop:0.58 #11161d,
        stop:1 #343b46);
    border-top: 1px solid #87919e;
    border-bottom: 1px solid #11161d;
    margin: 3px 10px;
}
QWidget#analysisConsole QWidget#mediaControls {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #454c58, stop:1 #242a33);
    border: 1px solid #77808c;
    border-bottom-color: #151a21;
    color: #f2f4f7;
}
QWidget#analysisConsole QTabWidget#metadataTabs::pane {
    background: $console_panel;
    border: 2px solid #7d8590;
    border-bottom-color: #303640;
    border-radius: 0 6px 6px 6px;
}
QWidget#analysisConsole QTabWidget#metadataTabs QTabBar::tab {
    background: #5a626e;
    border-color: #252a32;
    color: #e2e6ec;
}
QWidget#analysisConsole QTabWidget#metadataTabs QTabBar::tab:selected {
    background: #ded9f3;
    border-color: $console_accent;
    color: #41366f;
}
QWidget#analysisConsole QFrame#infoCard,
QWidget#analysisConsole QFrame#titleCard,
QWidget#analysisConsole QFrame#tagCard {
    background: #f8f9fb;
    border-color: #8f96a0;
}
QSplitter#pageSplitter::handle {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #4e555f, stop:0.5 #a7adb5, stop:1 #373d47);
    border: 1px solid #272c34;
    border-radius: 2px;
    margin: 7px 1px;
}
QSplitter#pageSplitter::handle:hover {
    background: $console_accent;
}
"""
)


def build_skin_style(key: str = DEFAULT_SKIN_KEY) -> str:
    """指定スキンの表面QSSを生成する。未知のキーは既定へ戻す。"""
    skin = SKINS.get(key, WHITEBOARD_MECHA_SKIN)
    return _SURFACE_QSS.substitute(skin.colors)
