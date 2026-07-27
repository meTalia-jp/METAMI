"""LTX 2.3公式ComfyUIワークフローの表示用情報を抽出する。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from models.display_data import LtxDisplayData


def read_ltx_info(
    metadata: Iterable[tuple[str, Any]],
    *,
    actual_width: int | None = None,
    actual_height: int | None = None,
    actual_duration: float | None = None,
    actual_fps: float | None = None,
    actual_frame_count: int | None = None,
) -> LtxDisplayData:
    """promptを優先し、workflowを判定補助としてLTX情報を返す。"""
    values = list(metadata)
    prompt = _metadata_dict(values, "prompt")
    workflow = _metadata_dict(values, "workflow")
    workflow_name = _workflow_name(workflow)
    detected = _is_ltx_prompt(prompt) or _is_ltx_workflow(workflow)
    base = LtxDisplayData(
        detected=detected,
        workflow_name=workflow_name,
        actual_width=actual_width,
        actual_height=actual_height,
        actual_duration=actual_duration,
        actual_fps=actual_fps,
        actual_frame_count=actual_frame_count,
    )
    if not detected:
        return base

    warnings: list[str] = []
    if prompt is None:
        warnings.append("prompt JSONがないため、workflow JSONから取得しました。")
        result = _read_workflow_fallback(workflow, base)
        return replace(result, parse_warnings=tuple(warnings))

    nodes = {
        str(node_id): node
        for node_id, node in prompt.items()
        if isinstance(node, dict)
    }
    if not nodes:
        return replace(base, parse_warnings=("prompt JSONにノードがありません。",))

    prompt_text = _primitive_by_title(nodes, "prompt", "string")
    enhance = _primitive_by_title(nodes, "enable prompt enhance", "boolean")
    width = _primitive_by_title(nodes, "width", "int")
    height = _primitive_by_title(nodes, "height", "int")
    duration = _primitive_by_title(nodes, "duration", "int")
    fps = _primitive_by_title(nodes, "frame rate", "int")
    frame_count = _configured_frame_count(nodes)
    main_seed, secondary_seed = _sampler_seeds(nodes, warnings)

    checkpoint = _connected_model_value(
        nodes, "CheckpointLoaderSimple", "ckpt_name", ("CFGGuider",)
    )
    if checkpoint is None:
        checkpoint = _unique_input(nodes, "CheckpointLoaderSimple", "ckpt_name", warnings)
    sampler = _unique_input(nodes, "KSamplerSelect", "sampler_name", warnings)
    cfg = _unique_input(nodes, "CFGGuider", "cfg", warnings)
    distilled = _connected_model_node(nodes, "LoraLoaderModelOnly", ("CFGGuider",))
    text_encoder = _unique_input(
        nodes, "LTXAVTextEncoderLoader", "text_encoder", warnings
    )
    if text_encoder is None:
        text_encoder = _unique_input(
            nodes, "LTXAVTextEncoderLoader", "ckpt_name", warnings
        )
    latent_upscaler = _unique_input(
        nodes, "LatentUpscaleModelLoader", "model_name", warnings
    )
    enhance_lora_node = _enhance_lora_node(nodes)

    return replace(
        base,
        prompt_text=_string_or_none(prompt_text),
        prompt_enhance_enabled=enhance if isinstance(enhance, bool) else None,
        configured_width=_int_or_none(width),
        configured_height=_int_or_none(height),
        configured_duration=_float_or_none(duration),
        configured_fps=_float_or_none(fps),
        configured_frame_count=frame_count,
        main_seed=main_seed,
        secondary_seed=secondary_seed,
        checkpoint=_string_or_none(checkpoint),
        sampler=_string_or_none(sampler),
        cfg=_float_or_none(cfg),
        negative_prompt=_negative_prompt(nodes),
        distilled_lora=_node_input_string(distilled, "lora_name"),
        distilled_lora_strength=_node_input_float(distilled, "strength_model"),
        text_encoder=_string_or_none(text_encoder),
        latent_upscaler=_string_or_none(latent_upscaler),
        enhance_lora=_node_input_string(enhance_lora_node, "lora_name"),
        enhance_lora_model_strength=_node_input_float(
            enhance_lora_node, "strength_model"
        ),
        enhance_lora_clip_strength=_node_input_float(
            enhance_lora_node, "strength_clip"
        ),
        parse_warnings=tuple(warnings),
    )


def _metadata_dict(
    metadata: list[tuple[str, Any]], wanted: str
) -> dict[str, Any] | None:
    for key, value in metadata:
        if _normalize(key) == wanted and isinstance(value, dict):
            return value
    return None


def _is_ltx_prompt(prompt: dict[str, Any] | None) -> bool:
    if not prompt:
        return False
    classes = {
        _class(node).casefold()
        for node in prompt.values()
        if isinstance(node, dict)
    }
    model_names = " ".join(
        str(value)
        for node in prompt.values()
        if isinstance(node, dict)
        for value in _inputs(node).values()
        if isinstance(value, str)
    ).casefold()
    has_ltx_nodes = any(name.startswith("ltx") for name in classes)
    has_video = any("video" in name or "i2v" in name for name in classes)
    return has_ltx_nodes and has_video and ("2.3" in model_names or "ltx" in model_names)


def _is_ltx_workflow(workflow: dict[str, Any] | None) -> bool:
    if not workflow:
        return False
    name = (_workflow_name(workflow) or "").casefold()
    if "ltx-2.3" in name and "image to video" in name:
        return True
    text = str(workflow).casefold()
    return "ltx-2.3" in text and ("image to video" in text or "i2v" in text)


def _workflow_name(workflow: dict[str, Any] | None) -> str | None:
    if not workflow:
        return None
    definitions = workflow.get("definitions")
    if isinstance(definitions, dict):
        subgraphs = definitions.get("subgraphs")
        if isinstance(subgraphs, list):
            for graph in subgraphs:
                if not isinstance(graph, dict):
                    continue
                name = graph.get("name")
                if isinstance(name, str) and "ltx" in name.casefold():
                    return name
    for key in ("name", "workflow_name", "title"):
        value = workflow.get(key)
        if isinstance(value, str) and "ltx" in value.casefold():
            return value
    return None


def _primitive_by_title(
    nodes: dict[str, dict[str, Any]], title_part: str, kind: str
) -> Any:
    matches: list[Any] = []
    for node in nodes.values():
        title = _title(node).casefold()
        class_name = _class(node).casefold()
        if title_part not in title or f"primitive{kind}" not in class_name:
            continue
        value = _inputs(node).get("value")
        if value is not None:
            matches.append(value)
    return matches[0] if len(matches) == 1 else None


def _configured_frame_count(nodes: dict[str, dict[str, Any]]) -> int | None:
    for node in nodes.values():
        if "mathexpression" not in _class(node).casefold():
            continue
        inputs = _inputs(node)
        expression = str(inputs.get("expression", "")).replace(" ", "").casefold()
        if expression not in {"a*b+1", "b*a+1"}:
            continue
        a = _resolve_scalar(nodes, inputs.get("a", inputs.get("values.a")))
        b = _resolve_scalar(nodes, inputs.get("b", inputs.get("values.b")))
        if _number(a) and _number(b):
            return int(float(a) * float(b) + 1)
    for node in nodes.values():
        if "latentvideo" not in _class(node).casefold():
            continue
        value = _resolve_scalar(nodes, _inputs(node).get("length"))
        result = _int_or_none(value)
        if result is not None:
            return result
    return None


def _sampler_seeds(
    nodes: dict[str, dict[str, Any]], warnings: list[str]
) -> tuple[int | None, int | None]:
    samplers = [
        node_id
        for node_id, node in nodes.items()
        if _class(node).casefold() == "samplercustomadvanced"
    ]
    candidates: list[tuple[int, int, str]] = []
    for sampler_id in samplers:
        noise_ref = _inputs(nodes[sampler_id]).get("noise")
        noise_id = _ref_id(noise_ref, nodes)
        if noise_id is None:
            continue
        seed = _int_or_none(_inputs(nodes[noise_id]).get("noise_seed"))
        if seed is None:
            seed = _int_or_none(_resolve_scalar(nodes, noise_ref))
        if seed is None:
            continue
        sampler_ancestors = sum(
            1
            for other_id in samplers
            if other_id != sampler_id and _depends_on(nodes, sampler_id, other_id)
        )
        candidates.append((sampler_ancestors, seed, sampler_id))
    candidates.sort(key=lambda item: item[0])
    if len(candidates) > 2:
        warnings.append("サンプラーが3個以上あるため、先頭2段のSeedだけを表示します。")
    main = candidates[0][1] if candidates else None
    secondary = candidates[1][1] if len(candidates) > 1 else None
    return main, secondary


def _depends_on(
    nodes: dict[str, dict[str, Any]], node_id: str, target_id: str
) -> bool:
    pending = [node_id]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        for value in _inputs(nodes[current]).values():
            for reference in _references(value, nodes):
                if reference == target_id:
                    return True
                pending.append(reference)
    return False


def _connected_model_node(
    nodes: dict[str, dict[str, Any]],
    wanted_class: str,
    consumers: tuple[str, ...],
) -> dict[str, Any] | None:
    wanted_ids = {
        node_id
        for node_id, node in nodes.items()
        if _class(node).casefold() == wanted_class.casefold()
    }
    for node_id, node in nodes.items():
        if _class(node) not in consumers:
            continue
        if any(_depends_on(nodes, node_id, wanted_id) for wanted_id in wanted_ids):
            return next(
                nodes[wanted_id]
                for wanted_id in wanted_ids
                if _depends_on(nodes, node_id, wanted_id)
            )
    return nodes[next(iter(wanted_ids))] if len(wanted_ids) == 1 else None


def _connected_model_value(
    nodes: dict[str, dict[str, Any]],
    wanted_class: str,
    field: str,
    consumers: tuple[str, ...],
) -> Any:
    node = _connected_model_node(nodes, wanted_class, consumers)
    return _inputs(node).get(field) if node else None


def _enhance_lora_node(
    nodes: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    generators = [
        node_id
        for node_id, node in nodes.items()
        if _class(node).casefold() == "textgenerateltx2prompt"
    ]
    loaders = {
        node_id
        for node_id, node in nodes.items()
        if _class(node).casefold() == "loraloader"
    }
    for generator in generators:
        for loader in loaders:
            if _depends_on(nodes, generator, loader):
                return nodes[loader]
    return nodes[next(iter(loaders))] if len(loaders) == 1 else None


def _negative_prompt(nodes: dict[str, dict[str, Any]]) -> str | None:
    for node in nodes.values():
        if "conditioning" not in _class(node).casefold():
            continue
        negative = _inputs(node).get("negative")
        node_id = _ref_id(negative, nodes)
        if node_id is None:
            continue
        source = nodes[node_id]
        text = _inputs(source).get("text")
        if isinstance(text, str):
            return text
    for node in nodes.values():
        if "negative" not in _title(node).casefold():
            continue
        text = _inputs(node).get("text")
        if isinstance(text, str):
            return text
    return None


def _unique_input(
    nodes: dict[str, dict[str, Any]],
    class_name: str,
    field: str,
    warnings: list[str],
) -> Any:
    values = []
    for node in nodes.values():
        if _class(node).casefold() != class_name.casefold():
            continue
        value = _inputs(node).get(field)
        if value is not None and value not in values:
            values.append(value)
    if len(values) > 1:
        warnings.append(f"{class_name}の{field}が複数あり、特定できません。")
        return None
    return values[0] if values else None


def _resolve_scalar(
    nodes: dict[str, dict[str, Any]], value: Any, visited: set[str] | None = None
) -> Any:
    node_id = _ref_id(value, nodes)
    if node_id is None:
        return value
    seen = set() if visited is None else visited
    if node_id in seen:
        return None
    seen.add(node_id)
    inputs = _inputs(nodes[node_id])
    for key in ("value", "noise_seed", "width", "height", "length", "fps"):
        if key in inputs:
            return _resolve_scalar(nodes, inputs[key], seen)
    return None


def _references(value: Any, nodes: dict[str, dict[str, Any]]) -> list[str]:
    reference = _ref_id(value, nodes)
    if reference is not None:
        return [reference]
    results: list[str] = []
    if isinstance(value, dict):
        for child in value.values():
            results.extend(_references(child, nodes))
    elif isinstance(value, (list, tuple)):
        for child in value:
            results.extend(_references(child, nodes))
    return results


def _ref_id(value: Any, nodes: dict[str, dict[str, Any]]) -> str | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        candidate = str(value[0])
        if candidate in nodes and isinstance(value[1], int):
            return candidate
    return None


def _read_workflow_fallback(
    workflow: dict[str, Any] | None, base: LtxDisplayData
) -> LtxDisplayData:
    if not workflow:
        return base
    nodes: list[dict[str, Any]] = []
    definitions = workflow.get("definitions")
    if isinstance(definitions, dict):
        for graph in definitions.get("subgraphs", []):
            if isinstance(graph, dict) and isinstance(graph.get("nodes"), list):
                nodes.extend(node for node in graph["nodes"] if isinstance(node, dict))

    def widget(title: str) -> Any:
        found = [
            node.get("widgets_values", [None])[0]
            for node in nodes
            if str(node.get("title", "")).strip().casefold() == title.casefold()
            and isinstance(node.get("widgets_values"), list)
            and node["widgets_values"]
        ]
        return found[0] if len(found) == 1 else None

    def class_widgets(class_name: str) -> list[list[Any]]:
        return [
            node["widgets_values"]
            for node in nodes
            if str(node.get("type", "")).casefold() == class_name.casefold()
            and isinstance(node.get("widgets_values"), list)
            and node["widgets_values"]
        ]

    width = _int_or_none(widget("Width"))
    height = _int_or_none(widget("Height"))
    duration = _float_or_none(widget("Duration"))
    fps = _float_or_none(widget("Frame Rate"))
    length_expression = widget("Math Expression (length)")
    frame_count = (
        int(duration * fps + 1)
        if duration is not None
        and fps is not None
        and str(length_expression).replace(" ", "").casefold()
        in {"a*b+1", "b*a+1"}
        else None
    )
    random_noise = class_widgets("RandomNoise")
    main_seed = next(
        (
            _int_or_none(values[0])
            for values in random_noise
            if len(values) > 1 and str(values[1]).casefold() == "randomize"
        ),
        None,
    )
    secondary_seed = next(
        (
            _int_or_none(values[0])
            for values in random_noise
            if len(values) > 1 and str(values[1]).casefold() == "fixed"
        ),
        None,
    )
    distilled = class_widgets("LoraLoaderModelOnly")
    enhance_lora = class_widgets("LoraLoader")
    negative_candidates = [
        values[0]
        for values in class_widgets("CLIPTextEncode")
        if isinstance(values[0], str) and values[0].strip()
    ]

    return replace(
        base,
        prompt_text=_string_or_none(widget("Prompt")),
        prompt_enhance_enabled=(
            widget("Boolean (Enable Prompt Enhance)")
            if isinstance(widget("Boolean (Enable Prompt Enhance)"), bool)
            else None
        ),
        configured_width=width,
        configured_height=height,
        configured_duration=duration,
        configured_fps=fps,
        configured_frame_count=frame_count,
        main_seed=main_seed,
        secondary_seed=secondary_seed,
        checkpoint=_first_widget(class_widgets("CheckpointLoaderSimple"), 0),
        sampler=_first_widget(class_widgets("KSamplerSelect"), 0),
        cfg=_float_or_none(_first_widget(class_widgets("CFGGuider"), 0)),
        negative_prompt=(
            negative_candidates[0] if len(negative_candidates) == 1 else None
        ),
        distilled_lora=_first_widget(distilled, 0),
        distilled_lora_strength=_float_or_none(_first_widget(distilled, 1)),
        text_encoder=_first_widget(class_widgets("LTXAVTextEncoderLoader"), 0),
        latent_upscaler=_first_widget(
            class_widgets("LatentUpscaleModelLoader"), 0
        ),
        enhance_lora=_first_widget(enhance_lora, 0),
        enhance_lora_model_strength=_float_or_none(
            _first_widget(enhance_lora, 1)
        ),
        enhance_lora_clip_strength=_float_or_none(
            _first_widget(enhance_lora, 2)
        ),
    )


def _class(node: dict[str, Any]) -> str:
    return str(node.get("class_type", node.get("type", "")))


def _title(node: dict[str, Any]) -> str:
    meta = node.get("_meta")
    if isinstance(meta, dict):
        return str(meta.get("title", ""))
    return str(node.get("title", ""))


def _inputs(node: dict[str, Any] | None) -> dict[str, Any]:
    value = node.get("inputs") if isinstance(node, dict) else None
    return value if isinstance(value, dict) else {}


def _node_input_string(node: dict[str, Any] | None, field: str) -> str | None:
    return _string_or_none(_inputs(node).get(field))


def _node_input_float(node: dict[str, Any] | None, field: str) -> float | None:
    return _float_or_none(_inputs(node).get(field))


def _first_widget(values: list[list[Any]], index: int) -> Any:
    candidates = [
        widgets[index]
        for widgets in values
        if len(widgets) > index and widgets[index] is not None
    ]
    unique = []
    for value in candidates:
        if value not in unique:
            unique.append(value)
    return unique[0] if len(unique) == 1 else None


def _normalize(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _int_or_none(value: Any) -> int | None:
    return int(value) if _number(value) else None


def _float_or_none(value: Any) -> float | None:
    return float(value) if _number(value) else None


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
