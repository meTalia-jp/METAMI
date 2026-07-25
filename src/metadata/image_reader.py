"""Qt標準機能で静止画を安全に読み込み、表示向きを統一する。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage, QImageReader


class ImageReadError(Exception):
    """静止画を読み込めない場合のエラー。"""


def read_oriented_image(path: Path) -> QImage:
    """EXIF Orientation等を反映した画像を返す。原本へは書き込まない。"""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        message = reader.errorString() or "画像を読み込めませんでした。"
        raise ImageReadError(message)
    return image
