"""M.E.T.A.M.I.（めたみ）のエントリーポイント。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from storage.database import DatabaseError, MetadataDatabase
from storage.data_management import DataManagementError, validate_metami_database
from storage.migrations import MigrationError, migrate_database
from storage.schema_version import (
    METAMI_SCHEMA_VERSION,
    SchemaVersionError,
    get_user_version,
)
from ui.main_window import APP_TITLE, MainWindow


def prepare_database(database: MetadataDatabase) -> bool:
    """通常利用より前に中央DBを作成・検証・必要時更新する。"""
    if not database.path.exists():
        database.initialize()
        return True

    validate_metami_database(database.path)
    version = get_user_version(database.path)
    if version > METAMI_SCHEMA_VERSION:
        QMessageBox.critical(
            None,
            "METAMIデータを開けません",
            "このMETAMIデータは、より新しいバージョンのMETAMIで\n"
            "作成または更新されています。\n\n"
            f"現在のMETAMIが対応するDBスキーマ: "
            f"{METAMI_SCHEMA_VERSION}\n"
            f"このMETAMIデータのDBスキーマ: {version}\n\n"
            "安全のため、このバージョンでは開けません。\n"
            "より新しいMETAMIを使用してください。",
        )
        return False
    if version == METAMI_SCHEMA_VERSION:
        return True

    if not confirm_schema_update():
        return False

    try:
        migrate_database(database.path)
    except MigrationError as error:
        details = [
            str(error),
            f"現在DB: {database.path.resolve()}",
        ]
        if error.backup_path is not None:
            details.append(f"更新前バックアップ: {error.backup_path}")
        if error.migration_error is not None:
            details.append(f"更新エラー: {error.migration_error}")
        if error.recovery_error is not None:
            details.extend(
                (
                    f"回復エラー: {error.recovery_error}",
                    "手動での復旧が必要です。",
                )
            )
        else:
            details.append("アプリを終了し、内容を確認してください。")
        QMessageBox.critical(
            None,
            "METAMIデータの更新に失敗しました",
            "\n\n".join(details),
        )
        return False
    QMessageBox.information(
        None,
        "METAMIデータの更新",
        "更新が完了しました。",
    )
    return True


def confirm_schema_update() -> bool:
    """スキーマ0の既存DBを更新するか、安全側を既定に確認する。"""
    box = QMessageBox()
    box.setWindowTitle("METAMIデータの更新")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText("METAMIデータを新しい管理形式へ更新します。")
    box.setInformativeText(
        "今回の更新では、データベース構造は変更されません。\n"
        "今後の安全な更新に備えて、スキーマ情報を登録します。\n\n"
        "更新前のMETAMIデータは自動的にバックアップされます。\n"
        "画像・動画ファイル本体は変更されません。\n\n"
        "続行しますか？"
    )
    update_button = box.addButton(
        "更新する", QMessageBox.ButtonRole.AcceptRole
    )
    exit_button = box.addButton(
        "終了する", QMessageBox.ButtonRole.RejectRole
    )
    box.setDefaultButton(exit_button)
    box.setEscapeButton(exit_button)
    box.exec()
    return box.clickedButton() is update_button


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationDisplayName(APP_TITLE)
    database = MetadataDatabase()
    database_error = ""
    try:
        if not prepare_database(database):
            return 1
    except (
        DatabaseError,
        DataManagementError,
        SchemaVersionError,
    ) as error:
        QMessageBox.critical(
            None,
            "METAMIデータを開けません",
            f"{error}\n\n安全のため、アプリを終了します。",
        )
        return 1
    else:
        try:
            database.check_registered_files()
        except DatabaseError as error:
            # 状態確認だけの失敗ではDB全体を無効化せず、GUIを継続する。
            database_error = str(error)
    window = MainWindow(database=database, database_error=database_error)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
