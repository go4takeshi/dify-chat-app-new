# -*- coding: utf-8 -*-
import os
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
import pandas as pd
import streamlit as st
from openai import OpenAI
from PIL import Image
import io
import base64

# =========================
# Dify 設定
# =========================
DIFY_CHAT_URL = "https://api.dify.ai/v1/chat-messages"

# =========================
# OpenAI 設定
# =========================
def get_openai_client():
    """OpenAI クライアントを取得"""
    api_key = st.secrets.get("OPENAI_API_KEY")
    if not api_key:
        st.error("OpenAI APIキーがSecretsに設定されていません。")
        return None
    return OpenAI(api_key=api_key)

# ペルソナの表示名とSecretsのキーをマッピング
PERSONA_NAMES = [
    "①ひらめ１号_g1",
    "②ひらめ１号_g2",
    "③ひらめ１号_g3",
]


def get_persona_api_keys():
    """SecretsからAPIキーを読み込む（トップレベル/ネスト両対応 & フォールバック）"""
    keys = {}

    # 1) まずはトップレベル（従来）
    for i, name in enumerate(PERSONA_NAMES):
        k = st.secrets.get(f"PERSONA_{i+1}_KEY")
        if k:
            keys[name] = k

    # 2) 次に [persona_api_keys] テーブル
    if "persona_api_keys" in st.secrets:
        table = st.secrets["persona_api_keys"]
        for i, name in enumerate(PERSONA_NAMES):
            k = table.get(f"PERSONA_{i+1}_KEY")
            if k and name not in keys:
                keys[name] = k

    # 3) 何も見つからない場合の汎用フォールバック（任意）
    if not keys:
        generic = st.secrets.get("DIFY_API_KEY")
        if generic:
            for name in PERSONA_NAMES:
                keys[name] = generic

    return keys

PERSONA_API_KEYS = get_persona_api_keys()

# アバター（ファイルが無い場合は絵文字にフォールバック）
PERSONA_AVATARS = {
    "①ひらめ１号_g1": "persona_1.jpg",
    "②ひらめ１号_g2": "persona_2.jpg",
    "③ひらめ１号_g3": "persona_3.jpg",
}

# =========================
# JSON解析とDALL-E 3機能
# =========================
def parse_dify_response(response_text):
    """Difyからの応答をパースして構造化データを返す"""
    try:
        # JSONとして解析を試行
        data = json.loads(response_text)
        
        # 新しいスキーマ（summariesの配列）に対応
        if "summaries" in data and isinstance(data["summaries"], list):
            summaries = []
            for item in data["summaries"]:
                title = item.get("title", "")
                summary = item.get("summary", "")
                category = item.get("category", "")
                image_prompt = item.get("image_prompt", "")
                
                # 概要が200文字を超える場合は切り詰める
                if len(summary) > 200:
                    summary = summary[:200] + "..."
                
                summaries.append({
                    "title": title,
                    "summary": summary,
                    "category": category,
                    "image_prompt": image_prompt
                })
            
            return {
                "summaries": summaries,
                "is_json": True,
                "is_multiple": True,
                "raw_text": response_text
            }
        
        # 旧形式（単一アイテム）にも対応
        elif "title" in data or "summary" in data:
            title = data.get("title", "")
            summary = data.get("summary", "")
            category = data.get("category", "")
            image_prompt = data.get("image_prompt", "")
            
            # 概要が200文字を超える場合は切り詰める
            if len(summary) > 200:
                summary = summary[:200] + "..."
                
            return {
                "summaries": [{
                    "title": title,
                    "summary": summary,
                    "category": category,
                    "image_prompt": image_prompt
                }],
                "is_json": True,
                "is_multiple": False,
                "raw_text": response_text
            }
        
        # その他のJSON形式
        else:
            return {
                "summaries": [],
                "is_json": False,
                "is_multiple": False,
                "raw_text": response_text
            }
            
    except json.JSONDecodeError:
        # JSONでない場合はそのまま返す
        return {
            "summaries": [],
            "is_json": False,
            "is_multiple": False,
            "raw_text": response_text
        }

def generate_image_with_dalle3(prompt):
    """DALL-E 3を使用して画像を生成"""
    try:
        client = get_openai_client()
        if not client:
            return None
            
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        
        image_url = response.data[0].url
        
        # 画像をダウンロードして返す
        img_response = requests.get(image_url)
        img_response.raise_for_status()
        
        image = Image.open(io.BytesIO(img_response.content))
        return image
        
    except Exception as e:
        st.error(f"画像生成中にエラーが発生しました: {e}")
        return None

