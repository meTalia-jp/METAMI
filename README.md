# M.E.T.A.M.I.

**読み方:** めたみ
**正式名称:** Metadata Exploration & Tag Analysis with Media Insight
**現在の状態:** Ver1.0.0 リリース候補

M.E.T.A.M.I.は、画像・動画の内容と埋め込みメタデータを確認し、
自分用の評価・タグ・メモを整理するWindows向けデスクトップアプリです。
原本ファイルへユーザー情報を書き込まず、M.E.T.A.M.I.専用のSQLite
データベースへ保存します。

![M.E.T.A.M.I. メイン画面](docs/images/metami-main.png)

## 名称の由来

代表的な名称展開は次のとおりです。

> **M**etadata **E**xploration & **T**ag **A**nalysis with **M**edia **I**nsight

機能や使い方を別の角度から表す名称として、次の解釈も使用します。

> **M**edia **E**xplorer for **T**ags, **A**ssets, **M**etadata, and **I**nsights

Media Explorer for Tags, Assets, Metadata, and Insights

読み方は「めたみ」です。キャラクターのタリアとめたみが、素材探しと
メタデータ整理を支援する構成になっています。

## 主な機能

- PNG、WEBP、JPG/JPEG、MP4のファイル・フォルダ読込とドラッグ＆ドロップ
- サムネイル付きカード一覧
- 縦長・横長・1:1素材に応じたカード表示
- 画像プレビューとMP4再生
- ファイル名、解像度、サイズ、再生時間などの基本情報表示
- 対応メタデータからPrompt、Model、Seed等の生成情報を表示
- WorkflowとJSON全文の表示・コピー
- ファイル名、Prompt、Model、Seed、形式による検索・絞り込み
- 0～3の評価、複数タグ、400文字までのメモ、40文字までのユーザータイトル
- 評価・タグ・メモ・ユーザータイトルのSQLite保存と再起動後の復元
- 見つからないファイルの記録表示、再関連付け、DB記録削除

### ユーザータイトルについて

「タグ・メモ」タブで、メディアごとに40文字までの任意タイトルを
設定できます。設定時は実ファイル名よりユーザータイトルを優先して
カードへ表示し、未設定の場合だけ実ファイル名へ戻ります。長いタイトルは
一覧で省略し、全文はツールチップで確認できます。タイトルは原本ではなく
SQLiteへ保存され、検索対象にもなります。

## 対応形式

| 形式 | 一覧・プレビュー | 基本情報 | 埋め込みメタデータ |
|---|---:|---:|---:|
| PNG | 対応 | 対応 | `tEXt`、`zTXt`、`iTXt` |
| WEBP | 対応 | 対応 | EXIF、XMP、ICCプロファイル |
| JPG/JPEG | 対応 | 対応 | 詳細EXIF表示は対象外 |
| MP4 | 対応 | 対応 | MP4コンテナ内の文字列メタデータ |

JPG/JPEGはEXIF Orientationがある場合、Qtの自動変換機能で表示方向へ
反映します。EXIF情報そのものを編集・詳細表示する機能はありません。
ファイルやコーデックによっては、Windows側の再生対応状況によりMP4を
再生できないことがあります。

## 動作環境

- Windows 10／11（64bit）
- Python 3.12系
- PySide6 6.11.1

開発時の自動試験環境はPython 3.13.12／PySide6 6.11.1です。
別PCではIntel N100／メモリ16GB／Windows／Python 3.12のvenv環境で、
依存関係の導入、`python src\main.py`、`run_metami.bat`、対応形式の表示、
タイトル・タグ・メモおよび初期レイアウトを確認しています。

## セットアップ

### 1. GitとPythonを用意する

