"""機能ロジックから分離した、スキン表面の軽量な描画部品。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.decorations import CharacterHeader


class StickyNoteFrame(QFrame):
    """紙の繊維と控えめな落ち影で付箋の立体感を描く。"""

    _PAPER_COLORS = {
        "pink": ("#fff9fb", "#f5dce4", "#d8aebc"),
        "mint": ("#fbfff8", "#dceccf", "#aec99c"),
        "yellow": ("#fffef4", "#f4e6b7", "#d4bd73"),
        "blue": ("#fbfdff", "#dce9f6", "#a8c0db"),
        "lavender": ("#fefbff", "#e4d8f1", "#bba5d3"),
    }

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        tone = str(self.property("noteTone") or "yellow")
        top_color, bottom_color, border_color = self._PAPER_COLORS.get(
            tone, self._PAPER_COLORS["yellow"]
        )
        note_rect = QRectF(self.rect()).adjusted(0.8, 8.0, -4.0, -4.0)
        paper = QPainterPath()
        paper.addRoundedRect(note_rect, 3, 3)

        # めくれは作らず、右下へ重なる薄い多段影だけで紙を浮かせる。
        painter.setPen(Qt.PenStyle.NoPen)
        for offset_x, offset_y, alpha in (
            (2.8, 3.0, 18),
            (1.8, 2.0, 26),
            (0.9, 1.1, 34),
        ):
            painter.setBrush(QColor(38, 42, 48, alpha))
            painter.drawRoundedRect(
                note_rect.translated(offset_x, offset_y), 3.5, 3.5
            )

        gradient = QLinearGradient(note_rect.topLeft(), note_rect.bottomRight())
        gradient.setColorAt(0.0, QColor(top_color))
        gradient.setColorAt(0.76, QColor(top_color))
        gradient.setColorAt(1.0, QColor(bottom_color))
        painter.setBrush(gradient)
        selected = bool(self.property("selected"))
        painter.setPen(
            QPen(QColor("#7652ce") if selected else QColor(border_color),
                 2.6 if selected else 1.0)
        )
        painter.drawPath(paper)

        # 紙の上端だけに弱い反射を置き、厚みを感じられるようにする。
        painter.setPen(QPen(QColor(255, 255, 255, 145), 0.8))
        painter.drawLine(
            QPointF(note_rect.left() + 5, note_rect.top() + 1),
            QPointF(note_rect.right() - 5, note_rect.top() + 1),
        )

        # ごく薄い紙繊維。文字の可読性を下げない濃度に固定する。
        painter.setClipPath(paper)
        painter.setPen(QPen(QColor(116, 98, 72, 13), 0.7))
        seed = sum(str(getattr(self, "path", "")).encode("utf-8"))
        for index in range(5):
            y = 31 + ((seed + index * 37) % max(32, self.height() - 50))
            x = 9 + ((seed + index * 19) % 34)
            length = 32 + ((seed + index * 13) % 58)
            painter.drawLine(x, y, min(self.width() - 18, x + length), y + 1)
        painter.setClipping(False)
        painter.end()


class MagnetPin(QWidget):
    """付箋の上端から突き出して見える立体的な丸型マグネット。"""

    _COLORS = {
        "pink": ("#ff8fb3", "#b83f69"),
        "mint": ("#83d587", "#367c43"),
        "yellow": ("#ffd45c", "#b17b10"),
        "blue": ("#75baff", "#2e6fae"),
        "lavender": ("#ad8bea", "#6641a9"),
    }

    def __init__(self, tone: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tone = tone
        self.setObjectName("cardPin")
        self.setFixedSize(30, 25)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setToolTip("マグネットで留めた付箋")
        self.setAccessibleName("付箋マグネット")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, 11.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(25, 27, 31, 70))
        painter.drawEllipse(center + QPointF(1.8, 3.0), 10.2, 8.0)
        light, dark = self._COLORS.get(
            self.tone, self._COLORS["lavender"]
        )
        gradient = QRadialGradient(center - QPointF(3.0, 3.0), 13.0)
        gradient.setColorAt(0.0, QColor("#ffffff"))
        gradient.setColorAt(0.18, QColor(light))
        gradient.setColorAt(1.0, QColor(dark))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(dark).darker(135), 1.1))
        painter.drawEllipse(center, 10.2, 9.2)
        painter.setBrush(QColor(255, 255, 255, 145))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center - QPointF(3.4, 3.2), 2.7, 1.9)
        painter.end()


class AnalysisStatusPanel(QWidget):
    """実際のSQLite利用状態だけを表示する状態パネル。"""

    def __init__(
        self, state: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("analysisStatusPanel")
        self.setFixedSize(116, 58)
        self.setAccessibleName("データベース状態")
        rows = QVBoxLayout(self)
        rows.setContentsMargins(7, 5, 7, 5)
        rows.setSpacing(1)
        title = QLabel("DATABASE")
        title.setObjectName("databaseStateTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.database_label = QLabel()
        self.database_label.setObjectName("consoleStatus")
        self.database_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rows.addWidget(title)
        rows.addWidget(self.database_label)
        self.set_state(state)

    def set_state(self, state: str) -> None:
        normalized = state if state in {"online", "error", "offline"} else "offline"
        labels = {
            "online": "DB ● Online",
            "error": "DB ● Error",
            "offline": "DB ○ Offline",
        }
        tips = {
            "online": "SQLiteデータベースを正常に利用できます。",
            "error": "SQLiteデータベース処理でエラーが発生しました。",
            "offline": "SQLiteデータベースを利用できません。",
        }
        self.database_label.setText(labels[normalized])
        self.database_label.setProperty("state", normalized)
        self.database_label.setToolTip(tips[normalized])
        self.setToolTip(tips[normalized])
        self.database_label.style().unpolish(self.database_label)
        self.database_label.style().polish(self.database_label)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(QColor("#171d26"))
        painter.setPen(QPen(QColor("#657182"), 1))
        painter.drawRoundedRect(area, 4, 4)
        painter.end()


class RatingAppealPanel(QWidget):
    """評価機能を見つけやすくする案内と既存コントロールの容器。"""

    def __init__(
        self, rating_control: QWidget, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ratingAppealPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.prompt_label = QLabel("星をクリックして評価")
        self.prompt_label.setObjectName("ratingAppealText")
        self.prompt_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.prompt_label.setStyleSheet(
            "color: #f1f3f7; background: transparent; font-size: 8pt;"
        )
        for label in rating_control.findChildren(QLabel):
            label.setStyleSheet(
                "color: #d7dbe3; background: transparent; font-size: 8pt;"
            )
        for button in rating_control.findChildren(QWidget):
            if button.objectName() == "ratingButton":
                button.setStyleSheet(
                    "QToolButton { color: #ffe34f; background: transparent; "
                    "border: none; font-size: 16pt; min-width: 90px; } "
                    "QToolButton:hover { background: #394354; color: #fff083; } "
                    "QToolButton:disabled { color: #8d929c; }"
                )
        self.nudge_label = QLabel()
        self.nudge_label.setObjectName("ratingNudgeText")
        self.nudge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nudge_label.setStyleSheet(
            "color: #ffe9a6; background: transparent; font-size: 7.5pt;"
        )
        self.nudge_label.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(0)
        layout.addWidget(self.prompt_label)
        layout.addWidget(rating_control, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.nudge_label)

    def show_nudge(self, message: str) -> None:
        self.nudge_label.setText(message)
        self.nudge_label.setToolTip(message)
        self.nudge_label.show()
        self.updateGeometry()

    def clear_nudge(self) -> None:
        self.nudge_label.clear()
        self.nudge_label.hide()
        self.updateGeometry()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(QColor("#161c25"))
        painter.setPen(QPen(QColor("#677384"), 1))
        painter.drawRoundedRect(area, 4, 4)
        painter.end()


class AnalysisConsolePanel(QWidget):
    """解析装置の外装に継ぎ目と固定ボルトを追加する。"""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(12, 15, 20, 145), 1))
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(6.5, 6.5, -6.5, -6.5), 7, 7
        )
        for point in (
            QPointF(9, 9),
            QPointF(self.width() - 9, 9),
            QPointF(9, self.height() - 9),
            QPointF(self.width() - 9, self.height() - 9),
        ):
            painter.setBrush(QColor("#aeb5be"))
            painter.setPen(QPen(QColor("#252a31"), 1))
            painter.drawEllipse(point, 3.2, 3.2)
            painter.drawLine(
                QPointF(point.x() - 1.6, point.y()),
                QPointF(point.x() + 1.6, point.y()),
            )
        painter.end()


class AnalysisCharacterHeader(CharacterHeader):
    """解析装置側専用の、暗色コンソールヘッダー。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setObjectName("analysisCharacterHeader")
        for label in self.findChildren(QLabel):
            if label.objectName() == "characterName":
                label.setStyleSheet("color: #f3f1ff; background: transparent;")
            elif label.objectName() == "characterRole":
                label.setStyleSheet("color: #bfc5d0; background: transparent;")

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        gradient = QLinearGradient(area.topLeft(), area.topRight())
        gradient.setColorAt(0.0, QColor("#3e4552"))
        gradient.setColorAt(1.0, QColor("#242934"))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor("#141820"), 1))
        painter.drawRoundedRect(area, 6, 6)
        painter.setPen(QPen(QColor("#8c78db"), 4))
        painter.drawLine(2, 6, 2, self.height() - 6)
        painter.end()
