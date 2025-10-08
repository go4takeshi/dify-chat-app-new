# -*- coding: utf-8 -*-
# Dify 連携チャット（Streamlit）- クリーン完全版
# -----------------------------------------------------------------------------
# 事前準備（.streamlit/secrets.toml の例） ※このコメントはそのままにしてください（引用符は使いません）
# [persona_api_keys]
# "①ミノンBC理想ファン_乳児ママ_本田ゆい（30）" = "app-xxxxxxxxxxxxxxxx"
# "②ミノンBC理想ファン_乳児パパ_安西涼太（31）" = "app-yyyyyyyyyyyyyyyy"
# # …必要なペルソナ分（← 表示名は日本語OK / 値はDifyアプリAPIキー）
# gcp_service_account = { ... サービスアカウント JSON ... }  # TOMLテーブル or JSON文字列
# gsheet_id = "1AbCdEfGhIj..."
# max_input_chars = 4000  # 任意（0=無効）
# -----------------------------------------------------------------------------

import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, Optional
from urllib.parse import urlencode

import pandas as pd
import requests
import streamlit as st

# =========================
# Constants & Secrets
# =========================
DIFY_CHAT_URL = "https://api.dify.ai/v1/chat-messages"

# 必須 Secrets チェック
if "persona_api_keys" not in st.secrets:
    st.error("Secrets に persona_api_keys がありません。 .streamlit/secrets.toml を設定してください。")
    st.stop()
if "gcp_service_account" not in st.secrets:
    st.error("Secrets に gcp_service_account がありません（サービスアカウントJSON）。")
    st.stop()
if "gsheet_id" not in st.secrets:
    st.error("Secrets に gsheet_id がありません（スプレッドシートID）。")
    st.stop()

PERSONA_API_KEYS: Dict[str, str] = dict(st.secrets["persona_api_keys"])  # UI表示名: DifyアプリAPIキー
GSHEET_ID: str = st.secrets["gsheet_id"]
MAX_INPUT_CHARS: int = int(st.secrets.get("max_input_chars", 0))

# UI アバター（公開ファイル名）
PERSONA_AVATARS: Dict[str, str] = {
    "①ミノンBC理想ファン_乳児ママ_本田ゆい（30）": "persona_1.jpg",
    "②ミノンBC理想ファン_乳児パパ_安西涼太（31）": "persona_2.jpg",
    "③ミノンBC理想ファン_保育園/幼稚園ママ_戸田綾香（35）": "persona_3.jpg",
    "④ミノンBC理想ファン_更年期女性_高橋恵子（48）": "persona_4.jpg",
    "⑤ミノンBC未満ファン_乳児ママ_中村優奈（31）": "persona_5.jpg",
    "⑥ミノンBC未満ファン_乳児パパ_岡田健志（32）": "persona_6.jpg",
    "⑦ミノンBC未満ファン_保育園・幼稚園ママ_石田真帆（34）": "persona_7.png",
    "⑧ミノンBC未満ファン_更年期女性_杉山紀子（51）": "persona_8.jpg",
}

# =========================
# Google Sheets Utilities
# =========================
def _get_sa_dict() -> dict:
    # Secrets の gcp_service_account を dict で返す（JSON文字列/TOMLテーブル両対応）
    raw = st.secrets["gcp_service_account"]
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # private_key の実改行を \n に自動補正して再トライ（貼付ミス救済）
            fixed = raw.replace("\r\n", "\n").replace("\n", "\\n")
            return json.loads(fixed)
    return raw

def _gs_client():
    import gspread
    from google.oauth2.service_account import Credentials

    sa_info = _get_sa_dict()
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
    return gspread.authorize(creds)

def _open_sheet():
    import gspread
    from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound

    gc = _gs_client()
    try:
        sh = gc.open_by_key(GSHEET_ID)
    except SpreadsheetNotFound:
        st.error("スプレッドシートが見つかりません。Secrets の gsheet_id を確認してください。")
        st.stop()
    except PermissionError:
        st.error("アクセス権がありません。対象シートを Service Account に『編集者』で共有してください。")
        st.stop()

    try:
        ws = sh.worksheet("chat_logs")
    except WorksheetNotFound:
        ws = sh.add_worksheet(title="chat_logs", rows=1000, cols=10)
        ws.append_row(["timestamp", "conversation_id", "bot_type", "role", "name", "content"])
    return ws

