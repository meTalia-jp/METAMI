# NOTICE

## M.E.T.A.M.I.

- 正式名称: Metadata Exploration & Tag Analysis with Media Insight
- 読み方: めたみ
- Copyright (c) 2026 meTalia-jp
- ソースコードライセンス: MIT License

ルートの`LICENSE`はM.E.T.A.M.I.のソースコードと付随するソフトウェア
文書に適用されます。タリア、めたみ、ロゴ、キャラクター画像、関連
イラストおよび高解像度原画・制作データはMIT License対象外です。

## 外部依存

### PySide6 / Qt for Python

M.E.T.A.M.I.はPySide6を使用します。PySide6およびQtのバイナリは
このリポジトリへ直接同梱せず、`requirements.txt`に従いpipから
利用者がPyPI経由でインストールします。M.E.T.A.M.I.はPythonソース形式で
公開し、Qt／PySide6本体を改変せず、GPL専用のQtモジュールも使用しません。

Qt for PythonはLGPLv3／GPLv3または商用ライセンスで提供されています。
本リポジトリのMIT Licenseは、PySide6、Shiboken6、Qtへ適用されません。

- Qt for Python: https://doc.qt.io/qtforpython-6/
- PySide6: https://pypi.org/project/PySide6/

将来、実行ファイル化してQtライブラリを同梱・再配布する場合は、
今回の判断対象外とし、LGPLv3を含む適用条件を改めて確認します。

## キャラクター・画像

次の画像類はソースコードと別の権利物として扱います。

- `src/assets/**`
- `docs/images/**`
- 将来追加するスクリーンショット、ロゴ、配布用画像

開発用GUIモックアップは公開対象に含めません。公開画像とキャラクター画像に
第三者素材が含まれていないことを権利者が確認済みです。

キャラクターの利用条件は次のとおりです。

- 非商用のファンアート・二次創作、改変、別解釈を歓迎します。
- SNSや個人サイトへの非商用投稿を許可します。
- 商用利用は事前相談が必要です。
- 公式と誤認させる利用、違法・差別・嫌がらせ目的の利用は禁止します。
- キャラクター素材そのものの無断販売は禁止します。
- 高解像度原画と制作データは公開・配布対象外です。

詳しくは`src/assets/README.md`を確認してください。

## フォント

ソースコードではWindows上のフォント名を表示候補として指定しますが、
フォントファイル自体は同梱しません。利用環境に存在しない場合は
Yu Gothic UIまたはMeiryo UI等へフォールバックします。