def display_parsed_response(parsed_data):
    """パースされたデータを適切に表示"""
    if parsed_data["is_json"] and parsed_data["summaries"]:
        # JSONデータの場合（複数のアイデア）
        for i, summary_item in enumerate(parsed_data["summaries"]):
            # 複数アイテムがある場合は区切り線を表示
            if i > 0:
                st.markdown("---")
            
            # タイトル表示
            if summary_item["title"]:
                st.markdown(f"### {summary_item['title']}")
            
            # カテゴリ表示
            if summary_item["category"]:
                st.markdown(f"**カテゴリ:** {summary_item['category']}")
            
            # 概要表示
            if summary_item["summary"]:
                st.markdown(summary_item["summary"])
            
            # 画像生成の指示がある場合
            if summary_item["image_prompt"]:
                st.markdown("🎨 **画像を生成中...**")
                st.info(f"プロンプト: {summary_item['image_prompt']}")
                
                with st.spinner("DALL-E 3で画像を生成しています..."):
                    generated_image = generate_image_with_dalle3(summary_item["image_prompt"])
                    
                if generated_image:
                    st.image(generated_image, caption=f"生成画像: {summary_item['image_prompt'][:50]}...", use_column_width=True)
                else:
                    st.error("画像の生成に失敗しました。")
    else:
        # 通常のテキストの場合
        st.markdown(parsed_data["raw_text"])

# =========================
# Google Sheets 接続ユーティリティ
# =========================
def _get_sa_dict():
    """Secretsの gcp_service_account から dict を返す（JSON文字列/TOMLテーブル両対応）"""
    if "gcp_service_account" not in st.secrets:
        return None
    raw = st.secrets["gcp_service_account"]
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # private_key の実改行を \n に自動補正して再トライ（貼付ミス救済）
            fixed = raw.replace("\r\n", "\n").replace("\n", "\\n")
            return json.loads(fixed)
    return dict(raw)

@st.cache_resource
def _gs_client():
    """gspread クライアントを返す（キャッシュする）"""
    import gspread
    from google.oauth2.service_account import Credentials

    sa_info = _get_sa_dict()
    if not sa_info:
        st.error("`gcp_service_account` がSecretsに設定されていません。")
        st.stop()

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
    return gspread.authorize(creds)

def _open_sheet():
    """chat_logs ワークシートを開く（なければ作成）。権限/IDエラーはUI表示して停止。"""
    import gspread
    from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound, GSpreadException

    if "gsheet_id" not in st.secrets:
        st.error("`gsheet_id` がSecretsに設定されていません。")
        st.stop()

    gc = _gs_client()
    sheet_id = st.secrets["gsheet_id"]

    try:
        sh = gc.open_by_key(sheet_id)
    except SpreadsheetNotFound:
        st.error("スプレッドシートが見つかりません。Secrets の `gsheet_id` を確認してください。")
        st.stop()
    except GSpreadException as e:
        if "PERMISSION_DENIED" in str(e):
            sa = _get_sa_dict()
            st.error("スプレッドシートへのアクセス権がありません。対象シートを下記のサービスアカウントに『編集者』で共有してください。")
            st.code(sa.get("client_email", "(unknown)"))
            st.stop()
        else:
            raise

    try:
        ws = sh.worksheet("chat_logs")
    except WorksheetNotFound:
        ws = sh.add_worksheet(title="chat_logs", rows=1000, cols=10)
        ws.append_row(["timestamp", "conversation_id", "bot_type", "role", "name", "content"])
    return ws

def save_log(conversation_id: str, bot_type: str, role: str, name: str, content: str):
    """一行追記（指数バックオフの簡易リトライ付き）"""
    from gspread.exceptions import APIError

    try:
        ws = _open_sheet()
        row = [datetime.now(timezone.utc).isoformat(), conversation_id, bot_type, role, name, content]

        for i in range(5):
            try:
                ws.append_row(row, value_input_option="RAW")
                return
            except APIError as e:
                code = getattr(getattr(e, "response", None), "status_code", None)
                if code in (429, 500, 503):
                    time.sleep(1.5 ** i)
                    continue
                raise
        raise RuntimeError("Google Sheets への保存に連続失敗しました。")
    except Exception as e:
        st.warning(f"Google Sheetsへのログ保存中にエラーが発生しました: {e}")