def save_log(conversation_id: str, bot_type: str, role: str, name: str, content: str) -> None:
    # 1行追記（APIの一時的エラーに対して指数バックオフ付きで再試行）
    from gspread.exceptions import APIError

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

@st.cache_data(ttl=3)
def load_history(conversation_id: str, bot_type: Optional[str] = None) -> pd.DataFrame:
    # 会話IDの履歴を読み込み。bot_type 指定時は複合キーで絞込。
    ws = _open_sheet()
    data = ws.get_all_records()
    df = pd.DataFrame(data)
    if df.empty:
        return df
    df = df[df["conversation_id"] == conversation_id].copy()
    if bot_type is not None and "bot_type" in df.columns:
        df = df[df["bot_type"] == bot_type].copy()
    if not df.empty and "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
        df = df.sort_values("timestamp")
    return df

# =========================
# Streamlit App
# =========================
st.set_page_config(page_title="Dify連携チャット（チャットフロー/グループ）", layout="centered")

# --- State init ---
if "page" not in st.session_state:
    st.session_state.page = "login"
    st.session_state.cid = ""
    st.session_state.messages = []  # CID 未確定時だけ使う一時バッファ
    st.session_state.bot_type = ""
    st.session_state.user_avatar_data = None
    st.session_state.name = ""

# --- Restore from query (share link) ---
qp = st.query_params
if qp.get("cid") and not st.session_state.cid:
    st.session_state.cid = qp.get("cid")
if qp.get("bot") and not st.session_state.bot_type:
    st.session_state.bot_type = qp.get("bot")
if qp.get("name") and not st.session_state.name:
    st.session_state.name = qp.get("name")
if qp.get("page") and st.session_state.page != qp.get("page"):
    st.session_state.page = qp.get("page")

# ========== STEP 1: LOGIN ==========
if st.session_state.page == "login":
    st.title("ミノンＢＣファンＡＩとチャット")

    with st.form("user_info_form"):
        name = st.text_input("あなたの表示名", value=st.session_state.name or "")
        lock_bot = bool(st.session_state.cid)  # 共有CIDがあるならボット選択はロック
        persona_choices = list(PERSONA_API_KEYS.keys())
        if not persona_choices:
            st.error("persona_api_keys が空です。Secrets を確認してください。")
            st.stop()
        bot_type = st.selectbox(
            "対話するミノンＢＣファンＡＩ",
            persona_choices,
            index=(persona_choices.index(st.session_state.bot_type)
                   if st.session_state.bot_type in PERSONA_API_KEYS else 0),
            disabled=lock_bot,
        )
        existing_cid = st.text_input("既存の会話ID（共有リンクで参加する場合に貼付）", value=st.session_state.cid or "")
        uploaded_file = st.file_uploader("あなたのアバター画像（任意）", type=["png", "jpg", "jpeg"])
        submitted = st.form_submit_button("チャット開始")

    # 権限チェック（キー文字列は表示しない）
    if st.button("Google Sheets 権限チェック", key="check_perm_login"):
        try:
            import gspread
            from google.oauth2.service_account import Credentials

            creds = Credentials.from_service_account_info(_get_sa_dict(), scopes=["https://www.googleapis.com/auth/spreadsheets"])
            gc = gspread.authorize(creds)
            gc.open_by_key(GSHEET_ID)
            st.success("OK: シートを開けました（共有・IDともに正しい）")
        except gspread.SpreadsheetNotFound:
            st.error("NG: gsheet_id が違うか、シートが存在しません。")
        except PermissionError:
            st.error("NG: 権限がありません。Service Account を『編集者』で共有してください。")
        except Exception as e:
            st.error(f"権限チェック中に例外: {type(e).__name__}")
            st.exception(e)

    if submitted and name:
        st.session_state.name = (name or "").strip() or "anonymous"
        st.session_state.bot_type = bot_type
        st.session_state.cid = (existing_cid or "").strip()
        st.session_state.user_avatar_data = uploaded_file.getvalue() if uploaded_file else None
        st.session_state.messages = []
        st.query_params.update({
            "page": "chat",
            "cid": st.session_state.cid or "",
            "bot": st.session_state.bot_type,
            "name": st.session_state.name,
        })
        st.rerun()

    if st.button("新しい会話を始める（会話IDをリセット）", key="new_conv_login"):
        st.session_state.page = "chat"
        st.session_state.cid = ""  # 空で開始→Dify が新規CIDを採番
        st.session_state.messages = []
        st.query_params.update({
            "page": "chat",
            "cid": "",
            "bot": st.session_state.bot_type or list(PERSONA_API_KEYS.keys())[0],
            "name": st.session_state.name or "anonymous",
        })
        st.rerun()

