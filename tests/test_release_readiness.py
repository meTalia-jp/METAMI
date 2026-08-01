"""Ver1.0.0候補 GitHub公開準備の静的・起動前検査。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from storage.database import MetadataDatabase  # noqa: E402
from ui.decorations import CharacterHeader  # noqa: E402


class ReleaseReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_runtime_requirements_are_minimal_and_importable(self) -> None:
        requirements = [
            line.strip()
            for line in (ROOT / "requirements.txt").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(requirements, ["PySide6==6.11.1"])
        import PySide6

        self.assertEqual(PySide6.__version__, "6.11.1")

    def test_readme_contains_reproducible_windows_setup(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        required_text = (
            "M.E.T.A.M.I.",
            "Metadata Exploration & Tag Analysis with Media Insight",
            "Python 3.12",
            "py -3.12 -m venv .venv",
            "python -m pip install -r requirements.txt",
            "python src/main.py",
            "data/metami.db",
            "原本",
            "missing",
            "Workflow",
            "JSON",
            "ユーザータイトル",
            "ライセンス",
            "Ver1.0.9",
            "Media Explorer for Tags, Assets, Metadata, and Insights",
            "Copyright (c) 2026 meTalia-jp",
            "英語UI",
        )
        for text in required_text:
            with self.subTest(text=text):
                self.assertIn(text, readme)

    def test_mit_license_and_asset_exclusion_are_consistent(self) -> None:
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
        assets = (ROOT / "src" / "assets" / "README.md").read_text(
            encoding="utf-8"
        )
        expected = "Copyright (c) 2026 meTalia-jp"
        self.assertIn("MIT License", license_text)
        self.assertIn(expected, license_text)
        self.assertIn(expected, notice)
        self.assertIn("MIT License対象外", notice)
        self.assertIn("MIT License対象", assets)
        self.assertIn("非商用のファンアート", assets)

    def test_public_readme_images_exist(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for relative in (
            "docs/images/metami-main.png",
            "docs/images/project-message-ja.png",
            "docs/images/project-message-en.png",
        ):
            with self.subTest(relative=relative):
                self.assertIn(relative, readme)
                self.assertTrue((ROOT / relative).is_file())

    def test_public_roadmap_has_no_internal_codex_instruction(self) -> None:
        roadmap = (ROOT / "docs" / "04_ロードマップ.md").read_text(
            encoding="utf-8"
        )
        old_instruction = (
            ROOT / "docs" / "Ver0.8.9_ロードマップ_Codex指示.md"
        )
        self.assertFalse(old_instruction.exists())
        self.assertIn("Ver0.8.9　統合確認と安定化（完了）", roadmap)
        self.assertIn("Ver1.0.0　初回リリース候補（公開前確認中）", roadmap)
        self.assertIn("将来候補", roadmap)
        for internal_phrase in (
            "Codex実装指示",
            "Codexへの実装指示",
            "コミット指示",
        ):
            self.assertNotIn(internal_phrase, roadmap)

    def test_batch_launcher_uses_project_relative_venv(self) -> None:
        launcher_path = ROOT / "run_metami.bat"
        launcher_bytes = launcher_path.read_bytes()
        self.assertFalse(launcher_bytes.startswith(b"\xef\xbb\xbf"))
        launcher = launcher_bytes.decode("utf-8")
        self.assertIn("%~dp0.venv\\Scripts\\python.exe", launcher)
        self.assertIn('"%~dp0src\\main.py"', launcher)
        self.assertIn("README.md", launcher)
        self.assertIn("qt.multimedia.ffmpeg.info=false", launcher)
        self.assertNotIn("Activate.ps1", launcher)
        self.assertNotIn("PowerShell", launcher)
        self.assertNotRegex(launcher, r"\b[A-Za-z]:[\\/]")

    def test_runtime_and_private_paths_are_ignored(self) -> None:
        for relative in (
            ".venv/Scripts/python.exe",
            "data/metami.db",
            "data/metami.db-wal",
            "sample/private.png",
            "bkup/backup.py",
            "output/result.png",
            ".idea/workspace.xml",
            ".vscode/settings.json",
            "debug.log",
            ".pytest_cache/state",
            "docs/mockups/GUIモック_ノート帳カード型_v1.png",
            "docs/mockups/モックアップ 2026年7月19日 16_23_36.png",
            "docs/mockups/モックアップ 2026年7月20日 .png",
            "docs/mockups/モックアップ 2026年7月21日 .png",
        ):
            with self.subTest(path=relative):
                result = subprocess.run(
                    ["git", "check-ignore", "--quiet", relative],
                    cwd=ROOT,
                    check=False,
                )
                self.assertEqual(result.returncode, 0)

        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.splitlines()
        self.assertIn("data/.gitkeep", tracked)
        self.assertFalse(
            any(
                path.startswith(("sample/", "bkup/", ".venv/"))
                or (path.startswith("docs/mockups/") and path.endswith(".png"))
                or path.endswith((".db", ".sqlite", ".sqlite3", ".log"))
                for path in tracked
            )
        )

    def test_source_and_public_text_have_no_fixed_local_absolute_path(self) -> None:
        drive_path = re.compile(r"\b[A-Za-z]:[\\/]")
        secret_token = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
        skipped_parts = {
            ".git",
            ".venv",
            "venv",
            "env",
            "sample",
            "bkup",
            "__pycache__",
        }
        text_suffixes = {".py", ".md", ".txt", ".bat", ".toml", ".yaml", ".yml"}

        problems: list[str] = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in text_suffixes:
                continue
            if any(part in skipped_parts for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if path == ROOT / "README.md":
                # 公開セットアップ例の推奨インストール先だけを許可する。
                text = text.replace("C:" + "\\METAMI", "")
            if drive_path.search(text) or secret_token.search(text):
                problems.append(str(path.relative_to(ROOT)))
        self.assertEqual(problems, [])

    def test_new_database_is_created_without_sample_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "data" / "metami.db"
            database = MetadataDatabase(database_path)
            database.initialize()
            self.assertTrue(database_path.is_file())
            self.assertEqual(database.get_schema_version(), 3)

    def test_missing_character_asset_uses_text_fallback(self) -> None:
        header = CharacterHeader(
            "代替表示",
            "素材欠損確認",
            "__missing_character__.png",
            "left",
        )
        try:
            avatar = header.findChild(QLabel, "characterAvatar")
            self.assertIsNotNone(avatar)
            assert avatar is not None
            self.assertEqual(avatar.text(), "代")
            self.assertIn("画像素材なし", avatar.toolTip())
        finally:
            header.close()


if __name__ == "__main__":
    unittest.main()
