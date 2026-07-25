"""M.E.T.A.M.I.（めたみ）のエントリーポイント。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from storage.database import DatabaseError, MetadataDatabase
from ui.main_window import APP_TITLE, MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationDisplayName(APP_TITLE)
    database = MetadataDatabase()
    database_error = ""
    try:
        database.initialize()
    except DatabaseError as error:
        database = None
        database_error = str(error)
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
