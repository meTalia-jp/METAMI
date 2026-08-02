"""Ver1.0.10段階Bの候補検出・通知・参照専用一覧を確認する。"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from storage.database import MetadataDatabase  # noqa: E402
from storage.file_hash import HashStatus, calculate_file_hash  # noqa: E402
from storage.schema_version import METAMI_SCHEMA_VERSION  # noqa: E402
from storage.user_data_candidate_detection import (  # noqa: E402
    detect_reuse_candidates,
)
from storage.user_data_reuse import (  # noqa: E402
    CopyDecision,
    SourceDataStatus,
)
from ui.file_identity_dialogs import (  # noqa: E402
    FileIdentityRegistrationWorker,
)
from ui.main_window import MainWindow  # noqa: E402
from ui.reuse_candidates_dialog import (  # noqa: E402
    ReuseCandidateDetectionWorker,
    ReuseCandidatesDialog,
)


class ReuseDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = MetadataDatabase(self.root / "metami.db")
        self.database.initialize()
        self.settings = QSettings(
            str(self.root / "settings.ini"), QSettings.Format.IniFormat
        )

    def tearDown(self) -> None:
        self.settings.clear()
        self.settings.sync()
        self.temporary.cleanup()

    def _file(self, name: str, content: bytes = b"same") -> tuple[Path, int]:
        path = self.root / name
        path.write_bytes(content)
        self.database.register_files([path])
        with closing(sqlite3.connect(self.database.path)) as connection:
            file_id = int(connection.execute(
                "SELECT id FROM files WHERE canonical_path = ?", (str(path.resolve()),)
            ).fetchone()[0])
        return path, file_id

    def _calculate(self, path: Path) -> None:
        self.database.save_file_hash_result(calculate_file_hash(path))

    @staticmethod
    def _wait_until(predicate, timeout: float = 5.0) -> None:
        attempts = max(1, int(timeout / 0.01))
        completed_attempts = 0
        stable_matches = 0
        for _ in range(attempts):
            completed_attempts += 1
            if predicate():
                stable_matches += 1
                if stable_matches >= 3:
                    return
            else:
                stable_matches = 0
            QApplication.processEvents()
            time.sleep(0.01)
        raise AssertionError(
            f"非同期処理が時間内に完了しませんでした。({completed_attempts}回)"
        )

    def _source_target(self):
        source, source_id = self._file("old.png")
        target, target_id = self._file("new.png")
        self.database.set_user_details(source, "以前の題名", "以前のメモ")
        self.database.set_rating(source, 3)
        self.database.add_tag(source, "人物")
        self._calculate(source)
        self._calculate(target)
        return source, source_id, target, target_id

    def test_registration_summary_contains_only_successful_calculated_ids(self) -> None:
        good, good_id = self._file("good.png", b"good")
        missing, _missing_id = self._file("missing.png", b"missing")
        missing.unlink()
        summaries = []
        worker = FileIdentityRegistrationWorker(self.database.path, [good, missing])
        worker.finished.connect(summaries.append)
        worker.run()
        self.assertEqual(summaries[0].successful_file_ids, (good_id,))
        self.assertEqual(summaries[0].succeeded, 1)
        self.assertEqual(summaries[0].failed, 1)

    def test_cancel_keeps_completed_id_and_excludes_unprocessed(self) -> None:
        paths_and_ids = [self._file(f"cancel-{index}.png", bytes([index])) for index in range(3)]
        worker = FileIdentityRegistrationWorker(
            self.database.path, [item[0] for item in paths_and_ids]
        )
        summaries = []
        worker.progress.connect(lambda _progress: worker.request_cancel())
        worker.finished.connect(summaries.append)
        worker.run()
        self.assertEqual(summaries[0].successful_file_ids, (paths_and_ids[0][1],))
        self.assertEqual(summaries[0].unprocessed, 2)

    def test_detects_calculated_and_valid_missing_source_read_only(self) -> None:
        source, source_id, _target, target_id = self._source_target()
        before = self.database.path.read_bytes()
        result = detect_reuse_candidates(self.database, [target_id])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].assessment.decision, CopyDecision.COPYABLE)
        self.assertEqual(result[0].sources.candidates[0].record.file_id, source_id)
        self.assertEqual(result[0].available_items, ("メモ", "タグ", "星評価", "タイトル"))
        self.assertEqual(self.database.path.read_bytes(), before)

        source.unlink()
        self.database.check_registered_files()
        self.database.refresh_file_hash_status(source)
        missing_result = detect_reuse_candidates(self.database, [target_id])
        self.assertEqual(missing_result[0].assessment.decision, CopyDecision.COPYABLE)
        self.assertTrue(missing_result[0].sources.candidates[0].record.missing)

    def test_excludes_self_wrong_digest_status_and_source_without_data(self) -> None:
        target, target_id = self._file("target.png")
        no_data, _no_data_id = self._file("no-data.png")
        wrong, _wrong_id = self._file("wrong.png", b"different")
        stale, _stale_id = self._file("stale.png")
        for path in (target, no_data, wrong, stale):
            self._calculate(path)
        self.database.set_memo(stale, "stale data")
        stale.write_bytes(b"changed")
        self.database.refresh_file_hash_status(stale)
        self.assertEqual(detect_reuse_candidates(self.database, [target_id]), ())

    def test_equivalent_conflicting_and_target_existing_data_are_retained(self) -> None:
        first, _first_id, target, target_id = self._source_target()
        second, second_id = self._file("old-2.png")
        self.database.set_user_details(second, "以前の題名", " 以前のメモ ")
        self.database.set_rating(second, 3)
        self.database.add_tag(second, "人物")
        self._calculate(second)
        equivalent = detect_reuse_candidates(self.database, [target_id])[0]
        self.assertEqual(equivalent.sources.status, SourceDataStatus.EQUIVALENT_SOURCE_DATA)
        self.assertEqual(len(equivalent.sources.candidates), 2)
        self.assertIsNone(equivalent.sources.user_data)
        self.assertEqual(equivalent.assessment.decision, CopyDecision.COPYABLE)

        self.database.set_memo(second, "異なるメモ")
        conflict = detect_reuse_candidates(self.database, [target_id])[0]
        self.assertEqual(conflict.sources.status, SourceDataStatus.CONFLICTING_SOURCE_DATA)
        self.assertEqual(conflict.assessment.decision, CopyDecision.SOURCE_DATA_CONFLICT)
        self.assertIn(second_id, [item.record.file_id for item in conflict.sources.candidates])

        self.database.set_memo(target, "現在のメモ")
        target_data = detect_reuse_candidates(self.database, [target_id])[0]
        self.assertEqual(target_data.assessment.decision, CopyDecision.SOURCE_DATA_CONFLICT)
        # 競合を解消すると、target既存データが理由として表面化する。
        self.database.set_memo(second, " 以前のメモ ")
        target_data = detect_reuse_candidates(self.database, [target_id])[0]
        self.assertEqual(target_data.assessment.decision, CopyDecision.TARGET_HAS_USER_DATA)
        self.assertEqual(target_data.display_status, "現在のファイルにデータあり")
        self.assertEqual(first.read_bytes(), target.read_bytes())

    def test_detection_worker_uses_new_database_instance(self) -> None:
        _source, _source_id, _target, target_id = self._source_target()
        captured = []
        worker = ReuseCandidateDetectionWorker(self.database.path, (target_id,))
        worker.finished.connect(captured.append)
        worker.run()
        self.assertEqual(len(captured[0]), 1)
        self.assertEqual(captured[0][0].assessment.decision, CopyDecision.COPYABLE)

    def test_read_only_dialog_shows_all_sources_details_and_tooltips(self) -> None:
        _source, _source_id, _target, target_id = self._source_target()
        second, _second_id = self._file("old-second.png")
        self.database.set_memo(second, "以前のメモ")
        self._calculate(second)
        result = detect_reuse_candidates(self.database, [target_id])
        dialog = ReuseCandidatesDialog(result)
        try:
            self.assertEqual(dialog.windowTitle(), "以前のMETAMIデータ")
            self.assertEqual(dialog.table.columnCount(), 4)
            self.assertEqual(dialog.table.horizontalHeaderItem(0).text(), "現在のファイル")
            self.assertIn("件の登録場所", dialog.table.item(0, 1).text())
            self.assertIn("\n", dialog.table.item(0, 1).toolTip())
            dialog.table.selectRow(0)
            QApplication.processEvents()
            self.assertIn("以前の登録 1", dialog.details.toPlainText())
            self.assertIn("以前の登録 2", dialog.details.toPlainText())
            button_texts = [button.text() for button in dialog.findChildren(QPushButton)]
            self.assertEqual(
                button_texts, ["コピー可能な0件へ一括コピー", "閉じる"]
            )
            self.assertFalse(dialog.copy_button.isEnabled())
            self.assertEqual(
                dialog.table.editTriggers(),
                dialog.table.EditTrigger.NoEditTriggers,
            )
        finally:
            dialog.close()

    def test_session_notification_suppresses_same_key_and_hash_change_is_new(self) -> None:
        _source, _source_id, _target, target_id = self._source_target()
        result = detect_reuse_candidates(self.database, [target_id])[0]
        window = MainWindow(self.database, settings=self.settings)
        calls = []
        window._show_reuse_notification = lambda values, count: calls.append((values, count))  # type: ignore[method-assign]
        try:
            window._reuse_detection_finished((result,))
            window._reuse_detection_finished((result,))
            self.assertEqual(len(calls), 1)
            changed_target = replace(result.target, content_hash="b" * 64)
            changed = replace(result, target=changed_target)
            window._reuse_detection_finished((changed,))
            self.assertEqual(len(calls), 2)
        finally:
            window.close()

    def test_detection_starts_only_after_identity_thread_finished(self) -> None:
        window = MainWindow(self.database, settings=self.settings)
        try:
            window._identity_successful_file_ids = (123, 456)
            with patch.object(window, "_start_reuse_detection") as start:
                window._identity_thread_finished()
                QApplication.processEvents()
                start.assert_called_once_with((123, 456))
        finally:
            window.close()

    def test_notification_confirm_and_ignore_actions(self) -> None:
        _source, _source_id, _target, target_id = self._source_target()
        results = detect_reuse_candidates(self.database, [target_id])
        window = MainWindow(self.database, settings=self.settings)
        try:
            window._show_reuse_notification(results, 1)
            notice = window._reuse_notification
            self.assertIsNotNone(notice)
            buttons = {button.text(): button for button in notice.buttons()}
            self.assertEqual(set(buttons), {"確認する", "今回は何もしない"})
            buttons["今回は何もしない"].click()
            QApplication.processEvents()
            self.assertIsNone(window._reuse_candidates_dialog)

            window._show_reuse_notification(results, 1)
            notice = window._reuse_notification
            buttons = {button.text(): button for button in notice.buttons()}
            buttons["確認する"].click()
            QApplication.processEvents()
            self.assertIsNotNone(window._reuse_candidates_dialog)
            window._reuse_candidates_dialog.close()
        finally:
            window.close()

    def test_no_available_result_does_not_notify(self) -> None:
        _source, _source_id, target, target_id = self._source_target()
        self.database.set_memo(target, "current")
        result = detect_reuse_candidates(self.database, [target_id])[0]
        window = MainWindow(self.database, settings=self.settings)
        with patch.object(window, "_show_reuse_notification") as show:
            window._reuse_detection_finished((result,))
            show.assert_not_called()
        window.close()
        self.assertEqual(METAMI_SCHEMA_VERSION, 2)

    def test_real_auto_registration_copy_route_notifies_memo_and_rating_source(self) -> None:
        source_folder = self.root / "source-folder"
        target_folder = self.root / "target-folder"
        source_folder.mkdir()
        target_folder.mkdir()
        source = source_folder / "original.png"
        source.write_bytes(b"identical-image-content")
        target = target_folder / "copied.png"
        window = MainWindow(self.database, settings=self.settings)
        notifications = []
        window._show_reuse_notification = (  # type: ignore[method-assign]
            lambda values, count: notifications.append((values, count))
        )
        try:
            self.assertTrue(window._open_folder(source_folder))
            self._wait_until(
                lambda: window._identity_thread is None
                and window._reuse_detection_thread is None
            )
            source_hash = self.database.get_file_hash(source)
            self.assertEqual(source_hash.status, HashStatus.CALCULATED)
            self.database.set_memo(source, "元ファイルのメモ")
            self.database.set_rating(source, 2)

            target.write_bytes(source.read_bytes())
            self.assertTrue(window._open_folder(target_folder))
            self._wait_until(
                lambda: window._identity_thread is None
                and window._reuse_detection_thread is None
            )
            target_hash = self.database.get_file_hash(target)
            self.assertEqual(target_hash.status, HashStatus.CALCULATED)
            self.assertEqual(target_hash.content_hash, source_hash.content_hash)
            self.assertEqual(len(notifications), 1)
            results, available_count = notifications[0]
            self.assertEqual(available_count, 1)
            self.assertEqual(results[0].assessment.decision, CopyDecision.COPYABLE)
            self.assertEqual(results[0].available_items, ("メモ", "星評価"))
        finally:
            window.close()

    def test_memo_only_and_rating_only_are_available_without_tags_or_title(self) -> None:
        for label, memo, rating, expected_item in (
            ("memo", "メモだけ", 0, "メモ"),
            ("rating", "", 1, "星評価"),
        ):
            with self.subTest(label=label):
                source, _source_id = self._file(f"{label}-source.png", label.encode())
                target, target_id = self._file(f"{label}-target.png", label.encode())
                self.database.set_memo(source, memo)
                self.database.set_rating(source, rating)
                self._calculate(source)
                self._calculate(target)

                results = detect_reuse_candidates(self.database, [target_id])

                self.assertEqual(len(results), 1)
                self.assertEqual(
                    results[0].assessment.decision, CopyDecision.COPYABLE
                )
                self.assertEqual(results[0].available_items, (expected_item,))


if __name__ == "__main__":
    unittest.main()
