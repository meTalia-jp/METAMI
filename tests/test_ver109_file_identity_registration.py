"""Ver1.0.9候補のファイル識別情報登録UIと実行基盤を確認する。"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QLabel, QPushButton, QRadioButton
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from storage.data_management import get_database_info
from storage.database import MetadataDatabase
from storage.file_hash import (
    BULK_REGISTRATION_STATUSES,
    CURRENT_HASH_ALGORITHM,
    HashStatus,
    is_bulk_registration_status,
)
from storage.schema_version import METAMI_SCHEMA_VERSION
from ui.file_identity_dialogs import (
    AUTO_IMAGES_ONLY,
    AUTO_OFF,
    AUTO_REGISTRATION_KEY,
    AutoRegistrationSettingsDialog,
    BulkRegistrationDialog,
    FileIdentityRegistrationWorker,
    IdentityStatusDialog,
    automatic_image_candidates,
    can_register_status,
    can_reregister_status,
    normalized_auto_registration_mode,
    requires_large_registration_confirmation,
)
from ui.main_window import MainWindow


class Ver109FileIdentityRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = MetadataDatabase(self.root / "data" / "metami.db")
        self.database.initialize()
        self.settings_path = self.root / "settings.ini"
        self.settings = QSettings(
            str(self.settings_path), QSettings.Format.IniFormat
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _file(self, name: str, content: bytes = b"sample") -> Path:
        path = self.root / "media" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _wait_for_identity_worker(self, window: MainWindow) -> None:
        deadline = time.monotonic() + 10
        while window._identity_thread is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertIsNone(window._identity_thread)

    @staticmethod
    def _close(window: MainWindow) -> None:
        if window._identity_worker is not None:
            window._identity_worker.request_cancel()
        if window._identity_thread is not None:
            window._identity_thread.quit()
            window._identity_thread.wait(3000)
        window.file_list_pane.release_media()
        window.preview_pane.release_media()
        window.close()

    def test_settings_default_validation_save_cancel_and_persistence(self) -> None:
        self.assertEqual(normalized_auto_registration_mode(None), AUTO_IMAGES_ONLY)
        self.assertEqual(normalized_auto_registration_mode("broken"), AUTO_IMAGES_ONLY)
        self.assertEqual(normalized_auto_registration_mode(AUTO_OFF), AUTO_OFF)
        first = MainWindow(self.database, settings=self.settings)
        try:
            self.assertEqual(first._identity_auto_mode, AUTO_IMAGES_ONLY)
            dialog = AutoRegistrationSettingsDialog(AUTO_IMAGES_ONLY)
            self.assertTrue(dialog.images_only.isChecked())
            self.assertFalse(dialog.off.isChecked())
            dialog.off.setChecked(True)
            self.assertEqual(dialog.selected_mode(), AUTO_OFF)
            dialog.reject()
            self.assertEqual(first._identity_auto_mode, AUTO_IMAGES_ONLY)
            first._save_identity_auto_mode(AUTO_OFF)
            self.assertEqual(
                self.settings.value(AUTO_REGISTRATION_KEY), AUTO_OFF
            )
        finally:
            self._close(first)
        second_settings = QSettings(
            str(self.settings_path), QSettings.Format.IniFormat
        )
        second = MainWindow(self.database, settings=second_settings)
        try:
            self.assertEqual(second._identity_auto_mode, AUTO_OFF)
        finally:
            self._close(second)

    def test_standard_indicators_show_checked_unchecked_and_disabled(self) -> None:
        settings_dialog = AutoRegistrationSettingsDialog(AUTO_IMAGES_ONLY)
        bulk = BulkRegistrationDialog(True)
        controls = (
            settings_dialog.findChildren(QRadioButton)
            + bulk.findChildren(QRadioButton)
            + bulk.findChildren(QCheckBox)
        )
        self.assertTrue(controls)
        self.assertTrue(all(control.style().objectName() == "fusion" for control in controls))
        self.assertTrue(settings_dialog.images_only.isChecked())
        self.assertFalse(settings_dialog.off.isChecked())
        self.assertTrue(bulk.include_subfolders.isChecked())
        from ui.styles import APP_STYLE
        self.assertNotIn("QRadioButton::indicator", APP_STYLE)
        self.assertNotIn("QCheckBox::indicator", APP_STYLE)

    def test_register_files_returns_only_first_registration(self) -> None:
        image = self._file("new.png")
        video = self._file("movie.mp4")
        first = self.database.register_files([image, video])
        self.assertEqual({path.name for path in first}, {"new.png", "movie.mp4"})
        self.assertEqual(self.database.register_files([image, video]), [])
        self.assertEqual(
            [path.name for path in automatic_image_candidates(first, AUTO_IMAGES_ONLY)],
            ["new.png"],
        )
        self.assertEqual(automatic_image_candidates(first, AUTO_OFF), [])

    def test_auto_registration_does_not_pick_existing_video_or_non_new_states(self) -> None:
        image = self._file("still.png")
        video = self._file("movie.mp4")
        self.database.register_files([image, video])
        window = MainWindow(self.database, settings=self.settings)
        captured: list[list[Path]] = []
        try:
            window._start_identity_registration = (  # type: ignore[method-assign]
                lambda paths, *_args, **_kwargs: captured.append(list(paths))
            )
            window._set_paths([image, video])
            self.assertEqual(captured, [])
            fresh = self._file("fresh.webp")
            window._set_paths([fresh])
            self.assertEqual([[path.name for path in group] for group in captured], [["fresh.webp"]])
            stored = self.database.get_file_hash(fresh)
            self.assertEqual(stored.status, HashStatus.NOT_CALCULATED)
        finally:
            self._close(window)

    def test_new_database_open_reload_subfolder_and_off_routes(self) -> None:
        folder = self.root / "fresh-folder"
        folder.mkdir()
        image = folder / "first.png"
        image.write_bytes(b"first")
        video = folder / "movie.mp4"
        video.write_bytes(b"video")
        window = MainWindow(self.database, settings=self.settings)
        try:
            with patch(
                "ui.file_identity_dialogs.calculate_file_hash",
                wraps=__import__("storage.file_hash", fromlist=["calculate_file_hash"]).calculate_file_hash,
            ) as calculate:
                self.assertTrue(window._open_folder(folder))
                self.assertIsNotNone(window._identity_thread)
                self._wait_for_identity_worker(window)
                self.assertGreaterEqual(calculate.call_count, 1)
            self.assertEqual(
                self.database.get_file_hash(image).status, HashStatus.CALCULATED
            )
            self.assertEqual(
                self.database.get_file_hash(video).status,
                HashStatus.NOT_CALCULATED,
            )
            added = folder / "added.webp"
            added.write_bytes(b"added")
            window._reload_current_folder()
            self._wait_for_identity_worker(window)
            self.assertEqual(
                self.database.get_file_hash(added).status, HashStatus.CALCULATED
            )
        finally:
            self._close(window)

        sub_root = self.root / "sub-root"
        child = sub_root / "child"
        child.mkdir(parents=True)
        nested = child / "nested.jpg"
        nested.write_bytes(b"nested")
        second_db = MetadataDatabase(self.root / "second.db")
        second_db.initialize()
        second_settings = QSettings(
            str(self.root / "second.ini"), QSettings.Format.IniFormat
        )
        second_settings.setValue("folders/includeSubfolders", True)
        second = MainWindow(second_db, settings=second_settings)
        try:
            self.assertTrue(second._open_folder(sub_root))
            self._wait_for_identity_worker(second)
            self.assertEqual(
                second_db.get_file_hash(nested).status, HashStatus.CALCULATED
            )
        finally:
            self._close(second)

        off_db = MetadataDatabase(self.root / "off.db")
        off_db.initialize()
        off_settings = QSettings(
            str(self.root / "off.ini"), QSettings.Format.IniFormat
        )
        off_settings.setValue(AUTO_REGISTRATION_KEY, AUTO_OFF)
        off_window = MainWindow(off_db, settings=off_settings)
        try:
            self.assertTrue(off_window._open_folder(folder))
            self.assertIsNone(off_window._identity_thread)
            self.assertEqual(
                off_db.get_file_hash(image).status, HashStatus.NOT_CALCULATED
            )
        finally:
            self._close(off_window)

    def test_confirmation_boundary(self) -> None:
        for count in (0, 1, 499):
            self.assertFalse(requires_large_registration_confirmation(count))
        for count in (500, 501, 1000):
            self.assertTrue(requires_large_registration_confirmation(count))

    def test_manual_action_status_rules(self) -> None:
        self.assertTrue(can_register_status(HashStatus.NOT_CALCULATED))
        self.assertTrue(can_register_status(HashStatus.FAILED))
        self.assertFalse(can_register_status(HashStatus.CALCULATED))
        self.assertFalse(can_register_status(HashStatus.STALE))
        self.assertFalse(can_register_status(HashStatus.MISSING))
        self.assertTrue(can_reregister_status(HashStatus.CALCULATED))
        self.assertTrue(can_reregister_status(HashStatus.STALE))
        self.assertTrue(can_reregister_status(HashStatus.FAILED))
        self.assertFalse(can_reregister_status(HashStatus.NOT_CALCULATED))
        self.assertFalse(can_reregister_status(HashStatus.MISSING))

    def test_bulk_dialog_options_are_per_operation(self) -> None:
        dialog = BulkRegistrationDialog(True)
        self.assertEqual(dialog.windowTitle(), "ファイル識別情報をまとめて登録")
        self.assertEqual(dialog.options().kinds, "images")
        self.assertTrue(dialog.options().include_subfolders)
        texts = [label.text() for label in dialog.findChildren(QLabel)]
        self.assertTrue(any("現在開いているフォルダ" in text for text in texts))
        self.assertTrue(any("ファイルが見つからない項目は対象になりません" in text for text in texts))
        radios = [button.text() for button in dialog.findChildren(QRadioButton)]
        self.assertEqual(radios, ["画像", "動画", "画像と動画"])
        checks = [button.text() for button in dialog.findChildren(QCheckBox)]
        self.assertEqual(checks, ["サブフォルダを含む"])
        button_texts = [button.text() for button in dialog.findChildren(QPushButton)]
        self.assertIn("対象を確認して次へ", button_texts)
        self.assertIn("キャンセル", button_texts)
        dialog.videos.setChecked(True)
        dialog.include_subfolders.setChecked(False)
        options = dialog.options()
        self.assertEqual(options.kinds, "videos")
        self.assertFalse(options.include_subfolders)
        dialog.both.setChecked(True)
        self.assertEqual(dialog.options().kinds, "both")

    def test_bulk_registration_statuses_are_centralized_and_safe(self) -> None:
        self.assertEqual(
            BULK_REGISTRATION_STATUSES,
            frozenset({
                HashStatus.NOT_CALCULATED,
                HashStatus.STALE,
                HashStatus.FAILED,
            }),
        )
        for status in BULK_REGISTRATION_STATUSES:
            self.assertTrue(is_bulk_registration_status(status))
        self.assertFalse(is_bulk_registration_status(HashStatus.CALCULATED))
        self.assertFalse(is_bulk_registration_status(HashStatus.MISSING))
        self.assertFalse(is_bulk_registration_status(None))

    def test_worker_uses_database_path_saves_and_continues_failures(self) -> None:
        image = self._file("good.png", b"good")
        missing = self.root / "media" / "missing.png"
        self.database.register_files([image])
        missing.parent.mkdir(parents=True, exist_ok=True)
        missing.write_bytes(b"will disappear")
        self.database.register_files([missing])
        missing.unlink()
        summaries = []
        progress = []
        worker = FileIdentityRegistrationWorker(
            self.database.path, [image, missing]
        )
        worker.progress.connect(progress.append)
        worker.finished.connect(summaries.append)
        worker.run()
        self.assertEqual(len(progress), 2)
        self.assertEqual(summaries[0].succeeded, 1)
        self.assertEqual(summaries[0].failed, 1)
        self.assertEqual(
            self.database.get_file_hash(image).status, HashStatus.CALCULATED
        )
        self.assertEqual(
            self.database.get_file_hash(missing).status, HashStatus.MISSING
        )
        self.assertEqual(
            self.database.get_file_hash(image).algorithm,
            CURRENT_HASH_ALGORITHM,
        )

    def test_cancel_keeps_finished_and_leaves_unprocessed(self) -> None:
        paths = [self._file(f"image_{index}.png", bytes([index])) for index in range(3)]
        self.database.register_files(paths)
        summaries = []
        worker = FileIdentityRegistrationWorker(self.database.path, paths)
        worker.progress.connect(lambda _progress: worker.request_cancel())
        worker.finished.connect(summaries.append)
        worker.run()
        summary = summaries[0]
        self.assertTrue(summary.cancelled)
        self.assertEqual(summary.succeeded, 1)
        self.assertEqual(summary.unprocessed, 2)
        self.assertEqual(
            self.database.get_file_hash(paths[0]).status, HashStatus.CALCULATED
        )
        self.assertEqual(
            self.database.get_file_hash(paths[1]).status,
            HashStatus.NOT_CALCULATED,
        )

    def test_menu_terms_status_dialog_and_schema_are_stable(self) -> None:
        window = MainWindow(self.database, settings=self.settings)
        try:
            actions = [action.text() for action in window.identity_menu.actions()]
            self.assertEqual(
                actions,
                [
                    "登録の設定…", "", "選択中のファイルを登録",
                    "選択中のファイルを再登録", "フォルダ内の未登録項目を登録…",
                    "登録状況…",
                ],
            )
            self.assertFalse(any("ハッシュ計算" in text for text in actions))
            info = get_database_info(self.database.path)
            processing = [True]
            status = IdentityStatusDialog(
                info,
                info_provider=lambda: get_database_info(self.database.path),
                is_processing=lambda: processing[0],
            )
            self.assertEqual(status.windowTitle(), "ファイル識別情報の登録状況")
            labels = [label.text() for label in status.findChildren(type(status.processing_label))]
            self.assertTrue(any("未登録とは" in text for text in labels))
            self.assertEqual(
                status.processing_label.text(),
                "ファイル識別情報を登録しています…",
            )
            processing[0] = False
            status.refresh()
            self.assertEqual(status.processing_label.text(), "")
            self.assertEqual(METAMI_SCHEMA_VERSION, 2)
        finally:
            self._close(window)

    def test_help_describes_operation_and_safety(self) -> None:
        text = (SRC / "assets" / "help" / "data_management.md").read_text(
            encoding="utf-8"
        )
        for expected in (
            "新しくMETAMIへ登録された画像",
            "更新時の大量自動処理を避けるため",
            "動画も自動登録されません",
            "500件以上",
            "今回は登録しない",
            "途中でキャンセル",
            "完了済み",
            "ファイル本体は変更されません",
            "再関連付けや自動パス更新はまだ実装されていません",
        ):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
