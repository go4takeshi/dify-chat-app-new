# app.py

import streamlit as st
import pandas as pd
# Difyのクライアントライブラリをインポートします。
# ライブラリ名が異なる場合は、実際のライブラリ名に修正してください。
# 例: from dify_client import ChatClient
from dify_client import ChatClient 

# --- 1. 基本設定 & 初期化 ---

# Streamlitページの基本設定
st.set_page_config(
    page_title="CSV Q&A Assistant",
    page_icon="📊",
    layout="wide"
)

# アプリのタイトル
st.title("📊 CSV Q&A Assistant")
st.caption("アップロードしたCSVファイルの内容について、AIに質問できます。")

# Dify APIクライアントの初期化
# Streamlit Community CloudのSecrets機能を使うことを強く推奨します。
# st.secrets["DIFY_API_KEY"] のように記述します。
try:
    client = ChatClient(api_key=st.secrets["DIFY_API_KEY"])
except Exception as e:
    st.error("Dify APIキーの設定に問題があるようです。StreamlitのSecretsを確認してください。")
    st.stop() # APIキーがない場合はアプリを停止

# セッション状態(st.session_state)の初期化
# これにより、ユーザーが操作してもデータがリセットされなくなります。
if "messages" not in st.session_state:
    st.session_state.messages = []
if "df" not in st.session_state:
    st.session_state.df = None
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = ""


# --- 2. サイドバー (ファイルアップロード機能) ---

with st.sidebar:
    st.header("Step 1: ファイルをアップロード")
    uploaded_file = st.file_uploader(
        "質問したいCSVファイルを選択してください",
        type=["csv"]
    )

    if uploaded_file is not None:
        # 新しいファイルがアップロードされたら、データを読み込み、チャットをリセットする
        try:
            # アップロードされたCSVをPandas DataFrameとして読み込む
            df_new = pd.read_csv(uploaded_file)

            # 読み込んだDataFrameをセッション状態に保存
            st.session_state.df = df_new
            
            # 新しいファイルが読み込まれたので、会話履歴とDifyの会話IDをリセット
            st.session_state.messages = []
            st.session_state.conversation_id = ""

            st.success("ファイルの読み込みが完了しました。")
            st.info("ファイルの内容（先頭5行）:")
            # 読み込んだデータのプレビューを表示
            st.dataframe(df_new.head())

        except Exception as e:
            st.error(f"ファイルの読み込み中にエラーが発生しました: {e}")
            st.session_state.df = None # エラー時はDataFrameを空にする
    
    st.divider()
    st.markdown(
        "Created with [Streamlit](https://streamlit.io/) & "
        "[Dify](https://dify.ai/)."
    )


# --- 3. メイン画面 (チャットインターフェース) ---

st.header("Step 2: 質問を入力")

# 過去の会話履歴をすべて表示
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# ユーザーからの新しい入力を受け取る
if prompt := st.chat_input("CSVファイルについて質問してください..."):
    # まず、ファイルがアップロードされているか確認
    if st.session_state.df is None:
        st.warning("質問する前に、サイドバーからCSVファイルをアップロードしてください。")
    else:
        # ユーザーの質問を会話履歴に追加して表示
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # アシスタントの応答を準備
        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            
            # --- LLMへの入力（プロンプト）を構築 ---
            # DataFrameをLLMが読みやすいMarkdown形式のテキストに変換
            # index=Falseで、行番号は含めないようにする
            csv_data_string = st.session_state.df.to_markdown(index=False)

            # LLMへの指示（システムプロンプト）とデータを組み合わせる
            final_prompt = f"""
あなたはプロのデータアナリストです。
以下のCSVデータの内容を正確に理解し、ユーザーからの質問に対して、データに基づいた回答を日本語で生成してください。

--- CSVデータ ---
{csv_data_string}
--- CSVデータここまで ---

ユーザーの質問: {prompt}
"""
            
            try:
                # --- Dify APIを呼び出す ---
                # Difyの会話機能を使う場合、conversation_idを渡します
                response = client.create_chat_message(
                    conversation_id=st.session_state.conversation_id or None,
                    query=final_prompt,
                    user="streamlit-user", # ユーザーを識別するID
                    stream=True # ストリーミング応答を有効にする
                )
                response.raise_for_status() # エラーチェック

                # ストリーミング応答を処理
                full_response = ""
                for chunk in response.iter_content(chunk_size=None):
                    # Difyからのレスポンス形式に合わせてchunkをパースする必要があります
                    # 以下は一般的なイベントストリームのパース例です
                    # data: {"event": "message", "answer": "...", ...}
                    if chunk.startswith(b'data:'):
                        import json
                        try:
                            data_str = chunk.decode('utf-8').split('data: ')[1]
                            data_json = json.loads(data_str)
                            if data_json.get("event") == "message":
                                full_response += data_json.get("answer", "")
                                message_placeholder.markdown(full_response + "▌")
                                # 新しい会話の場合、conversation_idを保存
                                if "conversation_id" in data_json and not st.session_state.conversation_id:
                                    st.session_state.conversation_id = data_json["conversation_id"]
                        except (json.JSONDecodeError, IndexError):
                            continue # パースエラーは無視

                message_placeholder.markdown(full_response)
                # アシスタントの最終応答を会話履歴に追加
                st.session_state.messages.append({"role": "assistant", "content": full_response})

            except Exception as e:
                error_message = f"API呼び出し中にエラーが発生しました: {e}"
                st.error(error_message)
                st.session_state.messages.append({"role": "assistant", "content": error_message})
