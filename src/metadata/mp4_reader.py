"""MP4の基本情報と文字列メタデータを読み取る。"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


MAX_METADATA_CONTAINER_SIZE = 64 * 1024 * 1024
METADATA_NAMES = {
    b"\xa9nam": "タイトル",
    b"\xa9ART": "アーティスト",
    b"\xa9alb": "アルバム",
    b"\xa9cmt": "コメント",
    b"\xa9day": "作成日",
    b"\xa9too": "エンコーダー",
    b"cprt": "著作権",
    b"desc": "説明",
    b"ldes": "詳細説明",
}


class Mp4ReadError(Exception):
    """MP4を安全に読み取れない場合のエラー。"""


@dataclass(frozen=True)
class Mp4Info:
    """画面表示に必要なMP4情報。"""

    file_size: int
    major_brand: str
    compatible_brands: tuple[str, ...]
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    frame_count: int | None
    metadata: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _Box:
    box_type: bytes
    payload_start: int
    end: int


def read_mp4_info(path: Path) -> Mp4Info:
    """MP4の大容量映像データを読み込まず、管理情報を返す。"""
    try:
        file_size = path.stat().st_size
        major_brand = ""
        compatible_brands: tuple[str, ...] = ()
        moov_data: bytes | None = None

        with path.open("rb") as mp4_file:
            offset = 0
            while offset < file_size:
                box_type, payload_start, box_end = _read_file_box(
                    mp4_file, offset, file_size
                )
                payload_size = box_end - payload_start
                if box_type == b"ftyp":
                    if payload_size < 8 or payload_size > 1024 * 1024:
                        raise Mp4ReadError("MP4の形式情報が正しくありません。")
                    mp4_file.seek(payload_start)
                    ftyp_data = mp4_file.read(payload_size)
                    major_brand = _decode_brand(ftyp_data[:4])
                    compatible_brands = tuple(
                        _decode_brand(ftyp_data[index : index + 4])
                        for index in range(8, len(ftyp_data) - 3, 4)
                    )
                elif box_type == b"moov":
                    if payload_size > MAX_METADATA_CONTAINER_SIZE:
                        raise Mp4ReadError("MP4の管理情報が大きすぎます。")
                    mp4_file.seek(payload_start)
                    moov_data = mp4_file.read(payload_size)
                    if len(moov_data) != payload_size:
                        raise Mp4ReadError("MP4の管理情報が途中で終わっています。")
                offset = box_end

        if not major_brand or moov_data is None:
            raise Mp4ReadError("MP4に必要な形式情報がありません。")

        duration = _read_duration(moov_data)
        width, height, fps, frame_count = _read_video_track_info(moov_data)
        metadata = _read_metadata(moov_data)
        return Mp4Info(
            file_size=file_size,
            major_brand=major_brand,
            compatible_brands=compatible_brands,
            duration_seconds=duration,
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
            metadata=tuple(metadata),
        )
    except Mp4ReadError:
        raise
    except (OSError, struct.error, UnicodeDecodeError) as error:
        raise Mp4ReadError("MP4ファイルを読み取れませんでした。") from error


def _read_file_box(mp4_file, offset: int, file_size: int) -> tuple[bytes, int, int]:
    mp4_file.seek(offset)
    header = mp4_file.read(8)
    if len(header) != 8:
        raise Mp4ReadError("MP4ボックスが途中で終わっています。")
    size, box_type = struct.unpack(">I4s", header)
    header_size = 8
    if size == 1:
        extended = mp4_file.read(8)
        if len(extended) != 8:
            raise Mp4ReadError("MP4ボックスが途中で終わっています。")
        size = struct.unpack(">Q", extended)[0]
        header_size = 16
    elif size == 0:
        size = file_size - offset
    if size < header_size or offset + size > file_size:
        raise Mp4ReadError("MP4ボックスのサイズが正しくありません。")
    return box_type, offset + header_size, offset + size


def _iter_boxes(data: bytes, start: int = 0, end: int | None = None) -> Iterator[_Box]:
    limit = len(data) if end is None else end
    offset = start
    while offset < limit:
        if limit - offset < 8:
            raise Mp4ReadError("MP4管理ボックスが途中で終わっています。")
        size, box_type = struct.unpack_from(">I4s", data, offset)
        header_size = 8
        if size == 1:
            if limit - offset < 16:
                raise Mp4ReadError("MP4管理ボックスが途中で終わっています。")
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header_size = 16
        elif size == 0:
            size = limit - offset
        box_end = offset + size
        if size < header_size or box_end > limit:
            raise Mp4ReadError("MP4管理ボックスのサイズが正しくありません。")
        yield _Box(box_type, offset + header_size, box_end)
        offset = box_end


def _read_duration(moov: bytes) -> float | None:
    mvhd = next((box for box in _iter_boxes(moov) if box.box_type == b"mvhd"), None)
    if mvhd is None:
        return None
    payload = moov[mvhd.payload_start : mvhd.end]
    if len(payload) < 20:
        raise Mp4ReadError("MP4の再生時間情報が不足しています。")
    version = payload[0]
    if version == 0:
        timescale, duration = struct.unpack_from(">II", payload, 12)
    elif version == 1 and len(payload) >= 32:
        timescale = struct.unpack_from(">I", payload, 20)[0]
        duration = struct.unpack_from(">Q", payload, 24)[0]
    else:
        raise Mp4ReadError("未対応のMP4再生時間情報です。")
    return duration / timescale if timescale else None


def _read_video_track_info(
    moov: bytes,
) -> tuple[int | None, int | None, float | None, int | None]:
    for trak in (box for box in _iter_boxes(moov) if box.box_type == b"trak"):
        children = list(_iter_boxes(moov, trak.payload_start, trak.end))
        tkhd = next((box for box in children if box.box_type == b"tkhd"), None)
        mdia = next((box for box in children if box.box_type == b"mdia"), None)
        if tkhd is None or mdia is None:
            continue
        handler = next(
            (
                box
                for box in _iter_boxes(moov, mdia.payload_start, mdia.end)
                if box.box_type == b"hdlr"
            ),
            None,
        )
        if handler is None or handler.end - handler.payload_start < 12:
            continue
        if moov[handler.payload_start + 8 : handler.payload_start + 12] != b"vide":
            continue
        if tkhd.end - tkhd.payload_start < 8:
            raise Mp4ReadError("MP4の映像サイズ情報が不足しています。")
        width_fixed, height_fixed = struct.unpack_from(">II", moov, tkhd.end - 8)
        width, height = width_fixed >> 16, height_fixed >> 16
        timescale, media_duration = _read_media_duration(moov, mdia)
        frame_count, timing_duration = _read_sample_timing(moov, mdia)
        duration_units = timing_duration or media_duration
        fps = (
            frame_count * timescale / duration_units
            if frame_count is not None and timescale and duration_units
            else None
        )
        return width, height, fps, frame_count
    return None, None, None, None


def _read_media_duration(data: bytes, mdia: _Box) -> tuple[int | None, int | None]:
    mdhd = next(
        (
            box
            for box in _iter_boxes(data, mdia.payload_start, mdia.end)
            if box.box_type == b"mdhd"
        ),
        None,
    )
    if mdhd is None:
        return None, None
    payload = data[mdhd.payload_start : mdhd.end]
    if len(payload) < 20:
        return None, None
    if payload[0] == 0:
        return struct.unpack_from(">II", payload, 12)
    if payload[0] == 1 and len(payload) >= 32:
        timescale = struct.unpack_from(">I", payload, 20)[0]
        duration = struct.unpack_from(">Q", payload, 24)[0]
        return timescale, duration
    return None, None


def _read_sample_timing(
    data: bytes, mdia: _Box
) -> tuple[int | None, int | None]:
    mdia_children = list(_iter_boxes(data, mdia.payload_start, mdia.end))
    minf = next((box for box in mdia_children if box.box_type == b"minf"), None)
    if minf is None:
        return None, None
    stbl = next(
        (
            box
            for box in _iter_boxes(data, minf.payload_start, minf.end)
            if box.box_type == b"stbl"
        ),
        None,
    )
    if stbl is None:
        return None, None
    children = list(_iter_boxes(data, stbl.payload_start, stbl.end))
    stts = next((box for box in children if box.box_type == b"stts"), None)
    stsz = next((box for box in children if box.box_type == b"stsz"), None)
    frame_count: int | None = None
    timing_count: int | None = None
    timing_duration: int | None = None
    if stsz is not None:
        payload = data[stsz.payload_start : stsz.end]
        if len(payload) >= 12:
            frame_count = struct.unpack_from(">I", payload, 8)[0]
    if stts is not None:
        payload = data[stts.payload_start : stts.end]
        if len(payload) >= 8:
            entry_count = struct.unpack_from(">I", payload, 4)[0]
            offset = 8
            total_count = 0
            total_duration = 0
            for _index in range(entry_count):
                if len(payload) - offset < 8:
                    break
                count, delta = struct.unpack_from(">II", payload, offset)
                total_count += count
                total_duration += count * delta
                offset += 8
            if offset == 8 + entry_count * 8:
                timing_count = total_count
                timing_duration = total_duration
    if frame_count is None:
        frame_count = timing_count
    return frame_count, timing_duration


def _read_metadata(moov: bytes) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for udta in (box for box in _iter_boxes(moov) if box.box_type == b"udta"):
        for child in _iter_boxes(moov, udta.payload_start, udta.end):
            if child.box_type == b"meta":
                if child.end - child.payload_start < 4:
                    continue
                meta_children = list(
                    _iter_boxes(moov, child.payload_start + 4, child.end)
                )
                key_names: dict[int, str] = {}
                keys = next(
                    (box for box in meta_children if box.box_type == b"keys"), None
                )
                if keys is not None:
                    key_names = _read_metadata_keys(moov, keys)
                for meta_child in meta_children:
                    if meta_child.box_type == b"ilst":
                        results.extend(_read_ilst(moov, meta_child, key_names))
            elif child.box_type in METADATA_NAMES:
                value = _decode_text(moov[child.payload_start : child.end])
                if value:
                    results.append((METADATA_NAMES[child.box_type], value))
    return results


def _read_metadata_keys(data: bytes, keys: _Box) -> dict[int, str]:
    payload = data[keys.payload_start : keys.end]
    if len(payload) < 8:
        raise Mp4ReadError("MP4のメタデータ項目名が不足しています。")
    entry_count = struct.unpack_from(">I", payload, 4)[0]
    offset = 8
    results: dict[int, str] = {}
    for index in range(1, entry_count + 1):
        if len(payload) - offset < 8:
            raise Mp4ReadError("MP4のメタデータ項目名が途中で終わっています。")
        entry_size = struct.unpack_from(">I", payload, offset)[0]
        if entry_size < 8 or offset + entry_size > len(payload):
            raise Mp4ReadError("MP4のメタデータ項目名が正しくありません。")
        name = _decode_text(payload[offset + 8 : offset + entry_size])
        if name:
            results[index] = name
        offset += entry_size
    return results


def _read_ilst(
    data: bytes, ilst: _Box, key_names: dict[int, str]
) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for item in _iter_boxes(data, ilst.payload_start, ilst.end):
        numeric_index = int.from_bytes(item.box_type, "big")
        item_name = METADATA_NAMES.get(
            item.box_type, key_names.get(numeric_index, _decode_brand(item.box_type))
        )
        custom_name = ""
        values: list[str] = []
        for child in _iter_boxes(data, item.payload_start, item.end):
            payload = data[child.payload_start : child.end]
            if child.box_type == b"name":
                custom_name = _decode_text(payload[4:])
            elif child.box_type == b"data" and len(payload) >= 8:
                value = _decode_text(payload[8:])
                if value:
                    values.append(value)
        if item.box_type == b"----" and custom_name:
            item_name = custom_name
        results.extend((item_name, value) for value in values)
    return results


def _decode_text(data: bytes) -> str:
    cleaned = data.strip(b"\0")
    if not cleaned:
        return ""
    for encoding in ("utf-8", "utf-16-be", "latin-1"):
        try:
            return cleaned.decode(encoding).strip("\0")
        except UnicodeDecodeError:
            continue
    return ""


def _decode_brand(brand: bytes) -> str:
    return brand.decode("latin-1").strip()
