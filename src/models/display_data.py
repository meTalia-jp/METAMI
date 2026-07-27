"""GUIへ渡す、形式に依存しない表示データ。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LtxDisplayData:
    """LTX 2.3専用タブへ渡す生成情報。"""

    detected: bool = False
    workflow_name: str | None = None
    prompt_text: str | None = None
    prompt_enhance_enabled: bool | None = None
    configured_width: int | None = None
    configured_height: int | None = None
    configured_duration: float | None = None
    configured_fps: float | None = None
    configured_frame_count: int | None = None
    main_seed: int | None = None
    secondary_seed: int | None = None
    actual_width: int | None = None
    actual_height: int | None = None
    actual_duration: float | None = None
    actual_fps: float | None = None
    actual_frame_count: int | None = None
    checkpoint: str | None = None
    sampler: str | None = None
    cfg: float | None = None
    negative_prompt: str | None = None
    distilled_lora: str | None = None
    distilled_lora_strength: float | None = None
    text_encoder: str | None = None
    latent_upscaler: str | None = None
    enhance_lora: str | None = None
    enhance_lora_model_strength: float | None = None
    enhance_lora_clip_strength: float | None = None
    parse_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DisplayData:
    """右ペインの各タブへ表示する情報。"""

    basic_info: tuple[tuple[str, str], ...]
    generation_info: tuple[tuple[str, str], ...]
    workflow_text: str
    json_text: str
    metadata_count: int
    status: str
    ltx: LtxDisplayData | None = None