# ========== STEP 2: CHAT ==========
elif st.session_state.page == "chat":
    # 共有CIDが指定されている場合、そのCIDの主ペルソナに自動切替（履歴表示前）
    if st.session_state.cid:
        try:
            df_any = load_history(st.session_state.cid, bot_type=None)
            if not df_any.empty and "bot_type" in df_any.columns:
                series = df_any["bot_type"].dropna()
                if not series.empty:
                    cid_bot = series.mode().iloc[0]
                    if cid_bot and st.session_state.bot_type != cid_bot:
                        st.warning(f"この会話IDは『{cid_bot}』で作成されています。ペルソナを合わせます。")
                        st.session_state.bot_type = cid_bot
                        st.query_params.update({"bot": cid_bot})
        except Exception:
            st.info("会話IDのペルソナ自動判定に失敗（初回や未保存時は問題ありません）。")

    st.markdown(f"#### 💬 {st.session_state.bot_type}")
    st.caption("同じ会話IDを共有すれば、全員で同じコンテキストを利用できます。")

    # 権限チェック（キーは表示しない）
    if st.button("Google Sheets 権限チェック", key="check_perm_chat"):
        try:
            import gspread
            from google.oauth2.service_account import Credentials

            creds = Credentials.from_service_account_info(_get_sa_dict(), scopes=["https://www.googleapis.com/auth/spreadsheets"])
            gc = gspread.authorize(creds)
            gc.open_by_key(GSHEET_ID)
            st.success("OK: シートを開けました（共有・IDともに正しい）")
        except gspread.SpreadsheetNotFound:
            st.error("NG: gsheet_id が違うか、シートが存在しません。")
        except PermissionError:
            st.error("NG: 権限がありません。Service Account を『編集者』で共有してください。")
        except Exception as e:
            st.error(f"権限チェック中に例外: {type(e).__name__}")
            st.exception(e)

    # 共有リンク
    cid_show = st.session_state.cid or "(未発行：最初の発話で採番されます)"
    st.info(f"会話ID: `{cid_show}`")
    if st.session_state.cid:
        params = {
            "page": "chat",
            "cid": st.session_state.cid,
            "bot": st.session_state.bot_type,
            "name": st.session_state.name,
        }
        share_link = f"?{urlencode(params)}"
        st.code(share_link, language="text")
        st.link_button("共有リンクを開く", share_link)

    # 表示オプション
    show_all_bots = st.checkbox("この会話IDの全ペルソナ履歴を表示する", value=False)

    # アバター
    assistant_avatar_file = PERSONA_AVATARS.get(st.session_state.bot_type, "default_assistant.png")
    user_avatar = st.session_state.get("user_avatar_data") if st.session_state.get("user_avatar_data") else "👤"
    assistant_avatar = assistant_avatar_file if os.path.exists(assistant_avatar_file) else "🤖"

    # 履歴表示（CID 確定時は Sheets のみを信頼）
    if st.session_state.cid:
        try:
            df = load_history(st.session_state.cid, None if show_all_bots else st.session_state.bot_type)
            for _, r in df.iterrows():
                row_bot = r.get("bot_type") or st.session_state.bot_type
                row_av_file = PERSONA_AVATARS.get(row_bot, "default_assistant.png")
                row_assistant_avatar = row_av_file if os.path.exists(row_av_file) else "🤖"

                avatar = row_assistant_avatar if r["role"] == "assistant" else user_avatar
                with st.chat_message(r["role"], avatar=avatar):
                    st.markdown(r["content"])
        except PermissionError:
            st.error("Sheets の権限がありません。上のボタンでチェックしてください。")
        except Exception as e:
            st.warning(f"履歴読み込みでエラー: {type(e).__name__}")
            st.exception(e)

    # CID 未確定時のみローカルバッファを表示（重複防止）
    if not st.session_state.cid:
        for msg in st.session_state.messages:
            avatar = assistant_avatar if msg["role"] == "assistant" else user_avatar
            with st.chat_message(msg["role"], avatar=avatar):
                st.markdown(msg["content"])

    # 入力
    if user_input := st.chat_input("メッセージを入力してください"):
        # 入力長ガード（任意）
        if MAX_INPUT_CHARS and len(user_input) > MAX_INPUT_CHARS:
            st.error(f"入力が長すぎます（最大 {MAX_INPUT_CHARS} 文字）。短くしてください。")
        else:
            is_new_thread = not bool(st.session_state.cid)

            # ユーザー発話の即時描画
            if is_new_thread:
                # 新規スレッドは保存せずローカル表示に留める（後でCID確定後に保存）
                st.session_state.messages.append({"role": "user", "content": user_input})
            else:
                # 既存スレッドはすぐに保存してよい（重複しない）
                try:
                    save_log(st.session_state.cid, st.session_state.bot_type, "user", st.session_state.name or "anonymous", user_input)
                except Exception as e:
                    st.warning(f"スプレッドシート保存に失敗（user）：{e}")
            with st.chat_message("user", avatar=user_avatar):
                st.markdown(user_input)

            # Dify へ送信
            api_key = PERSONA_API_KEYS.get(st.session_state.bot_type)
            if not api_key:
                st.error("選択されたペルソナのAPIキーが Secrets に見つかりません。管理者に確認してください。")
                answer = "⚠️ APIキー未設定です。"
            else:
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                payload = {
                    "inputs": {},
                    "query": user_input,
                    "user": st.session_state.name or "streamlit-user",
                    "conversation_id": st.session_state.cid,
                    "response_mode": "blocking",
                }
                with st.chat_message("assistant", avatar=assistant_avatar):
                    try:
                        with st.spinner("AIが応答を生成中です…"):
                            res = requests.post(DIFY_CHAT_URL, headers=headers, data=json.dumps(payload), timeout=60)
                            res.raise_for_status()
                            rj = res.json()
                            answer = rj.get("answer", "⚠️ 応答がありませんでした。")

                            # 新規CIDの採番（別アプリCIDの誤上書きは拒否）
                            new_cid = rj.get("conversation_id")
                            if st.session_state.cid and new_cid and new_cid != st.session_state.cid:
                                st.error("この会話IDは現在のペルソナでは引き継げません。共有元と同じペルソナを選んでください。")
                            else:
                                if new_cid and not st.session_state.cid:
                                    st.session_state.cid = new_cid
                                    st.query_params.update({"cid": new_cid})

                            # 初回ユーザー発話の遅延保存（CID確定後）
                            if is_new_thread:
                                try:
                                    save_log(st.session_state.cid or new_cid, st.session_state.bot_type, "user", st.session_state.name or "anonymous", user_input)
                                except Exception as e:
                                    st.warning(f"初回ユーザー発話の保存に失敗：{e}")

                            st.markdown(answer)
                    except requests.exceptions.HTTPError as e:
                        body = getattr(e.response, "text", "")
                        answer = f"⚠️ HTTPエラー: {e}\n\n```\n{body}\n```"
                        st.markdown(answer)
                    except Exception as e:
                        answer = f"⚠️ 不明なエラー: {e}"
                        st.markdown(answer)

            # アシスタント発話の保存
            try:
                save_log(st.session_state.cid, st.session_state.bot_type, "assistant", st.session_state.bot_type, answer)
            except Exception as e:
                st.warning(f"スプレッドシート保存に失敗（assistant）：{e}")

            # 重複防止：CID確定後はローカルバッファをクリア
            if st.session_state.cid:
                st.session_state.messages.clear()

    # 操作ボタン
    col1, col2, col3 = st.columns(3)
    if col1.button("履歴を再読込"):
        st.cache_data.clear()
        st.rerun()
    if col2.button("この会話を終了（新規IDで再開）"):
        st.session_state.cid = ""
        st.session_state.messages = []
        st.query_params.update({"cid": ""})
        st.success("会話IDをリセットしました。次の送信で新規IDが採番されます。")
    if col3.button("ログアウト"):
        st.session_state.page = "login"
        st.session_state.messages = []
        st.query_params.clear()
        st.rerun()

# ========== Fallback ==========
else:
    st.error("不正なページ指定です。")
    if st.button("最初のページに戻る"):
        st.session_state.page = "login"
        st.session_state.cid = ""
        st.query_params.clear()
        st.rerun()
