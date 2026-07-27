"""LTX 2.3専用メタデータ解析と表示の回帰テスト。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication, QLabel

from metadata.ltx_reader import read_ltx_info
from metadata.mp4_reader import read_mp4_info
from models.display_data import LtxDisplayData
from ui.metadata_pane import LtxTab


def _prompt(text: str, enhance: bool) -> dict:
    nodes = {
        "source": ("PrimitiveStringMultiline", {"value": text}, "Prompt"),
        "enhance": ("PrimitiveBoolean", {"value": enhance}, "Enable Prompt Enhance"),
        "width": ("PrimitiveInt", {"value": 1280}, "Width"),
        "height": ("PrimitiveInt", {"value": 720}, "Height"),
        "duration": ("PrimitiveInt", {"value": 3}, "Duration"),
        "fps": ("PrimitiveInt", {"value": 25}, "Frame Rate"),
        "length": (
            "ComfyMathExpression",
            {
                "expression": "a * b + 1",
                "values.a": ["duration", 0],
                "values.b": ["fps", 0],
            },
            "",
        ),
        "checkpoint": (
            "CheckpointLoaderSimple",
            {"ckpt_name": "ltx-2.3-dev.safetensors"},
            "",
        ),
        "distilled": (
            "LoraLoaderModelOnly",
            {
                "model": ["checkpoint", 0],
                "lora_name": "distilled.safetensors",
                "strength_model": 0.5,
            },
            "",
        ),
        "noise1": ("RandomNoise", {"noise_seed": 341013851809238}, ""),
        "sampler1": ("SamplerCustomAdvanced", {"noise": ["noise1", 0]}, ""),
        "noise2": ("RandomNoise", {"noise_seed": 42}, ""),
        "sampler2": (
            "SamplerCustomAdvanced",
            {"noise": ["noise2", 0], "latent_image": ["sampler1", 0]},
            "",
        ),
        "sampler_name": ("KSamplerSelect", {"sampler_name": "euler"}, ""),
        "cfg": ("CFGGuider", {"model": ["distilled", 0], "cfg": 1.0}, ""),
        "text_encoder": (
            "LTXAVTextEncoderLoader",
            {"text_encoder": "gemma.safetensors"},
            "",
        ),
        "upscaler": (
            "LatentUpscaleModelLoader",
            {"model_name": "upscaler.safetensors"},
            "",
        ),
        "enhance_lora": (
            "LoraLoader",
            {
                "lora_name": "enhance.safetensors",
                "strength_model": 0.8,
                "strength_clip": 0.9,
            },
            "",
        ),
        "enhance_text": (
            "TextGenerateLTX2Prompt",
            {"clip": ["enhance_lora", 1], "prompt": ["source", 0]},
            "",
        ),
        "negative": (
            "CLIPTextEncode",
            {"text": "bad quality"},
            "Negative Prompt",
        ),
        "conditioning": (
            "LTXVConditioning",
            {"negative": ["negative", 0]},
            "",
        ),
        "latent": ("EmptyLTXVLatentVideo", {"length": ["length", 1]}, ""),
    }
    return {
        node_id: {
            "class_type": class_type,
            "inputs": inputs,
            "_meta": {"title": title},
        }
        for node_id, (class_type, inputs, title) in nodes.items()
    }


class LtxMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_four_language_and_enhance_patterns_keep_source_prompt(self) -> None:
        patterns = (
            ("女の子はキーボードをたたく", False),
            ("女の子はキーボードをたたく", True),
            ("The girl types on the keyboard.", False),
            ("The girl types on the keyboard.", True),
        )
        for text, enabled in patterns:
            with self.subTest(text=text, enabled=enabled):
                info = read_ltx_info([("prompt", _prompt(text, enabled))])
                self.assertTrue(info.detected)
                self.assertEqual(info.prompt_text, text)
                self.assertIs(info.prompt_enhance_enabled, enabled)
                self.assertNotIn("enhanced_prompt", info.__dataclass_fields__)

    def test_settings_seed_and_models_follow_node_connections(self) -> None:
        info = read_ltx_info(
            [("prompt", _prompt("original", True))],
            actual_width=1280,
            actual_height=704,
            actual_duration=2.92,
            actual_fps=25,
            actual_frame_count=73,
        )
        self.assertEqual(
            (
                info.configured_width,
                info.configured_height,
                info.configured_duration,
                info.configured_fps,
                info.configured_frame_count,
            ),
            (1280, 720, 3.0, 25.0, 76),
        )
        self.assertEqual((info.main_seed, info.secondary_seed), (341013851809238, 42))
        self.assertEqual(info.checkpoint, "ltx-2.3-dev.safetensors")
        self.assertEqual(info.distilled_lora, "distilled.safetensors")
        self.assertEqual(info.enhance_lora, "enhance.safetensors")
        self.assertEqual(info.text_encoder, "gemma.safetensors")
        self.assertEqual(info.latent_upscaler, "upscaler.safetensors")
        self.assertEqual(info.negative_prompt, "bad quality")

    def test_missing_broken_non_ltx_and_partial_metadata_are_safe(self) -> None:
        workflow = {
            "definitions": {
                "subgraphs": [{"name": "Image to Video (LTX-2.3)", "nodes": []}]
            }
        }
        self.assertTrue(read_ltx_info([("workflow", workflow)]).detected)
        self.assertFalse(read_ltx_info([("prompt", "{broken")]).detected)
        self.assertFalse(
            read_ltx_info(
                [("prompt", {"1": {"class_type": "KSampler", "inputs": {}}})]
            ).detected
        )
        partial = _prompt("test", False)
        del partial["width"]
        self.assertIsNone(read_ltx_info([("prompt", partial)]).configured_width)

    def test_prompt_card_and_copy_buttons_use_exact_saved_text(self) -> None:
        tab = LtxTab()
        tab.set_data(
            LtxDisplayData(
                detected=True,
                prompt_text="日本語 prompt\nsecond line",
                negative_prompt="bad quality, blur",
                main_seed=341013851809238,
            )
        )
        labels = [
            label.text() for label in tab.prompt_card.findChildren(QLabel)
        ]
        self.assertIn("プロンプト情報", labels)
        self.assertIn("プロンプト", labels)
        self.assertIn("ネガティブプロンプト", labels)
        self.assertIn(
            "※サブグラフ内から抽出した情報です。"
            "生成への適用状態は判定していません。",
            labels,
        )
        tab._copy_prompt()
        QApplication.processEvents()
        self.assertEqual(QApplication.clipboard().text(), "日本語 prompt\nsecond line")
        tab._copy_negative_prompt()
        QApplication.processEvents()
        self.assertEqual(QApplication.clipboard().text(), "bad quality, blur")

    def test_prompt_card_uses_saved_information_placeholder(self) -> None:
        tab = LtxTab()
        tab.set_data(LtxDisplayData(detected=True))
        self.assertEqual(tab.prompt_label.text(), "保存情報なし")
        self.assertEqual(tab.negative_prompt_label.text(), "保存情報なし")
        self.assertFalse(tab.prompt_copy.isEnabled())
        self.assertFalse(tab.negative_prompt_copy.isEnabled())

    def test_local_official_samples_when_available(self) -> None:
        files = sorted((ROOT / "sample" / "ltx23_prompt_compare").glob("*0002[6-9]*.mp4"))
        if len(files) != 4:
            self.skipTest("ローカルのLTX比較サンプル4本がありません")
        expected = (
            ("女の子はパソコンを操作", False),
            ("女の子はパソコンを操作", True),
            ("The girl is using the computer", False),
            ("The girl is using the computer", True),
        )
        for path, (prompt_start, enabled) in zip(files, expected):
            mp4 = read_mp4_info(path)
            metadata = [
                (key, json.loads(value) if key in {"prompt", "workflow"} else value)
                for key, value in mp4.metadata
            ]
            info = read_ltx_info(
                metadata,
                actual_width=mp4.width,
                actual_height=mp4.height,
                actual_duration=mp4.duration_seconds,
                actual_fps=mp4.fps,
                actual_frame_count=mp4.frame_count,
            )
            self.assertTrue(info.prompt_text.startswith(prompt_start))
            self.assertIs(info.prompt_enhance_enabled, enabled)
            self.assertEqual(info.configured_frame_count, 121)
            self.assertEqual((info.actual_width, info.actual_height), (640, 320))
            self.assertEqual(info.actual_frame_count, 121)
            self.assertAlmostEqual(info.actual_fps, 24.0)


if __name__ == "__main__":
    unittest.main()
