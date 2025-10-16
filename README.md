# ミノンBC AIファンチャット (Streamlit)

このリポジトリは、Dify の Chat API を使って Streamlit 上で複数ペルソナと会話できるチャットアプリです。会話履歴は Google Sheets にも自動保存でき、チャット中に CSV をアップロードして LLM に渡す機能も備えています。

さらに、チャット履歴のダウンロード機能を拡張し、アシスタントの応答を改行区切りのキーワードとして出力した場合に、それらを `keyword_1`, `keyword_2`, ... の別列へ展開してCSVでダウンロードできるようにしました。

## 主な機能
- 複数のペルソナ（Secrets に API キーを設定）
- 会話の共有（会話ID）
- 会話ログを Google Sheets に保存
- 会話途中で CSV をアップロードし LLM に渡す（先頭100行を添付可能）
- **🎨 画像生成機能**
  - Difyとは独立した画像生成セクション
  - 最近のチャット内容を参考にした画像生成
  - 7種類のスタイル選択（プロフェッショナル、アート風、写真風など）
  - 4種類のサイズ選択（正方形、横長、縦長、コスト削減用小サイズ）
  - Google Driveへの自動保存機能
- チャット履歴を CSV ダウンロード
  - 通常形式（role, name, content）
  - キーワード分割形式（assistant の content を改行で分割して `keyword_1..` 列に展開）
  - キーワード分割時の最大列数は UI スライダーで指定可能（デフォルト: 100、上限: 150）。上限を超えるキーワードは切り捨てられ、最後のセルに "(...+N truncated)" の注記が付きます。

## 必要な環境
- Python 3.8+
- 必要なパッケージは `requirements.txt` を参照してインストールしてください。

## ローカルでの実行方法
1. 仮想環境を作成（推奨）
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
2. 必要パッケージをインストール
```powershell
pip install -r requirements.txt
```
3. `streamlit` を起動
```powershell
streamlit run app.py
```
4. ブラウザで表示されるアドレスにアクセスして使用します。

## Secrets と設定
- Streamlit Community Cloud にデプロイする場合は、以下の Secrets を設定してください:
  - `PERSONA_1_KEY`, `PERSONA_2_KEY`, ... のように各ペルソナの API キー
  - `OPENAI_API_KEY`（画像生成機能用のOpenAI APIキー）
  - `gcp_service_account`（Google Service Account の JSON文字列、Google Sheets に保存する場合）
  - `gsheet_id`（Google Sheets のキー）
  - `drive_folder_name`（画像保存用Google Driveフォルダ名、省略時は "MinonBC_AI_Images"）

### Secrets設定例
```toml
[secrets]
PERSONA_1_KEY = "app-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
PERSONA_2_KEY = "app-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
OPENAI_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
gsheet_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz123456789"
drive_folder_name = "AI画像_2024年プロジェクト"
gcp_service_account = """
{
  "type": "service_account",
  "project_id": "your-project-id",
  ...
}
"""
```

## 画像生成機能の使い方
1. **Difyでアイディア抽出**
   - 左側のチャットでDifyに商品アイディアや企画を相談
   - 「画像については取り扱っていない」と言われても大丈夫

2. **画像生成に移行**
   - 右側の「参考にするメッセージ」で最近のチャット内容を選択
   - または「手動入力」で独自の内容を記述

3. **細かな調整**
   - スタイル（プロフェッショナル、アート風、写真風など）を選択
   - サイズ（正方形、横長、縦長、コスト削減用など）を選択

4. **生成・保存**
   - 「画像を生成する」ボタンで実行
   - 「Google Driveに保存」で永続化

## テストチェックリスト（キーワード分割機能）
1. アプリを起動し、ペルソナを選択してチャットを開始する。
2. アシスタントに対して「改行で区切られたキーワード」形式で応答するよう指示する。
   例: "以下の文章から3つのキーワードを改行で出力してください。文章: ..."
3. 画面下部で「ダウンロード形式」を「キーワード分割」に切り替える。
4. プレビューに `keyword_1`.. の列が表示され、各行でキーワードが別列に入っていることを確認する。
5. 必要であればスライダーで最大キーワード数を調整し、切り捨てが発生した場合は最後のセルに "(...+N truncated)" が付くことを確認する。
6. ダウンロードして Excel 等で列が分かれていることを確認する。

## 注意点
- LLM の応答が必ずしも「改行で区切られたキーワード」になるとは限りません。安定して分割させたい場合、明確な出力テンプレート（JSONやマークダウンのコードブロックなど）をプロンプトで指定することを推奨します。
- 大量データや非常に多いキーワード数はプレビューやダウンロードに時間がかかる可能性があります。

## 今後の改善案
- キーワード分割出力を JSON で受け取り検証・変換するワークフロー
- 切り捨て時に切り捨て数を別列で出力
- 大規模会話に対するストリーミング・逐次処理対応

---
作業を続けたい場合（例: 切り捨て数を別列で出力する、デフォルトの上限を変更する、CSVのカラム名をカスタマイズする等）、希望を教えてください。
