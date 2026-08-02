"""Ver1.0.10段階Cのフォルダ単位METAMIデータコピーを確認する。"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from unittest.mock import patch  # noqa: E402

from storage.database import MetadataDatabase  # noqa: E402
from storage.file_hash import calculate_file_hash  # noqa: E402
from storage.schema_version import METAMI_SCHEMA_VERSION  # noqa: E402
from storage.user_data_copy import (  # noqa: E402
    BatchCopySummary,
    FileCopyStatus,
    copy_user_data_batch,
)
from ui.reuse_candidates_dialog import (  # noqa: E402
    ReuseCandidatesDialog,
    UserDataCopyProgressDialog,
    UserDataCopyWorker,
)
from storage.user_data_candidate_detection import detect_reuse_candidates  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


class UserDataCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = MetadataDatabase(self.root / "metami.db")
        self.database.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _register(self, name: str, content: bytes = b"same") -> tuple[Path, int]:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self.database.register_files([path])
        self.database.save_file_hash_result(calculate_file_hash(path))
        with closing(sqlite3.connect(self.database.path)) as connection:
            file_id = int(connection.execute(
                "SELECT id FROM files WHERE canonical_path = ?", (str(path.resolve()),)
            ).fetchone()[0])
        return path, file_id

    def test_copies_all_four_fields_atomically_and_keeps_source_hash_and_paths(self) -> None:
        source, _source_id = self._register("source.png")
        target, target_id = self._register("folder/target.png")
        self.database.set_user_details(source, "My Title", "line 1\n  line 2")
        self.database.set_rating(source, 3)
        self.database.add_tag(source, "人物")
        self.database.add_tag(source, "NightScene")
        before_source_hash = self.database.get_file_hash(source)
        before_target_hash = self.database.get_file_hash(target)

        summary = copy_user_data_batch(
            self.database, [target_id], allowed_paths=[target]
        )

        self.assertEqual(summary.copied_count, 1)
        self.assertEqual(summary.results[0].status, FileCopyStatus.COPIED)
        copied = self.database.get_file_user_data(target_id)
        self.assertEqual(copied.memo, "line 1\n  line 2")
        self.assertEqual(copied.rating, 3)
        self.assertEqual(copied.title, "My Title")
        self.assertEqual(set(copied.tags), {"人物", "NightScene"})
        self.assertEqual(self.database.get_file_hash(source), before_source_hash)
        self.assertEqual(self.database.get_file_hash(target), before_target_hash)
        self.assertEqual(METAMI_SCHEMA_VERSION, 2)

    def test_each_single_field_is_copyable(self) -> None:
        cases = (
            ("memo", lambda path: self.database.set_memo(path, "memo")),
            ("tag", lambda path: self.database.add_tag(path, "tag")),
            ("rating", lambda path: self.database.set_rating(path, 1)),
            ("title", lambda path: self.database.set_user_title(path, "Title")),
        )
        for index, (label, setter) in enumerate(cases):
            with self.subTest(label=label):
                content = f"same-{index}".encode()
                source, _ = self._register(f"{label}-source.png", content)
                target, target_id = self._register(f"{label}-target.png", content)
                setter(source)
                summary = copy_user_data_batch(self.database, [target_id], allowed_paths=[target])
                self.assertEqual(summary.copied_count, 1)
                self.assertTrue(self.database.get_file_user_data(target_id).has_data)

    def test_existing_target_data_is_never_overwritten(self) -> None:
        for index, setter in enumerate((
            lambda path: self.database.set_memo(path, "existing"),
            lambda path: self.database.add_tag(path, "existing"),
            lambda path: self.database.set_rating(path, 2),
            lambda path: self.database.set_user_title(path, "existing"),
        )):
            content = f"existing-{index}".encode()
            source, _ = self._register(f"source-{index}.png", content)
            target, target_id = self._register(f"target-{index}.png", content)
            self.database.set_memo(source, "source data")
            setter(target)
            before = self.database.get_file_user_data(target_id)
            summary = copy_user_data_batch(self.database, [target_id], allowed_paths=[target])
            self.assertEqual(summary.copied_count, 0)
            self.assertEqual(summary.results[0].status, FileCopyStatus.SKIPPED_TARGET_HAS_DATA)
            self.assertEqual(self.database.get_file_user_data(target_id), before)

    def test_equivalent_sources_copy_but_conflicting_sources_are_skipped(self) -> None:
        for label, second_memo, expected in (
            ("equal", "same memo", FileCopyStatus.COPIED),
            ("conflict", "different", FileCopyStatus.SKIPPED_SOURCE_CONFLICT),
        ):
            content = label.encode()
            source1, _ = self._register(f"{label}/one.png", content)
            source2, _ = self._register(f"{label}/two.png", content)
            target, target_id = self._register(f"{label}/target.png", content)
            self.database.set_memo(source1, "same memo")
            self.database.set_memo(source2, second_memo)
            summary = copy_user_data_batch(self.database, [target_id], allowed_paths=[target])
            self.assertEqual(summary.results[-1].status, expected)

    def test_changed_hash_and_other_folder_are_skipped_without_db_changes(self) -> None:
        source, _ = self._register("old.png", b"original")
        target, target_id = self._register("open/target.png", b"original")
        other, other_id = self._register("other/target.png", b"original")
        self.database.set_memo(source, "source")
        target.write_bytes(b"changed")

        summary = copy_user_data_batch(
            self.database, [target_id, other_id], allowed_paths=[target]
        )

        statuses = {item.target_file_id: item.status for item in summary.results}
        self.assertEqual(statuses[target_id], FileCopyStatus.SKIPPED_HASH_MISMATCH)
        self.assertEqual(statuses[other_id], FileCopyStatus.SKIPPED_NOT_IN_CURRENT_FOLDER)
        self.assertFalse(self.database.get_file_user_data(target_id).has_data)
        self.assertFalse(self.database.get_file_user_data(other_id).has_data)

    def test_tag_write_failure_rolls_back_user_info_and_other_targets_continue(self) -> None:
        source, _ = self._register("rollback/source.png", b"rollback")
        failed_target, failed_id = self._register("rollback/failed.png", b"rollback")
        good_target, good_id = self._register("rollback/good.png", b"rollback")
        self.database.set_memo(source, "memo")
        self.database.add_tag(source, "tag")
        with closing(sqlite3.connect(self.database.path)) as connection:
            connection.execute(f"""
                CREATE TRIGGER fail_copy_tag BEFORE INSERT ON file_tags
                WHEN NEW.file_id = {failed_id}
                BEGIN SELECT RAISE(ABORT, 'forced tag failure'); END
            """)

        summary = copy_user_data_batch(
            self.database, [failed_id, good_id],
            allowed_paths=[failed_target, good_target],
        )

        statuses = {item.target_file_id: item.status for item in summary.results}
        self.assertEqual(statuses[failed_id], FileCopyStatus.FAILED_DB_WRITE)
        self.assertEqual(statuses[good_id], FileCopyStatus.COPIED)
        self.assertFalse(self.database.get_file_user_data(failed_id).has_data)
        self.assertTrue(self.database.get_file_user_data(good_id).has_data)

    def test_cancel_keeps_completed_and_leaves_remaining_unchanged(self) -> None:
        source, _ = self._register("cancel/source.png", b"cancel")
        first, first_id = self._register("cancel/first.png", b"cancel")
        second, second_id = self._register("cancel/second.png", b"cancel")
        self.database.set_memo(source, "copied")
        cancel = threading.Event()

        def progress(done: int, _total: int, _path: Path) -> None:
            if done == 1:
                cancel.set()

        summary = copy_user_data_batch(
            self.database, [first_id, second_id], allowed_paths=[first, second],
            cancel_event=cancel, progress=progress,
        )
        self.assertEqual(summary.copied_count, 1)
        self.assertTrue(summary.cancelled)
        self.assertTrue(self.database.get_file_user_data(first_id).has_data)
        self.assertFalse(self.database.get_file_user_data(second_id).has_data)

    def test_candidate_dialog_has_one_batch_button_and_no_selection_controls(self) -> None:
        source, _ = self._register("ui/source.png", b"ui")
        target, target_id = self._register("ui/target.png", b"ui")
        self.database.set_memo(source, "memo")
        results = detect_reuse_candidates(self.database, [target_id])
        dialog = ReuseCandidatesDialog(results)
        try:
            self.assertTrue(dialog.copy_button.isEnabled())
            self.assertEqual(dialog.copy_button.text(), "コピー可能な1件へ一括コピー")
            self.assertNotIn("チェック", dialog.copy_button.text())
        finally:
            dialog.close()

    def test_worker_uses_database_path_reports_progress_and_can_cancel(self) -> None:
        source, _ = self._register("worker/source.png", b"worker")
        target, target_id = self._register("worker/target.png", b"worker")
        self.database.set_memo(source, "memo")
        summaries = []
        progress = []
        worker = UserDataCopyWorker(self.database.path, (target_id,), (target,))
        worker.progress.connect(lambda *values: progress.append(values))
        worker.finished.connect(summaries.append)
        worker.run()
        self.assertEqual(summaries[0].copied_count, 1)
        self.assertTrue(progress)

        progress_dialog = UserDataCopyProgressDialog(1)
        try:
            progress_dialog.update_progress(1, 1, str(target))
            self.assertEqual(progress_dialog.bar.value(), 1)
        finally:
            progress_dialog.deleteLater()

    def test_successful_copy_refreshes_current_folder_ui(self) -> None:
        settings = QSettings(
            str(self.root / "settings.ini"), QSettings.Format.IniFormat
        )
        window = MainWindow(self.database, settings=settings)
        window._current_folder = self.root
        summary = BatchCopySummary(1, 1, 0, 0, 0, 0, 0, False, ())
        try:
            with patch.object(window, "_reload_current_folder") as reload_folder:
                window._copy_finished(summary)
                reload_folder.assert_called_once_with()
            self.assertIsNotNone(window._copy_result_dialog)
            window._copy_result_dialog.close()
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
