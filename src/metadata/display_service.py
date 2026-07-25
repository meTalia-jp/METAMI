"""各形式の抽出結果をGUI向け表示データへ整理する。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from metadata.image_reader import ImageReadError, read_oriented_image
from metadata.mp4_reader import Mp4ReadError, read_mp4_info
from metadata.png_reader import PngMetadataError, read_png_dimensions, read_png_metadata
from metadata.webp_reader import WebpReadError, read_webp_info
from models.display_data import DisplayData


GENERATION_FIELDS = (
    ("jp_prompt", ("jpprompt",)),
    ("Prompt", ("prompt", "positiveprompt", "positive")),
    ("Negative Prompt", ("negativeprompt", "negative")),
    ("Seed", ("seed", "noiseseed")),
    ("Model", ("model", "modelname", "ckptname")),
    ("Sampler", ("sampler", "samplername")),
    ("Scheduler", ("scheduler",)),
    ("Steps", ("steps",)),
    ("CFG", ("cfg", "cfgscale")),
    ("VAE", ("vae", "vaename")),
    ("LoRA", ("lora", "loraname")),
)


class DisplayDataError(Exception):
    """ファイル情報を表示用に整理できない場合のエラー。"""


def build_display_data(path: Path) -> DisplayData:
    """対応ファイルを読み取り、4タブ分の表示データを返す。"""
    try:
        stat = path.stat()
        basic: list[tuple[str, str]] = [
            ("ファイル名", path.name),
            ("パス", str(path.resolve())),
            ("形式", path.suffix.removeprefix(".").upper()),
            ("ファイルサイズ", _format_file_size(stat.st_size)),
        ]
        raw_metadata: list[tuple[str, Any]]
        suffix = path.suffix.lower()

        if suffix == ".png":
            width, height = read_png_dimensions(path)
            basic.append(("解像度", f"{width} × {height} ピクセル"))
            raw_metadata = [
                (key, _parse_json(value)) for key, value in read_png_metadata(path)
            ]
        elif suffix == ".webp":
            info = read_webp_info(path)
            basic.extend(
                (
                    ("解像度", f"{info.width} × {info.height} ピクセル"),
                    ("圧縮形式", info.encoding),
                )
            )
            raw_metadata = [
                (item.name, _decode_webp_metadata(item.data, item.size))
                for item in info.metadata
            ]
        elif suffix in {".jpg", ".jpeg"}:
            image = read_oriented_image(path)
            basic.append(
                ("解像度", f"{image.width()} × {image.height()} ピクセル")
            )
            # JPEG固有EXIFの詳細抽出はVer0.9.2の対象外。
            raw_metadata = []
        elif suffix == ".mp4":
            info = read_mp4_info(path)
            dimensions = (
                f"{info.width} × {info.height} ピクセル"
                if info.width is not None and info.height is not None
                else "情報なし"
            )
            duration = (
                _format_duration(info.duration_seconds)
                if info.duration_seconds is not None
                else "情報なし"
            )
            basic.extend(
                (
                    ("解像度", dimensions),
                    ("再生時間", duration),
                    ("主要ブランド", info.major_brand),
                    ("互換ブランド", ", ".join(info.compatible_brands) or "情報なし"),
                )
            )
            raw_metadata = [(key, _parse_json(value)) for key, value in info.metadata]
        else:
            raise DisplayDataError("このファイル形式には対応していません。")

        basic.extend(
            (
                ("作成日時", _format_timestamp(stat.st_ctime)),
                ("更新日時", _format_timestamp(stat.st_mtime)),
            )
        )
        generation = _extract_generation_info(raw_metadata)
        workflow = _extract_workflow(raw_metadata)
        json_text = _format_full_metadata(raw_metadata)
        count = len(raw_metadata)
        status = f"メタデータ {count}項目" if count else "メタデータなし"
        return DisplayData(
            basic_info=tuple(basic),
            generation_info=tuple(generation),
            workflow_text=workflow,
            json_text=json_text,
            metadata_count=count,
            status=status,
        )
    except DisplayDataError:
        raise
    except (
        OSError,
        ImageReadError,
        PngMetadataError,
        WebpReadError,
        Mp4ReadError,
    ) as error:
        raise DisplayDataError(str(error)) from error


def _extract_generation_info(
    metadata: list[tuple[str, Any]],
) -> list[tuple[str, str]]:
    found: dict[str, list[str]] = {label: [] for label, _aliases in GENERATION_FIELDS}
    aliases = {
        alias: label
        for label, field_aliases in GENERATION_FIELDS
        for alias in field_aliases
    }

    for _key, value in metadata:
        positive, negative = _extract_comfy_text_prompts(value)
        found["Prompt"].extend(text for text in positive if text not in found["Prompt"])
        found["Negative Prompt"].extend(
            text for text in negative if text not in found["Negative Prompt"]
        )
        found["jp_prompt"].extend(
            text
            for text in positive
            if any(ord(character) > 127 for character in text)
            and text not in found["jp_prompt"]
        )

    def visit(key: str, value: Any) -> None:
        normalized = _normalize_key(key)
        label = aliases.get(normalized)
        if label and _is_display_scalar(value):
            rendered = _render_value(value)
            if rendered and rendered not in found[label]:
                found[label].append(rendered)
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(str(child_key), child_value)
        elif isinstance(value, list):
            for child_value in value:
                if isinstance(child_value, (dict, list)):
                    visit(key, child_value)

    for key, value in metadata:
        visit(key, value)

    return [
        (
            label,
            "\n".join(found[label][:20]) if found[label] else "情報なし",
        )
        for label, _aliases in GENERATION_FIELDS
    ]


def _extract_workflow(metadata: Iterable[tuple[str, Any]]) -> str:
    for key, value in metadata:
        if _normalize_key(key) == "workflow":
            return _render_value(value, pretty=True)
    return "Workflow情報はありません。"


def _format_full_metadata(metadata: list[tuple[str, Any]]) -> str:
    if not metadata:
        return "メタデータはありません。"
    merged: dict[str, Any] = {}
    for key, value in metadata:
        if key not in merged:
            merged[key] = value
        elif isinstance(merged[key], list):
            merged[key].append(value)
        else:
            merged[key] = [merged[key], value]
    return json.dumps(merged, ensure_ascii=False, indent=2, default=str)


def _parse_json(value: str) -> Any:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _decode_webp_metadata(data: bytes | None, size: int) -> Any:
    if data is None:
        return f"{size:,}バイト（内容表示を省略）"
    try:
        return _parse_json(data.decode("utf-8"))
    except UnicodeDecodeError:
        return f"{size:,}バイトのバイナリデータ"


def _normalize_key(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def _is_display_scalar(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (str, int, float))
        and str(value).strip() != ""
    )


def _extract_comfy_text_prompts(value: Any) -> tuple[list[str], list[str]]:
    """ComfyUIのAPI形式ノードからテキスト入力を抽出する。"""
    positive: list[str] = []
    negative: list[str] = []
    if not isinstance(value, dict):
        return positive, negative
    nodes = {str(key): node for key, node in value.items() if isinstance(node, dict)}

    def collect_text(reference: Any, visited: set[str]) -> list[str]:
        results: list[str] = []
        references: list[tuple[str, int | None]] = []
        if isinstance(reference, (list, tuple)) and reference:
            candidate = str(reference[0])
            if candidate in nodes:
                output_index = (
                    reference[1]
                    if len(reference) > 1 and isinstance(reference[1], int)
                    else None
                )
                references.append((candidate, output_index))
            else:
                for child in reference:
                    results.extend(collect_text(child, visited))
        elif isinstance(reference, dict):
            for child in reference.values():
                results.extend(collect_text(child, visited))
        for node_id, output_index in references:
            if node_id in visited:
                continue
            visited.add(node_id)
            node = nodes[node_id]
            class_type = str(node.get("class_type", "")).casefold()
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                continue
            # ConditioningZeroOut は入力文を負プロンプトとして使うノードではなく、
            # 入力された条件をゼロ化するノード。元の正文を辿らない。
            if "conditioningzeroout" in _normalize_key(class_type):
                continue
            text = inputs.get("text")
            if (
                ("textencode" in class_type or "cliptext" in class_type)
                and isinstance(text, str)
                and text.strip()
            ):
                results.append(text)
            source_value = inputs.get("value")
            if (
                "primitivestring" in class_type
                and isinstance(source_value, str)
                and source_value.strip()
            ):
                results.append(source_value)
            class_key = _normalize_key(class_type)
            if (
                output_index in (0, 1)
                and "positive" in inputs
                and "negative" in inputs
                and (
                    "conditioning" in class_key
                    or "cropguides" in class_key
                    or "wanimagetovideo" in class_key
                )
            ):
                children = [
                    inputs["positive"] if output_index == 0 else inputs["negative"]
                ]
            else:
                children = list(inputs.values())
            for child in children:
                results.extend(collect_text(child, visited))
        return results

    for node in nodes.values():
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if "positive" in inputs:
            positive.extend(
                text
                for text in collect_text(inputs["positive"], set())
                if text not in positive
            )
        if "negative" in inputs:
            negative.extend(
                text
                for text in collect_text(inputs["negative"], set())
                if text not in negative
            )
    if positive or negative:
        return positive, negative

    for node in nodes.values():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type", "")).casefold()
        if "textencode" not in class_type and "cliptext" not in class_type:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        text = inputs.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
        title = str(meta.get("title", "")).casefold()
        target = (
            negative
            if "negative" in title or "ネガティブ" in title or "否定" in title
            else positive
        )
        if text not in target:
            target.append(text)
    return positive, negative


def _render_value(value: Any, pretty: bool = False) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2 if pretty else None)
    return str(value)


def _format_file_size(size: int) -> str:
    if size < 1024:
        return f"{size:,} バイト"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB（{size:,} バイト）"
    return f"{size / (1024 * 1024):.1f} MB（{size:,} バイト）"


def _format_duration(seconds: float) -> str:
    total_milliseconds = round(seconds * 1000)
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    if hours:
        return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"
    return f"{minutes}:{whole_seconds:02d}.{milliseconds:03d}"


def _format_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
