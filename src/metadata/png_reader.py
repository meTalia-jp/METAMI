"""PNGに埋め込まれたテキストメタデータを読み取る。"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TEXT_CHUNK_TYPES = {b"tEXt", b"zTXt", b"iTXt"}
MAX_CHUNK_SIZE = 64 * 1024 * 1024
MAX_TEXT_SIZE = 16 * 1024 * 1024


class PngMetadataError(Exception):
    """PNGを安全に読み取れない場合のエラー。"""


def read_png_dimensions(path: Path) -> tuple[int, int]:
    """PNGのIHDRから幅と高さを読み取る。"""
    try:
        with path.open("rb") as png_file:
            header = png_file.read(24)
        if (
            len(header) != 24
            or header[:8] != PNG_SIGNATURE
            or header[12:16] != b"IHDR"
        ):
            raise PngMetadataError("PNGの画像情報を読み取れませんでした。")
        return struct.unpack(">II", header[16:24])
    except PngMetadataError:
        raise
    except (OSError, struct.error) as error:
        raise PngMetadataError("PNGの画像情報を読み取れませんでした。") from error


def read_png_metadata(path: Path) -> list[tuple[str, str]]:
    """PNGのテキストチャンクを出現順に返す。"""
    metadata: list[tuple[str, str]] = []

    try:
        with path.open("rb") as png_file:
            if png_file.read(len(PNG_SIGNATURE)) != PNG_SIGNATURE:
                raise PngMetadataError("PNG形式のファイルではありません。")

            while True:
                header = png_file.read(8)
                if len(header) != 8:
                    raise PngMetadataError("PNGデータが途中で終わっています。")

                length, chunk_type = struct.unpack(">I4s", header)
                if length > MAX_CHUNK_SIZE:
                    raise PngMetadataError("大きすぎるPNGチャンクを検出しました。")

                chunk_data = png_file.read(length)
                crc_bytes = png_file.read(4)
                if len(chunk_data) != length or len(crc_bytes) != 4:
                    raise PngMetadataError("PNGチャンクが途中で終わっています。")

                expected_crc = struct.unpack(">I", crc_bytes)[0]
                actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
                if expected_crc != actual_crc:
                    raise PngMetadataError("PNGチャンクの整合性を確認できません。")

                if chunk_type in TEXT_CHUNK_TYPES:
                    metadata.append(_decode_text_chunk(chunk_type, chunk_data))

                if chunk_type == b"IEND":
                    break
    except PngMetadataError:
        raise
    except (OSError, struct.error, zlib.error) as error:
        raise PngMetadataError("PNGメタデータを読み取れませんでした。") from error

    return metadata


def _decode_text_chunk(chunk_type: bytes, data: bytes) -> tuple[str, str]:
    try:
        if chunk_type == b"tEXt":
            keyword, text = data.split(b"\0", 1)
            return _decode_keyword(keyword), text.decode("latin-1")

        if chunk_type == b"zTXt":
            keyword, remainder = data.split(b"\0", 1)
            if not remainder or remainder[0] != 0:
                raise ValueError
            text = _decompress_text(remainder[1:]).decode("latin-1")
            return _decode_keyword(keyword), text

        keyword, remainder = data.split(b"\0", 1)
        if len(remainder) < 2:
            raise ValueError
        compression_flag, compression_method = remainder[:2]
        language, translated_and_text = remainder[2:].split(b"\0", 1)
        translated_keyword, text = translated_and_text.split(b"\0", 1)
        if compression_flag not in (0, 1) or compression_method != 0:
            raise ValueError
        if compression_flag == 1:
            text = _decompress_text(text)
        # 言語タグと翻訳キーワードも構造検証のためデコードする。
        language.decode("ascii")
        translated_keyword.decode("utf-8")
        return _decode_keyword(keyword), text.decode("utf-8")
    except (UnicodeDecodeError, ValueError, zlib.error) as error:
        raise PngMetadataError("PNG内のテキスト情報を解釈できません。") from error


def _decode_keyword(keyword: bytes) -> str:
    if not 1 <= len(keyword) <= 79:
        raise PngMetadataError("PNG内の項目名が正しくありません。")
    return keyword.decode("latin-1")


def _decompress_text(data: bytes) -> bytes:
    decompressor = zlib.decompressobj()
    text = decompressor.decompress(data, MAX_TEXT_SIZE + 1)
    if (
        len(text) > MAX_TEXT_SIZE
        or decompressor.unconsumed_tail
        or not decompressor.eof
    ):
        raise PngMetadataError("PNG内のテキスト情報が大きすぎます。")
    return text
