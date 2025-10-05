"""Cleaned app.py - unified version without merge markers.

Features:
- Persona selection (API keys via Streamlit secrets)
- Conversation resume via conversation_id (query param)
- CSV upload and attach (head(100))
- Google Sheets logging (optional, via service account in secrets)
- Chat history export: normal CSV or keyword-split CSV using utils.prepare_keyword_split_csv
"""

from datetime import datetime, timezone
import io
import json
import os
import time
from urllib.parse import urlencode

import pandas as pd
import requests
import streamlit as st

from utils import prepare_keyword_split_csv


# Constants
DIFY_CHAT_URL = "https://api.dify.ai/v1/chat-messages"

# Personas
PERSONA_NAMES = [
    "①ミノンBC理想ファン_乳児ママ_本田ゆい（30）",
    "②ミノンBC理想ファン_乳児パパ_安西涼太（31）",
    "③ミノンBC理想ファン_保育園/幼稚園ママ_戸田綾香（35）",
    "④ミノンBC理想ファン_更年期女性_高橋恵子（48）",
    "⑤ミノンBC未満ファン_乳児ママ_中村優奈（31）",
    "⑥ミノンBC未満ファン_乳児パパ_岡田健志（32）",
    "⑦ミノンBC未満ファン_保育園・幼稚園ママ_石田真帆（34）",
    "⑧ミノンBC未満ファン_更年期女性_杉山紀子（51）",
]


def get_persona_api_keys():
    keys = {}
    for i, _ in enumerate(PERSONA_NAMES):
        k = st.secrets.get(f"PERSONA_{i+1}_KEY")
        if k:
            keys[PERSONA_NAMES[i]] = k
    return keys


PERSONA_API_KEYS = get_persona_api_keys()

PERSONA_AVATARS = {PERSONA_NAMES[i]: f"persona_{i+1}.jpg" for i in range(len(PERSONA_NAMES))}


# Google Sheets helpers
def _get_sa_dict():
    if "gcp_service_account" not in st.secrets:
        return None
    raw = st.secrets["gcp_service_account"]
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            fixed = raw.replace("\r\n", "\n").replace("\n", "\\n")
            return json.loads(fixed)
    return dict(raw)


@st.cache_resource
def _gs_client():
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


@st.cache_data(ttl=60)
def load_history(conversation_id: str) -> pd.DataFrame:
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


# Streamlit UI
st.set_page_config(page_title="ミノンBC AIファンチャット", layout="centered")


def init_session_state():
    st.session_state.page = "login"
    st.session_state.cid = ""
    st.session_state.messages = []
    st.session_state.bot_type = ""
    st.session_state.user_avatar_data = None
    st.session_state.name = ""
    st.session_state.uploaded_csv_df = None
    st.session_state.uploaded_csv_name = ""
    st.session_state.attach_csv_next_message = False


if "page" not in st.session_state:
    init_session_state()


def restore_from_query_params():
    qp = st.query_params
    if qp.get("page") == "chat":
        st.session_state.page = "chat"
        st.session_state.cid = qp.get("cid", "")
        st.session_state.bot_type = qp.get("bot", "")
        st.session_state.name = qp.get("name", "")
        st.query_params.clear()
        st.rerun()


if st.session_state.page == "login" and st.query_params.get("page") == "chat":
    restore_from_query_params()


if st.session_state.page == "login":
    st.title("ミノンBC AIファンとの対話")
    if not PERSONA_API_KEYS:
        st.error("APIキーが一つも設定されていません。Streamlit CloudのSecretsに `PERSONA_1_KEY` などを設定してください。")
        st.stop()

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