@st.cache_data(ttl=60)  # ライブ更新のため短めのTTL
def load_history(conversation_id: str) -> pd.DataFrame:
    """指定された会話IDの履歴をGoogle Sheetsから読み込む"""
    try:
        ws = _open_sheet()
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["timestamp", "conversation_id", "bot_type", "role", "name", "content"])

        df_filtered = df[df["conversation_id"] == conversation_id].copy()
        if not df_filtered.empty and "timestamp" in df_filtered.columns:
            df_filtered["timestamp"] = pd.to_datetime(df_filtered["timestamp"], errors="coerce", utc=True)
            df_filtered = df_filtered.sort_values("timestamp")
        return df_filtered
    except Exception as e:
        st.error(f"Google Sheetsからの履歴読み込み中にエラーが発生しました: {e}")
        return pd.DataFrame()

# =========================
# Streamlit UI
# =========================
st.set_page_config(page_title="ひらめ１号との対話", layout="centered")

# --- session_stateの初期化 ---
def init_session_state():
    st.session_state.page = "login"
    st.session_state.cid = ""
    st.session_state.messages = []
    st.session_state.bot_type = ""
    st.session_state.user_avatar_data = None
    st.session_state.name = ""

if "page" not in st.session_state:
    init_session_state()

# --- クエリパラメータから復元（共有リンク用） ---
def restore_from_query_params():
    qp = st.query_params
    if qp.get("page") == "chat":
        st.session_state.page = "chat"
        st.session_state.cid = qp.get("cid", "")
        st.session_state.bot_type = qp.get("bot", "")
        st.session_state.name = qp.get("name", "")
        # ページ遷移時にクエリパラメータをクリアして、再読み込みループを防ぐ
        st.query_params.clear()
        st.rerun()

if st.session_state.page == "login" and st.query_params.get("page") == "chat":
    restore_from_query_params()

# ========== STEP 1: ログイン画面 ==========
if st.session_state.page == "login":
    st.title("ミノンBC AIファンとの対話")

    # APIキーが一つも設定されていない場合はエラー表示
    if not PERSONA_API_KEYS:
        st.error("APIキーが一つも設定されていません。Streamlit CloudのSecretsに `PERSONA_1_KEY` などを設定してください。")
        st.stop()
    
    # OpenAI APIキーの確認
    if not st.secrets.get("OPENAI_API_KEY"):
        st.warning("⚠️ OpenAI APIキーが設定されていません。画像生成機能を使用するには、Streamlit CloudのSecretsに `OPENAI_API_KEY` を設定してください。")
    
    # JSON出力フォーマットの説明
    with st.expander("📖 Dify出力フォーマットについて"):
        st.markdown("""
        **JSON形式での出力**
        
        Difyから以下のJSON形式で出力すると、適切にフォーマットされます：
        
        ```json
        {
            "planner": {},
            "summaries": [
                {
                    "title": "アイデアのタイトル",
                    "summary": "200文字以内の概要",
                    "category": "カテゴリ名",
                    "image_prompt": "DALL-E 3用の画像生成プロンプト"
                }
            ]
        }
        ```
        
        **フィールドの説明：**
        - `title`: 表示されるタイトル
        - `summary`: 200文字以内の概要（超過分は自動切り詰め）
        - `category`: アイデアのカテゴリ
        - `image_prompt`: 画像生成指示がある場合のプロンプト
        
        **特徴：**
        - 複数のアイデアを配列で返すことができます
        - 各アイデアは区切り線で分けて表示されます
        - 画像プロンプトがある場合は自動的にDALL-E 3で画像生成します
        - JSON形式でない場合は通常のテキストとして表示されます
        """)

    with st.form("user_info_form"):
        name = st.text_input("あなたの表示名", value=st.session_state.name or "")
        bot_type = st.selectbox(
            "対話するAIペルソナ",
            list(PERSONA_API_KEYS.keys()),
            index=(list(PERSONA_API_KEYS.keys()).index(st.session_state.bot_type)
                   if st.session_state.bot_type in PERSONA_API_KEYS else 0),
        )
        existing_cid = st.text_input("既存の会話ID（共有リンクで参加する場合）", value=st.session_state.cid or "")
        uploaded_file = st.file_uploader("あなたのアバター画像（任意）", type=["png", "jpg", "jpeg"])
        submitted = st.form_submit_button("チャット開始")

    if submitted:
        if not name:
            st.warning("表示名を入力してください。")
        else:
            st.session_state.name = name.strip()
            st.session_state.bot_type = bot_type
            st.session_state.cid = existing_cid.strip()
            if uploaded_file is not None:
                st.session_state.user_avatar_data = uploaded_file.getvalue()
            else:
                st.session_state.user_avatar_data = None

            st.session_state.messages = []
            st.session_state.page = "chat"
            st.rerun()

