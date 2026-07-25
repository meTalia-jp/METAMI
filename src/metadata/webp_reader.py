"""WEBPの基本画像情報とメタデータチャンクを読み取る。"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


METADATA_CHUNK_NAMES = {
    b"EXIF": "EXIF",
    b"XMP ": "XMP",
    b"ICCP": "ICCプロファイル",
}
MAX_METADATA_SIZE = 16 * 1024 * 1024


class WebpReadError(Exception):
    """WEBPを安全に読み取れない場合のエラー。"""


@dataclass(frozen=True)
class WebpMetadata:
    """WEBPに埋め込まれたメタデータチャンク。"""

    name: str
    size: int
    data: bytes | None


@dataclass(frozen=True)
class WebpInfo:
    """画面表示に必要なWEBP情報。"""

    width: int
    height: int
    file_size: int
    encoding: str
    metadata: tuple[WebpMetadata, ...]


def read_webp_info(path: Path) -> WebpInfo:
    """RIFFコンテナを検証し、WEBPの基本情報を返す。"""
    try:
        file_size = path.stat().st_size
        with path.open("rb") as webp_file:
            header = webp_file.read(12)
            if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WEBP":
                raise WebpReadError("WEBP形式のファイルではありません。")

            riff_size = struct.unpack("<I", header[4:8])[0]
            container_end = riff_size + 8
            if container_end != file_size:
                raise WebpReadError("WEBPデータのサイズが正しくありません。")

            width = height = 0
            encoding = "不明"
            metadata: list[WebpMetadata] = []

            while webp_file.tell() < container_end:
                chunk_header = webp_file.read(8)
                if len(chunk_header) != 8:
                    raise WebpReadError("WEBPデータが途中で終わっています。")

                chunk_type, chunk_size = struct.unpack("<4sI", chunk_header)
                data_start = webp_file.tell()
                data_end = data_start + chunk_size
                padded_end = data_end + (chunk_size & 1)
                if padded_end > container_end:
                    raise WebpReadError("WEBPチャンクが途中で終わっています。")

                prefix = webp_file.read(min(chunk_size, 10))
                if chunk_type in (b"VP8X", b"VP8 ", b"VP8L"):
                    chunk_width, chunk_height = _read_dimensions(chunk_type, prefix)
                    if chunk_width and chunk_height:
                        width, height = chunk_width, chunk_height
                    if chunk_type == b"VP8 ":
                        encoding = "VP8（非可逆）"
                    elif chunk_type == b"VP8L":
                        encoding = "VP8L（可逆）"
                    elif encoding == "不明":
                        encoding = "VP8X（拡張形式）"

                metadata_name = METADATA_CHUNK_NAMES.get(chunk_type)
                if metadata_name:
                    webp_file.seek(data_start)
                    data = (
                        webp_file.read(chunk_size)
                        if chunk_size <= MAX_METADATA_SIZE
                        else None
                    )
                    metadata.append(WebpMetadata(metadata_name, chunk_size, data))

                webp_file.seek(padded_end)

            if not width or not height:
                raise WebpReadError("WEBPの画像サイズを取得できませんでした。")

            return WebpInfo(
                width=width,
                height=height,
                file_size=file_size,
                encoding=encoding,
                metadata=tuple(metadata),
            )
    except WebpReadError:
        raise
    except (OSError, struct.error) as error:
        raise WebpReadError("WEBPファイルを読み取れませんでした。") from error


def _read_dimensions(chunk_type: bytes, data: bytes) -> tuple[int, int]:
    if chunk_type == b"VP8X":
        if len(data) < 10:
            raise WebpReadError("VP8X画像情報が不足しています。")
        return int.from_bytes(data[4:7], "little") + 1, int.from_bytes(
            data[7:10], "little"
        ) + 1

    if chunk_type == b"VP8L":
        if len(data) < 5 or data[0] != 0x2F:
            raise WebpReadError("VP8L画像情報が正しくありません。")
        bits = int.from_bytes(data[1:5], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1

    if len(data) < 10 or data[3:6] != b"\x9d\x01\x2a":
        raise WebpReadError("VP8画像情報が正しくありません。")
    width = int.from_bytes(data[6:8], "little") & 0x3FFF
    height = int.from_bytes(data[8:10], "little") & 0x3FFF
    return width, height