elif st.session_state.page == "chat":
    st.markdown(f"#### 💬 {st.session_state.bot_type}")
    st.caption("同じ会話IDを共有すれば、複数人で同じ会話に参加できます。")

    cid_show = st.session_state.cid or "(未発行：最初の発話で採番)"
    st.info(f"会話ID: `{cid_show}`")
    if st.session_state.cid:
        params = {
            "page": "chat",
            "cid": st.session_state.cid,
            "bot": st.session_state.bot_type,
            "name": st.session_state.name,
        }
        try:
            from streamlit.web.server.server import Server
            base_url = Server.get_current()._get_base_url()
            full_url = f"https://{base_url}{st.runtime.get_script_run_ctx().page_script_hash}"
            share_link = f"{full_url}?{urlencode(params)}"
            st.code(share_link, language="text")
        except (ImportError, AttributeError):
            share_link = f"?{urlencode(params)}"
            st.code(share_link, language="text")

    assistant_avatar_file = PERSONA_AVATARS.get(st.session_state.bot_type, "default_assistant.png")
    user_avatar = st.session_state.get("user_avatar_data") if st.session_state.get("user_avatar_data") else "👤"
    assistant_avatar = assistant_avatar_file if os.path.exists(assistant_avatar_file) else "🤖"
    if assistant_avatar == "🤖":
        st.info(f"アシスタントのアバター画像（{assistant_avatar_file}）が見つかりません。カスタムアイコンを表示するには、リポジトリのルートに画像を配置してください。")

    with st.expander("CSVをアップロードしてチャットで利用する"):
        uploaded_csv = st.file_uploader("CSVファイルを選択", type=["csv"] )
        if uploaded_csv is not None:
            try:
                df = pd.read_csv(uploaded_csv)
                st.session_state.uploaded_csv_df = df
                st.session_state.uploaded_csv_name = getattr(uploaded_csv, "name", "uploaded.csv")
                st.success(f"CSVを読み込みました: {st.session_state.uploaded_csv_name} ({len(df)} 行)")
                st.dataframe(df.head(10))
                st.session_state.attach_csv_next_message = st.checkbox(
                    "次のメッセージにこのCSVの内容を含める（先頭100行まで）",
                    value=st.session_state.get("attach_csv_next_message", False)
                )
            except Exception as e:
                st.error(f"CSVの読み込みに失敗しました: {e}")
                st.session_state.uploaded_csv_df = None

    # 履歴をGoogle Sheetsから読み込んで表示
    if st.session_state.cid and not st.session_state.messages:
        history_df = load_history(st.session_state.cid)
        if not history_df.empty:
            for _, row in history_df.iterrows():
                st.session_state.messages.append({
                    "role": row["role"],
                    "content": row["content"],
                    "name": row["name"],
                })

    for msg in st.session_state.messages:
        role = msg.get("role", "")
        name = msg.get("name", role)
        avatar = assistant_avatar if role == "assistant" else user_avatar
        with st.chat_message(name, avatar=avatar):
            st.markdown(msg.get("content", ""))

    if user_input := st.chat_input("メッセージを入力してください"):
        user_message = {"role": "user", "content": user_input, "name": st.session_state.name}
        st.session_state.messages.append(user_message)
        with st.chat_message(st.session_state.name, avatar=user_avatar):
            st.markdown(user_input)

        save_log(
            st.session_state.cid or "(allocating...)",
            st.session_state.bot_type,
            "user",
            st.session_state.name,
            user_input,
        )

        api_key = PERSONA_API_KEYS.get(st.session_state.bot_type)
        if not api_key:
            st.error("選択されたペルソナのAPIキーが未設定です。")
            st.stop()

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        inputs = {}
        if st.session_state.get("attach_csv_next_message") and st.session_state.get("uploaded_csv_df") is not None:
            df = st.session_state.uploaded_csv_df
            truncated = df.head(100)
            try:
                csv_text = truncated.to_csv(index=False)
            except Exception:
                csv_text = truncated.astype(str).to_csv(index=False)
            inputs["csv"] = csv_text
            st.session_state.attach_csv_next_message = False

        payload = {
            "inputs": inputs,
            "query": user_input,
            "user": st.session_state.name,
            "conversation_id": st.session_state.cid,
            "response_mode": "blocking",
        }

        with st.chat_message(st.session_state.bot_type, avatar=assistant_avatar):
            answer = ""
            try:
                with st.spinner("AIが応答を生成中です..."):
                    res = requests.post(DIFY_CHAT_URL, headers=headers, data=json.dumps(payload), timeout=60)
                    res.raise_for_status()
                    rj = res.json()
                    answer = rj.get("answer", "⚠️ 応答がありませんでした。")

                    new_cid = rj.get("conversation_id")
                    if new_cid and not st.session_state.cid:
                        st.session_state.cid = new_cid

                    st.markdown(answer)

            except requests.exceptions.HTTPError as e:
                body = getattr(e.response, "text", "(レスポンスボディ取得不可)")
                answer = f"⚠️ APIリクエストでHTTPエラーが発生しました (ステータスコード: {e.response.status_code})\n\n```\n{body}\n```"
                st.error(answer)
            except requests.exceptions.RequestException as e:
                answer = f"⚠️ APIリクエストで通信エラーが発生しました: {e}"
                st.error(answer)
            except Exception as e:
                answer = f"⚠️ 不明なエラーが発生しました: {e}"
                st.error(answer)

        if answer:
            assistant_message = {"role": "assistant", "content": answer, "name": st.session_state.bot_type}
            st.session_state.messages.append(assistant_message)
            save_log(
                st.session_state.cid,
                st.session_state.bot_type,
                "assistant",
                st.session_state.bot_type,
                answer,
            )

        st.rerun()

    st.markdown("---")
    if st.session_state.messages:
        try:
            df_log = pd.DataFrame(st.session_state.messages)
            csv_bytes = df_log.to_csv(index=False).encode("utf-8-sig")

            st.caption("キーワード分割時の最大列数を指定（デフォルト100、上限150）")
            max_kw_ui = st.slider("最大キーワード数", min_value=1, max_value=150, value=100)

            download_format = st.radio("ダウンロード形式を選択", ("通常", "キーワード分割"), index=0, horizontal=True)

            if download_format == "通常":
                try:
                    st.caption("通常形式: role / name / content を含むプレビュー")
                    st.dataframe(df_log.head(50))
                except Exception:
                    st.write("プレビューの表示に失敗しました（通常）。")
                st.download_button("CSVをダウンロード", data=csv_bytes, file_name=f"chat_log_{st.session_state.cid}.csv", mime="text/csv")
            else:
                try:
                    csv_kw_bytes = prepare_keyword_split_csv(st.session_state.messages, max_keywords=max_kw_ui)
                    df_preview = pd.read_csv(io.BytesIO(csv_kw_bytes))
                    st.caption("キーワード分割プレビュー: assistant の content を改行で分割して keyword_1.. に配置")
                    st.dataframe(df_preview.head(50))
                    st.download_button("CSVをダウンロード（キーワード分割）", data=csv_kw_bytes, file_name=f"chat_log_keywords_{st.session_state.cid}.csv", mime="text/csv")
                except Exception as e:
                    st.warning(f"キーワード分割CSVの準備中にエラー: {e}")
        except Exception as e:
            st.warning(f"CSVダウンロードの準備中にエラー: {e}")

    col1, col2 = st.columns(2)
    if col1.button("新しい会話を始める"):
        st.session_state.cid = ""
        st.session_state.messages = []
        st.success("新しい会話を開始します。")
        time.sleep(1)
        st.rerun()

    if col2.button("ログアウトして最初に戻る"):
        init_session_state()
        st.rerun()

else:
    st.error("不正なページ状態です。")
    if st.button("最初のページに戻る"):
        init_session_state()
        st.rerun()