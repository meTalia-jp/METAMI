"""キャラクターヘッダーと小さな状態・カード装飾を描画する。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


CHARACTER_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "characters"


class CharacterHeader(QWidget):
    """画像がなくても成立する、左右領域共通の担当キャラクターヘッダー。"""

    def __init__(
        self,
        name: str,
        role: str,
        asset_name: str,
        side: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("characterHeader")
        self.setProperty("side", side)

        avatar = QLabel()
        avatar.setObjectName("characterAvatar")
        avatar.setFixedSize(72, 72)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        asset_path = CHARACTER_ASSET_DIR / asset_name
        pixmap = QPixmap(str(asset_path)) if asset_path.is_file() else QPixmap()
        if pixmap.isNull():
            avatar.setText(name[:1])
            avatar.setToolTip(f"{name}（画像素材なし）")
        else:
            avatar.setPixmap(
                pixmap.scaled(
                    avatar.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            avatar.setToolTip(name)

        name_label = QLabel(name)
        name_label.setObjectName("characterName")
        role_label = QLabel(role)
        role_label.setObjectName("characterRole")
        role_label.setWordWrap(True)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        text_layout.addWidget(name_label)
        text_layout.addWidget(role_label)
        text_layout.addStretch(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 12, 6)
        layout.setSpacing(10)
        layout.addWidget(avatar)
        layout.addLayout(text_layout, 1)
        self._accessory_layout = QHBoxLayout()
        self._accessory_layout.setContentsMargins(0, 0, 0, 0)
        self._accessory_layout.setSpacing(4)
        layout.addLayout(self._accessory_layout)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def add_accessory(self, widget: QWidget) -> None:
        """ヘッダー右端へ、評価など常時表示する操作部品を追加する。"""
        self._accessory_layout.addWidget(widget)


def heading_pixmap(kind: str) -> QPixmap:
    """ペイン見出し用のノート・付箋・クリップ風アイコンを返す。"""
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#6746a4"), 1.5)
    painter.setPen(pen)

    if kind == "notebook":
        painter.setBrush(QColor("#f5efff"))
        painter.drawRoundedRect(QRectF(4, 3, 13, 15), 2, 2)
        for y in (7, 11, 15):
            painter.drawLine(QPointF(8, y), QPointF(14, y))
        painter.drawLine(QPointF(5, 5), QPointF(5, 16))
    elif kind == "clip":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(QPointF(13.5, 5))
        path.cubicTo(17, 8, 13, 17, 9, 17)
        path.cubicTo(4, 17, 4, 12, 7, 7)
        path.cubicTo(9, 3, 14, 4, 13, 8)
        path.cubicTo(12, 11, 9, 14, 8, 12)
        painter.drawPath(path)
    else:
        painter.setBrush(QColor("#fff0c9"))
        painter.drawRoundedRect(QRectF(3, 3, 14, 14), 2, 2)
        fold = QPainterPath(QPointF(12, 17))
        fold.lineTo(17, 12)
        fold.lineTo(17, 17)
        fold.closeSubpath()
        painter.setBrush(QColor("#f4d990"))
        painter.drawPath(fold)
    painter.end()
    return pixmap


def status_pixmap(state: str = "ready") -> QPixmap:
    """ステータスバー用の小さな状態ドットを返す。"""
    colors = {
        "ready": QColor("#78ad8d"),
        "active": QColor("#8763cf"),
        "error": QColor("#c76579"),
    }
    pixmap = QPixmap(12, 12)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#ffffff"), 1))
    painter.setBrush(colors.get(state, colors["ready"]))
    painter.drawEllipse(QRectF(2, 2, 8, 8))
    painter.end()
    return pixmap


def memo_pixmap() -> QPixmap:
    """カード上で保存済みメモを示す、小さな紙と鉛筆のアイコン。"""
    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#9a7943"), 1))
    painter.setBrush(QColor("#fff4cf"))
    painter.drawRoundedRect(QRectF(2.5, 2.5, 11, 13), 1.5, 1.5)
    painter.drawLine(QPointF(5, 6), QPointF(11, 6))
    painter.drawLine(QPointF(5, 9), QPointF(10, 9))
    painter.setPen(
        QPen(
            QColor("#7654a8"),
            2,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
        )
    )
    painter.drawLine(QPointF(9, 14), QPointF(15, 8))
    painter.end()
    return pixmap


def tab_pixmap(color: str) -> QPixmap:
    """右端付箋タブの識別に使う、小さな色紙アイコン。"""
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    fill = QColor(color)
    border = fill.darker(118)
    painter.setPen(QPen(border, 1))
    painter.setBrush(fill)
    painter.drawRoundedRect(QRectF(2, 2, 10, 10), 2, 2)
    painter.end()
    return pixmap