- [Git for Windows](https://git-scm.com/download/win)
- [Python 3.12](https://www.python.org/downloads/)

Pythonのインストール時は、必要に応じて`Add python.exe to PATH`を
有効にしてください。

### 2. リポジトリを取得する

GitHubの「Code」からリポジトリURLを取得して実行してください。

```powershell
git clone <repository-url>
cd METAMI
```

### 3. venvを作成して有効化する

Python Launcherを使用する場合:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

`python`コマンドがPython 3.12を指している場合:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

PowerShellでスクリプト実行が拒否された場合は、現在のプロセスだけを
対象に次を実行してから有効化してください。

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 4. 依存パッケージを導入する

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt`には実行に必要なPySide6だけを記載しています。
SQLiteはPython標準ライブラリを使用するため、追加インストールは不要です。

## 起動

venvを有効化して起動:

```powershell
python src/main.py
```

または、エクスプローラーから`run_metami.bat`を実行します。
`.venv`が存在しない場合やPySide6が未導入の場合は、セットアップ手順を
日本語で案内します。

## 基本操作

1. 「ファイルを開く」または「フォルダを開く」で素材を追加します。
2. 左側のカードを選択すると、右側へプレビューと情報が表示されます。
3. 検索欄と各フィルターで一覧を絞り込みます。
4. 右上の星で0～3の評価を設定します。
5. 「タグ・メモ」タブでタイトル、タグ、メモを保存します。
6. 「Workflow」「JSON全文」タブで埋め込み情報を確認します。

## データベースと原本保護

実行時データベースは次の場所へ自動作成されます。

```text
data/metami.db
```

- 評価、タグ、メモ、ユーザータイトル、ファイル状態、確認済みパスを保存します。
- 原本画像・動画へ評価、タグ、メモ、ユーザータイトルを書き込みません。
- 原本のファイル名変更、移動、削除、メタデータ書換えを行いません。
- バックアップする場合は、アプリを終了してから`data/metami.db`を
  安全な場所へコピーしてください。
- `data/metami.db`はGit管理対象外です。

### missing管理

登録済みパスに原本が存在しない場合、記録を`missing`として保持します。
通常一覧からは除外されますが、ファイル状態フィルターで確認できます。

- 「ファイルを探す」: ユーザー確認後、新しいパスへDB記録を関連付けます。
- 「METAMIの記録を削除」: 評価・タグ関連・メモ・パス履歴をDBから
  削除します。原本ファイルは削除しません。

## プライバシー

次の内容はGitHubへ追加しないでください。

- `data/metami.db`およびSQLiteの補助ファイル
- `sample/`内の私物画像・動画
- メモ、個人情報、ローカルパスを含むログ
- APIキー、トークン、`.env`等の秘密情報
- バックアップ、出力物、キャッシュ

本リポジトリはサンプルDBを配布せず、初回起動時に空DBを生成します。

## テスト

venvを有効化した状態で実行します。

```powershell
python -m unittest discover -s tests -v
```

公開前には[GitHub公開前チェックリスト](docs/GITHUB公開前チェックリスト.md)
も確認してください。

## 別PCでの確認と不具合報告

別PCでは、このREADMEの「セットアップ」から順に実施してください。
初回起動後に`data/metami.db`が生成されることを確認し、本人が権利を持つ
PNG、WEBP、JPG/JPEG、MP4を1件ずつ読み込んでください。

不具合報告には次の情報を含めてください。

- M.E.T.A.M.I.のバージョン
- Windowsのバージョンと表示倍率
- `python --version`
- `python -c "import PySide6; print(PySide6.__version__)"`
- 再現手順と表示されたエラーメッセージ
- ファイル形式と縦横サイズ

個人ファイル、DB、メモ、フルパス、生成Promptをそのまま添付しないで
ください。公開後はGitHub Issuesの案内に従ってください。

## スクリーンショット

上部のメイン画面は、ユーザータイトルを優先表示し、基本情報タブを
開かないことで実ファイル名・実フォルダー名・フルパスを表示しない
公開用構成です。

## プロジェクトメッセージ

### 日本語

![M.E.T.A.M.I. ライセンス説明・日本語](docs/images/project-message-ja.png)

### English

![M.E.T.A.M.I. License message in English](docs/images/project-message-en.png)

## ライセンスと素材

M.E.T.A.M.I.のソースコードは[MIT License](LICENSE)で公開します。

```text
Copyright (c) 2026 meTalia-jp
```

MIT Licenseはソースコードに適用されます。タリア、めたみ、ロゴ、
キャラクター画像、関連イラスト、高解像度原画および制作データは
MIT License対象外です。

PySide6／Qt for Pythonは本リポジトリへ同梱せず、利用者がpipで導入します。
Qt for PythonはLGPLv3／GPLv3または商用ライセンスで提供されています。
本リポジトリではPythonソースだけを公開し、PySide6、Shiboken6、Qt本体を
同梱・改変せず、GPL専用のQtモジュールも使用しません。ライセンス条件は
[Qt for Python公式ドキュメント](https://doc.qt.io/qtforpython-6/)
および配布パッケージの表示を確認してください。

将来、実行ファイル化してQtライブラリを同梱・再配布する場合は今回の判断
対象外とし、LGPLv3を含む適用条件を改めて確認します。

キャラクター、ロゴ、画像素材はソースコードと別の権利物として扱います。
詳細は[素材の権利表記](src/assets/README.md)と
[NOTICE](NOTICE.md)を参照してください。
開発用GUIモックアップ4画像はGitHub公開対象に含めません。

### キャラクター利用方針

- 非商用のファンアート・二次創作を歓迎します。
- キャラクターの改変や別解釈を許可します。
- SNSや個人サイトへの非商用投稿を許可します。
- 商用利用は事前相談とします。
- 公式と誤認させる利用は禁止します。
- 違法、差別、嫌がらせ目的の利用は禁止します。
- キャラクター素材そのものの無断販売は禁止します。
- 高解像度原画や制作データは公開対象外です。

## 開発言語とコントリビューション

本プロジェクトは日本語中心で開発・保守します。Issueや説明は日本語を
基本としますが、英語ドキュメント、英語UI、翻訳改善のPull Requestを
歓迎します。

## 既知の制約

- MP4再生可否はWindowsとQtが利用できるコーデックに依存します。
- JPEGの詳細EXIF表示・編集には対応していません。
- Xの280文字表示は目安で、X固有の重み付き文字数計算ではありません。
- ファイル移動・改名の完全ハッシュによる自動追跡は行いません。
- UIと文書は日本語中心で、英語UIはまだありません。

## 開発状況と今後

- 現在: Ver1.0.0 リリース候補
- 公開前: GitHub上の最終差分、公開範囲、画像表示を所有者が確認
- 公開後: 不具合修正を優先し、新機能は別バージョンで検討

タリアポイント、METAMIカード、AI画像解析等は将来構想であり、
Ver1.0.0には含まれません。