# ========== STEP 2: チャット画面 ==========
elif st.session_state.page == "chat":
    st.markdown(f"#### 💬 {st.session_state.bot_type}")
    st.caption("同じ会話IDを共有すれば、複数人で同じ会話に参加できます。")

    # --- 共有リンク表示 ---
    cid_show = st.session_state.cid or "(未発行：最初の発話で採番)"
    st.info(f"会話ID: `{cid_show}`")
    if st.session_state.cid:
        params = {
            "page": "chat",
            "cid": st.session_state.cid,
            "bot": st.session_state.bot_type,
            "name": st.session_state.name,
        }
        # Streamlit CloudのベースURLを取得（ローカルでは動作しない場合がある）
        try:
            from streamlit.web.server.server import Server
            base_url = Server.get_current()._get_base_url()
            full_url = f"https://{base_url}{st.runtime.get_script_run_ctx().page_script_hash}"
            share_link = f"{full_url}?{urlencode(params)}"
            st.code(share_link, language="text")
        except (ImportError, AttributeError):
            # ローカル環境や取得失敗時のフォールバック
            share_link = f"?{urlencode(params)}"
            st.code(share_link, language="text")

    # --- アバター設定 ---
    assistant_avatar_file = PERSONA_AVATARS.get(st.session_state.bot_type, "default_assistant.png")
    user_avatar = st.session_state.get("user_avatar_data") if st.session_state.get("user_avatar_data") else "👤"
    assistant_avatar = assistant_avatar_file if os.path.exists(assistant_avatar_file) else "🤖"
    if assistant_avatar == "🤖":
        st.info(f"アシスタントのアバター画像（{assistant_avatar_file}）が見つかりません。リポジトリのルートに画像を配置すると表示されます。")

    # --- 履歴表示 ---
    # 1. Google Sheetsから履歴を読み込み
    if st.session_state.cid and not st.session_state.messages:
        history_df = load_history(st.session_state.cid)
        if not history_df.empty:
            for _, row in history_df.iterrows():
                st.session_state.messages.append({
                    "role": row["role"],
                    "content": row["content"],
                    "name": row["name"]
                })

    # 2. st.session_state.messages を表示
    for msg in st.session_state.messages:
        role = msg["role"]
        name = msg.get("name", role)
        avatar = assistant_avatar if role == "assistant" else user_avatar
        with st.chat_message(name, avatar=avatar):
            if role == "assistant":
                # アシスタントの場合は特別な表示処理
                parsed_data = parse_dify_response(msg["content"])
                if parsed_data["is_json"]:
                    display_parsed_response(parsed_data)
                else:
                    st.markdown(msg["content"])
            else:
                st.markdown(msg["content"])

    # --- チャット入力 ---
    if user_input := st.chat_input("メッセージを入力してください"):
        # ユーザーメッセージを即時表示
        user_message = {"role": "user", "content": user_input, "name": st.session_state.name}
        st.session_state.messages.append(user_message)
        with st.chat_message(st.session_state.name, avatar=user_avatar):
            st.markdown(user_input)

        # ユーザーメッセージをログに保存
        save_log(
            st.session_state.cid or "(allocating...)",
            st.session_state.bot_type,
            "user",
            st.session_state.name,
            user_input
        )

        # --- Dify APIへリクエスト（安定版） ---
        api_key = PERSONA_API_KEYS.get(st.session_state.bot_type)
        if not api_key:
            st.error("選択されたペルソナのAPIキーが未設定です。")
            st.stop()

        headers = {"Authorization": f"Bearer {api_key}"}  # Content-Type は json= が自動付与

        # 表示名→英数字・短めの安定IDに正規化（表示名自体はUI表示に使い、APIには安定IDを渡す）
        import re, hashlib
        raw_name = st.session_state.name or "guest"
        user_id = re.sub(r'[^A-Za-z0-9_-]', '_', raw_name).strip('_')[:64] or hashlib.md5(raw_name.encode()).hexdigest()[:16]

        # inputs は Dify 側の User Inputs とキー名を一致させること（未定義キーは送らない）
        inputs = {}

        payload = {
            "inputs": inputs,
            "query": user_input,
            "user": user_id,
            "response_mode": "blocking",
        }
        # ★初回は conversation_id を"送らない"（空文字は入れない）
        if st.session_state.cid:
            payload["conversation_id"] = st.session_state.cid

        def call_dify(pyld):
            return requests.post(DIFY_CHAT_URL, headers=headers, json=pyld, timeout=60)

        with st.chat_message(st.session_state.bot_type, avatar=assistant_avatar):
            answer = ""
            try:
                with st.spinner("AIが応答を生成中です..."):
                    res = call_dify(payload)

                    # --- 400 対策：会話IDが原因っぽいときだけ1回だけフォールバック ---
                    if res.status_code == 400 and payload.get("conversation_id"):
                        try:
                            errj = res.json()
                            emsg = (errj.get("message") or errj.get("error") or errj.get("detail") or "")
                        except Exception:
                            emsg = res.text
                        # "conversation", "invalid" 等の語を含む場合に会話IDを外して再送
                        if any(k in emsg.lower() for k in ["conversation", "invalid id", "must not be empty"]):
                            bad_cid = payload.pop("conversation_id")
                            res = call_dify(payload)
                            if res.ok:
                                st.warning(f"無効な会話IDだったため新規会話で再開しました（old={bad_cid}）")

                    res.raise_for_status()
                    rj = res.json()
                    answer = rj.get("answer", "⚠️ 応答がありませんでした。")

                    # 新規会話IDが発行されたら保存
                    new_cid = rj.get("conversation_id")
                    if new_cid and not st.session_state.cid:
                        st.session_state.cid = new_cid

                    # 応答を解析して適切に表示
                    parsed_data = parse_dify_response(answer)
                    display_parsed_response(parsed_data)

            except requests.exceptions.HTTPError as e:
                # エラーメッセージ本文をそのまま表示（原因の特定に有効）
                body_text = getattr(e.response, "text", "(レスポンスボディ取得不可)")
                st.error(f"⚠️ APIリクエストでHTTPエラーが発生しました (ステータスコード: {e.response.status_code})\n\n```\n{body_text}\n```")
                answer = f"⚠️ APIリクエストでHTTPエラーが発生しました (ステータスコード: {e.response.status_code})\n\n```\n{body_text}\n```"
            except requests.exceptions.RequestException as e:
                st.error(f"⚠️ APIリクエストで通信エラーが発生しました: {e}")
                answer = f"⚠️ APIリクエストで通信エラーが発生しました: {e}"
            except Exception as e:
                st.error(f"⚠️ 不明なエラーが発生しました: {e}")
                answer = f"⚠️ 不明なエラーが発生しました: {e}"

        # アシスタントの応答を保存
        if answer:
            # パースされたデータに基づいて表示用の内容を作成
            parsed_data = parse_dify_response(answer)
            
            if parsed_data["is_json"] and parsed_data["summaries"]:
                # JSONの場合は構造化された内容で保存
                display_content_parts = []
                for i, summary_item in enumerate(parsed_data["summaries"]):
                    if i > 0:
                        display_content_parts.append("---")
                    
                    if summary_item["title"]:
                        display_content_parts.append(f"**{summary_item['title']}**")
                    
                    if summary_item["category"]:
                        display_content_parts.append(f"カテゴリ: {summary_item['category']}")
                    
                    if summary_item["summary"]:
                        display_content_parts.append(summary_item["summary"])
                    
                    if summary_item["image_prompt"]:
                        display_content_parts.append(f"🎨 画像生成: {summary_item['image_prompt']}")
                
                display_content = "\n\n".join(display_content_parts)
            else:
                display_content = answer
            
            assistant_message = {"role": "assistant", "content": display_content, "name": st.session_state.bot_type}
            st.session_state.messages.append(assistant_message)
            save_log(
                st.session_state.cid or "(allocating...)",
                st.session_state.bot_type,
                "assistant",
                st.session_state.bot_type,
                display_content
            )

        # 画面を再実行して、共有リンクやダウンロードボタンを更新
        st.rerun()

    # --- 操作ボタン ---
    st.markdown("---")

    col1, col2 = st.columns(2)
    if col1.button("新しい会話を始める"):
        # 現在のユーザー名とボットタイプは維持しつつ、会話IDとメッセージをリセット
        st.session_state.cid = ""
        st.session_state.messages = []
        st.success("新しい会話を開始します。")
        time.sleep(1)  # メッセージ表示のためのウェイト
        st.rerun()

    if col2.button("ログアウトして最初に戻る"):
        # 全てのセッション情報をクリア
        init_session_state()
        st.rerun()

# ========== フォールバック ==========
else:
    st.error("不正なページ状態です。")
    if st.button("最初のページに戻る"):
        init_session_state()
        st.rerun()

