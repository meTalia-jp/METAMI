"""画像・動画プレビューを担当する中央ペイン。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from metadata.mp4_reader import Mp4ReadError, read_mp4_info
from metadata.image_reader import ImageReadError, read_oriented_image


class TelevisionPlaceholder(QWidget):
    """未選択時に、テレビへ映っためたみを描くダミープレビュー。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        asset = (
            Path(__file__).resolve().parents[1]
            / "assets"
            / "characters"
            / "metami.png"
        )
        self._character = QPixmap(str(asset))
        self.setObjectName("televisionPlaceholder")
        self.setAccessibleName("プレビュー待機画面")

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(18, 22, -18, -20)
        tv_width = min(area.width() * 0.76, 560.0)
        tv_height = min(area.height() * 0.76, tv_width * 0.62)
        tv_width = min(tv_width, tv_height / 0.62)
        body = QRectF(
            area.center().x() - tv_width / 2,
            area.center().y() - tv_height / 2 + 8,
            tv_width,
            tv_height,
        )

        painter.setPen(QPen(QColor("#58453b"), 4, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        antenna_top = body.top() - min(34.0, tv_height * 0.16)
        painter.drawLine(
            QPointF(body.center().x() - 9, body.top() + 2),
            QPointF(body.center().x() - 38, antenna_top),
        )
        painter.drawLine(
            QPointF(body.center().x() + 9, body.top() + 2),
            QPointF(body.center().x() + 38, antenna_top),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(57, 39, 31, 55))
        painter.drawRoundedRect(body.translated(4, 7), 22, 22)
        painter.setBrush(QColor("#765447"))
        painter.setPen(QPen(QColor("#432d27"), 2))
        painter.drawRoundedRect(body, 22, 22)

        control_width = max(48.0, tv_width * 0.14)
        screen = body.adjusted(16, 16, -(control_width + 13), -22)
        painter.setBrush(QColor("#303238"))
        painter.setPen(QPen(QColor("#33231e"), 2))
        painter.drawRoundedRect(screen.adjusted(-5, -5, 5, 5), 16, 16)
        painter.setBrush(QColor("#eeeaf5"))
        painter.setPen(QPen(QColor("#a99abd"), 1.2))
        painter.drawRoundedRect(screen, 12, 12)

        if not self._character.isNull():
            character = self._character.scaled(
                max(1, int(screen.width() - 10)),
                max(1, int(screen.height() - 8)),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            target = QPointF(
                screen.center().x() - character.width() / 2,
                screen.center().y() - character.height() / 2,
            )
            painter.drawPixmap(target, character)

        knob_x = body.right() - control_width / 2 - 6
        for offset in (0.38, 0.61):
            center = QPointF(knob_x, body.top() + tv_height * offset)
            painter.setBrush(QColor("#d1a36f"))
            painter.setPen(QPen(QColor("#402d27"), 2))
            painter.drawEllipse(center, 10, 10)
            painter.drawLine(center, center + QPointF(5, -4))
        painter.setPen(QPen(QColor("#3f2c26"), 5,
                            Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        painter.drawLine(
            QPointF(body.left() + tv_width * 0.20, body.bottom() - 2),
            QPointF(body.left() + tv_width * 0.16, body.bottom() + 12),
        )
        painter.drawLine(
            QPointF(body.right() - tv_width * 0.20, body.bottom() - 2),
            QPointF(body.right() - tv_width * 0.16, body.bottom() + 12),
        )
        painter.end()


class PreviewPane(QWidget):
    """ファイル形式に応じて画像または動画を表示する。"""

    mediaError = Signal(str)
    locateMissingRequested = Signal()
    deleteMissingRequested = Signal()
    ratingEngagementReached = Signal()
    playbackIdle = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("previewPane")
        self._image_pixmap = QPixmap()
        self._poster_pixmap = QPixmap()
        self._poster_pending = False
        self._engagement_emitted = False
        self.stack = QStackedWidget()
        self.stack.setObjectName("previewSurface")

        self.device_header = QWidget()
        self.device_header.setObjectName("previewDeviceHeader")
        device_header_layout = QHBoxLayout(self.device_header)
        device_header_layout.setContentsMargins(12, 0, 12, 0)
        device_header_layout.setSpacing(8)
        self.device_title = QLabel("◆ METAMI ANALYSIS UNIT ◆")
        self.device_title.setObjectName("previewDeviceTitle")
        self.device_mode = QLabel("VISUAL / METADATA MONITOR")
        self.device_mode.setObjectName("previewDeviceMode")
        device_header_layout.addStretch(1)
        device_header_layout.addWidget(self.device_title)
        device_header_layout.addStretch(1)
        device_header_layout.addWidget(self.device_mode)

        self.device_footer = QWidget()
        self.device_footer.setObjectName("previewDeviceFooter")
        footer_layout = QHBoxLayout(self.device_footer)
        footer_layout.setContentsMargins(12, 0, 12, 0)
        footer_layout.setSpacing(16)
        self.device_source = QLabel("SOURCE  STANDBY")
        self.device_source.setObjectName("previewDeviceData")
        self.device_resolution = QLabel("RES  —")
        self.device_resolution.setObjectName("previewDeviceData")
        self.device_time = QLabel("TIME  0:00")
        self.device_time.setObjectName("previewDeviceData")
        self.device_signal = QLabel("● SYSTEM READY")
        self.device_signal.setObjectName("previewDeviceSignal")
        footer_layout.addWidget(self.device_source)
        footer_layout.addWidget(self.device_resolution)
        footer_layout.addWidget(self.device_time)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.device_signal)
        self.placeholder = TelevisionPlaceholder()
        self.stack.addWidget(self.placeholder)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(1, 1)
        self.image_scroll = QScrollArea()
        self.image_scroll.setWidgetResizable(True)
        self.image_scroll.setWidget(self.image_label)
        self.stack.addWidget(self.image_scroll)

        self.video_widget = QVideoWidget()
        self.video_poster = QLabel("動画のサムネイルを読み込んでいます…")
        self.video_poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_display = QStackedWidget()
        self.video_display.addWidget(self.video_poster)
        self.video_display.addWidget(self.video_widget)
        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.7)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        self.video_widget.videoSink().videoFrameChanged.connect(self._capture_poster)
        self.play_button = QToolButton()
        self.play_button.setObjectName("mediaButton")
        self.play_button.setIcon(_play_icon())
        self.play_button.setIconSize(QSize(20, 20))
        self.play_button.setToolTip("再生")
        self.play_button.setAccessibleName("再生")
        self.play_button.clicked.connect(self._toggle_playback)
        self.mute_button = QToolButton()
        self.mute_button.setObjectName("mediaButton")
        self.mute_button.setIcon(_speaker_icon(False))
        self.mute_button.setIconSize(QSize(20, 20))
        self.mute_button.setToolTip("ミュート")
        self.mute_button.setAccessibleName("ミュート")
        self.mute_button.setCheckable(True)
        self.mute_button.toggled.connect(self._toggle_mute)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("volumeSlider")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.setToolTip("音量: 70%")
        self.volume_slider.setAccessibleName("音量")
        self.volume_slider.valueChanged.connect(self._set_volume)
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self.media_player.setPosition)
        self.time_label = QLabel("0:00 / 0:00")
        controls = QHBoxLayout()
        controls.setContentsMargins(8, 5, 8, 5)
        controls.addWidget(self.play_button)
        controls.addWidget(self.mute_button)
        controls.addWidget(self.volume_slider)
        controls.addWidget(self.position_slider, 1)
        controls.addWidget(self.time_label)
        controls_bar = QWidget()
        controls_bar.setObjectName("mediaControls")
        controls_bar.setLayout(controls)
        video_page = QWidget()
        video_layout = QVBoxLayout(video_page)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.addWidget(self.video_display, 1)
        video_layout.addWidget(controls_bar)
        self.stack.addWidget(video_page)

        self.missing_page = QWidget()
        self.missing_page.setObjectName("missingPreview")
        missing_layout = QVBoxLayout(self.missing_page)
        missing_layout.setContentsMargins(24, 22, 24, 22)
        missing_layout.setSpacing(12)
        self.missing_title = QLabel("ファイルが見つかりません")
        self.missing_title.setObjectName("missingPreviewTitle")
        self.missing_message = QLabel(
            "原本は操作せず、METAMIに保存された整理情報を表示しています。"
        )
        self.missing_message.setWordWrap(True)
        missing_layout.addWidget(self.missing_title)
        missing_layout.addWidget(self.missing_message)
        details = QGridLayout()
        details.setHorizontalSpacing(14)
        details.setVerticalSpacing(8)
        self.missing_values: dict[str, QLabel] = {}
        for row, (key, label) in enumerate(
            (
                ("filename", "以前のファイル名"),
                ("path", "以前のパス"),
                ("checked", "最終確認日時"),
                ("rating", "保存済み評価"),
                ("tags", "保存済みタグ"),
                ("memo", "保存済みメモ"),
            )
        ):
            heading = QLabel(label)
            heading.setObjectName("missingInfoHeading")
            value = QLabel("—")
            value.setObjectName("missingInfoValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.missing_values[key] = value
            details.addWidget(heading, row, 0, Qt.AlignmentFlag.AlignTop)
            details.addWidget(value, row, 1)
        details.setColumnStretch(1, 1)
        missing_layout.addLayout(details)
        missing_layout.addStretch(1)
        actions = QHBoxLayout()
        self.locate_missing_button = QPushButton("登録パスを変更")
        self.locate_missing_button.setToolTip(
            "利用者が選んだファイルへ、このDB記録の登録パスを変更します"
        )
        self.locate_missing_button.setObjectName("missingAction")
        self.locate_missing_button.clicked.connect(self.locateMissingRequested.emit)
        self.delete_missing_button = QPushButton("METAMIの記録を削除")
        self.delete_missing_button.setObjectName("deleteRecordButton")
        self.delete_missing_button.clicked.connect(self.deleteMissingRequested.emit)
        actions.addWidget(self.locate_missing_button)
        actions.addStretch(1)
        actions.addWidget(self.delete_missing_button)
        missing_layout.addLayout(actions)
        self.stack.addWidget(self.missing_page)

        self.media_player.positionChanged.connect(self._update_position)
        self.media_player.durationChanged.connect(self._update_duration)
        self.media_player.playbackStateChanged.connect(self._update_play_button)
        self.media_player.mediaStatusChanged.connect(self._on_media_status)
        self.media_player.errorOccurred.connect(self._on_media_error)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(0)
        layout.addWidget(self.device_header)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.device_footer)

    def show_file(self, path: Path) -> None:
        self.media_player.stop()
        self._engagement_emitted = False
        suffix = path.suffix.upper().lstrip(".") or "FILE"
        self.device_source.setText(f"SOURCE  {suffix}")
        self.device_signal.setText("● SIGNAL ONLINE")
        if path.suffix.lower() == ".mp4":
            self.device_mode.setText("MOTION / METADATA MONITOR")
            try:
                info = read_mp4_info(path)
                if info.width and info.height:
                    self.device_resolution.setText(
                        f"RES  {info.width}×{info.height}"
                    )
                else:
                    self.device_resolution.setText("RES  VIDEO STREAM")
                duration = int((info.duration_seconds or 0) * 1000)
                self.device_time.setText(
                    f"TIME  0:00 / {_format_clock(duration)}"
                )
            except Mp4ReadError:
                self.device_resolution.setText("RES  VIDEO STREAM")
                self.device_time.setText("TIME  0:00")
            self._image_pixmap = QPixmap()
            self._poster_pixmap = QPixmap()
            self._poster_pending = True
            self.video_poster.clear()
            self.video_poster.setText("動画のサムネイルを読み込んでいます…")
            self.video_display.setCurrentIndex(0)
            self.stack.setCurrentIndex(2)
            self.position_slider.setValue(0)
            self.audio_output.setMuted(True)
            self.media_player.setSource(QUrl.fromLocalFile(str(path)))
            self.media_player.play()
            return
        try:
            image = read_oriented_image(path)
        except ImageReadError:
            self.clear("画像を表示できませんでした。")
            return
        pixmap = QPixmap.fromImage(image)
        self._image_pixmap = pixmap
        self.device_mode.setText("STILL / METADATA MONITOR")
        self.device_resolution.setText(
            f"RES  {pixmap.width()}×{pixmap.height()}"
        )
        self.device_time.setText("MODE  STILL IMAGE")
        self.stack.setCurrentIndex(1)
        self._fit_image()

    def clear(self, message: str = "ファイルを選択してください。") -> None:
        self.media_player.stop()
        self.media_player.setSource(QUrl())
        self._image_pixmap = QPixmap()
        self._poster_pixmap = QPixmap()
        self._poster_pending = False
        self._engagement_emitted = False
        self.audio_output.setMuted(self.mute_button.isChecked())
        self.placeholder.setToolTip(message)
        self.placeholder.setAccessibleName(message)
        self.device_source.setText("SOURCE  STANDBY")
        self.device_resolution.setText("RES  —")
        self.device_time.setText("TIME  0:00")
        self.device_signal.setText("● SYSTEM READY")
        self.device_mode.setText("VISUAL / METADATA MONITOR")
        self.stack.setCurrentIndex(0)

    def show_missing(
        self,
        *,
        filename: str,
        path: str,
        last_checked_at: str,
        rating: int,
        tags: tuple[str, ...] | list[str],
        has_memo: bool,
    ) -> None:
        """missing記録を、通常プレビューの代わりに安全に表示する。"""
        self.media_player.stop()
        self.media_player.setSource(QUrl())
        self._engagement_emitted = False
        self.missing_values["filename"].setText(filename)
        self.missing_values["path"].setText(path)
        self.missing_values["checked"].setText(last_checked_at or "未確認")
        self.missing_values["rating"].setText(
            "★" * rating + "☆" * (3 - rating) if rating else "未評価（☆☆☆）"
        )
        self.missing_values["tags"].setText("、".join(tags) if tags else "なし")
        self.missing_values["memo"].setText("あり" if has_memo else "なし")
        self.device_source.setText("SOURCE  MISSING")
        self.device_resolution.setText("RES  NO SIGNAL")
        self.device_time.setText("TIME  —")
        self.device_signal.setText("● RECORD MODE")
        self.device_mode.setText("ARCHIVE / MISSING RECORD")
        self.stack.setCurrentWidget(self.missing_page)


    def release_media(self) -> None:
        self.media_player.stop()
        self.media_player.setSource(QUrl())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_image()
        self._fit_poster()

    def _fit_image(self) -> None:
        if self._image_pixmap.isNull():
            return
        self.image_label.setPixmap(
            self._image_pixmap.scaled(
                self.image_scroll.viewport().size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _toggle_playback(self) -> None:
        if self._poster_pending:
            self._poster_pending = False
            self.audio_output.setMuted(self.mute_button.isChecked())
            self.video_display.setCurrentIndex(1)
            if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                return
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.video_display.setCurrentIndex(1)
            self.media_player.play()

    def _toggle_mute(self, muted: bool) -> None:
        label = "ミュート解除" if muted else "ミュート"
        self.mute_button.setIcon(_speaker_icon(muted))
        self.mute_button.setToolTip(label)
        self.mute_button.setAccessibleName(label)
        if not self._poster_pending:
            self.audio_output.setMuted(muted)

    def _set_volume(self, value: int) -> None:
        self.audio_output.setVolume(value / 100)
        self.volume_slider.setToolTip(f"音量: {value}%")

    def _capture_poster(self, frame) -> None:
        if not self._poster_pending or not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        self._poster_pixmap = QPixmap.fromImage(image)
        self._poster_pending = False
        self.media_player.pause()
        self.media_player.setPosition(0)
        self.audio_output.setMuted(self.mute_button.isChecked())
        self.video_display.setCurrentIndex(0)
        self._fit_poster()

    def _fit_poster(self) -> None:
        if self._poster_pixmap.isNull():
            return
        self.video_poster.setPixmap(
            self._poster_pixmap.scaled(
                self.video_display.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _update_play_button(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        label = "一時停止" if playing else "再生"
        self.play_button.setIcon(_pause_icon() if playing else _play_icon())
        self.play_button.setToolTip(label)
        self.play_button.setAccessibleName(label)
        if not playing:
            self.playbackIdle.emit()

    def _update_position(self, position: int) -> None:
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)
        duration = self.media_player.duration()
        self._update_time(position, duration)
        self._check_video_engagement(position, duration)

    def _check_video_engagement(self, position: int, duration: int) -> None:
        """再生位置が全体の75%へ達した時だけ評価案内条件を通知する。"""
        if duration > 0 and position * 4 >= duration * 3:
            self._emit_rating_engagement()

    def _update_duration(self, duration: int) -> None:
        self.position_slider.setRange(0, duration)
        self._update_time(self.media_player.position(), duration)

    def _update_time(self, position: int, duration: int) -> None:
        self.time_label.setText(
            f"{_format_clock(position)} / {_format_clock(duration)}"
        )
        self.device_time.setText(
            f"TIME  {_format_clock(position)} / {_format_clock(duration)}"
        )

    def _on_media_error(self, _error: QMediaPlayer.Error, message: str) -> None:
        if message:
            self._poster_pending = False
            self.audio_output.setMuted(self.mute_button.isChecked())
            self.mediaError.emit(
                "動画を再生できませんでした。Windowsの対応コーデックを確認してください。"
            )

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._emit_rating_engagement()
            self.playbackIdle.emit()

    def _emit_rating_engagement(self) -> None:
        if self._engagement_emitted:
            return
        self._engagement_emitted = True
        self.ratingEngagementReached.emit()

    def is_video_playing(self) -> bool:
        return (
            self.stack.currentIndex() == 2
            and self.media_player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        )


def _format_clock(milliseconds: int) -> str:
    total_seconds = max(0, milliseconds // 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _media_pixmap() -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#4f4563"), 1.8))
    painter.setBrush(QColor("#4f4563"))
    return pixmap, painter


def _play_icon() -> QIcon:
    pixmap, painter = _media_pixmap()
    painter.drawPolygon(QPolygonF((QPointF(8, 5), QPointF(19, 12), QPointF(8, 19))))
    painter.end()
    return QIcon(pixmap)


def _pause_icon() -> QIcon:
    pixmap, painter = _media_pixmap()
    painter.drawRoundedRect(7, 5, 3, 14, 1, 1)
    painter.drawRoundedRect(14, 5, 3, 14, 1, 1)
    painter.end()
    return QIcon(pixmap)


def _speaker_icon(muted: bool) -> QIcon:
    pixmap, painter = _media_pixmap()
    painter.drawPolygon(
        QPolygonF(
            (QPointF(5, 10), QPointF(9, 10), QPointF(14, 6), QPointF(14, 18),
             QPointF(9, 14), QPointF(5, 14))
        )
    )
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if muted:
        painter.drawLine(QPointF(17, 9), QPointF(22, 15))
        painter.drawLine(QPointF(22, 9), QPointF(17, 15))
    else:
        painter.drawArc(14, 8, 7, 8, -60 * 16, 120 * 16)
    painter.end()
    return QIcon(pixmap)
