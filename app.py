# -*- coding: utf-8 -*-
import os
import json
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
import pandas as pd
import streamlit as st


# =========================
# Dify 設定
# =========================
DIFY_CHAT_URL = "https://api.dify.ai/v1/chat-messages"

# 既存の直書きキー（必要ならこのまま使用）
PERSONA_API_KEYS = {
    "①ミノンBC理想ファン_乳児ママ_本田ゆい（30）": "app-qNLWOMF6gJYLLzvWy6aUe3Fs",
    "②ミノンBC理想ファン_乳児パパ_安西涼太（31）": "app-2929ZbRVXV8iusFNSy4cupT5",
    "③ミノンBC理想ファン_保育園/幼稚園ママ_戸田綾香（35）": "app-7fzWdvERX8PWhhxiblrO5UY1",
    "④ミノンBC理想ファン_更年期女性_高橋恵子（48）": "app-tAw9tNFRWTiXqsmeduNEzzXX",
    "⑤ミノンBC未満ファン_乳児ママ_中村優奈（31）": "app-iGSXywEwUI5faBVTG3xRvOzU",
    "⑥ミノンBC未満ファン_乳児パパ_岡田健志（32）": "app-0fb7NSs8rWRAU3eLcY0Z7sHH",
    "⑦ミノンBC未満ファン_保育園・幼稚園ママ_石田真帆（34）": "app-3mq6c6el9Cu8H8JyULFCFInu",
    "⑧ミノンBC未満ファン_更年期女性_杉山紀子（51）": "app-3mq6c6el9Cu8H8JyULFCFInu",
}

# Secrets 側に [persona_api_keys] を置いている場合は上書き（任意）
if "persona_api_keys" in st.secrets:
    PERSONA_API_KEYS.update(dict(st.secrets["persona_api_keys"]))

# アバター
PERSONA_AVATARS = {
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
# Google Sheets 接続ユーティリティ
# =========================

def _get_sa_dict():
    """Secretsの gcp_service_account から dict を返す（JSON文字列/TOMLテーブル両対応）"""
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
    """gspread クライアントを返す"""
    import gspread
    from google.oauth2.service_account import Credentials

    sa_info = _get_sa_dict()
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
    return gspread.authorize(creds)


def _open_sheet():
    """chat_logs ワークシートを開く（なければ作成）。権限/IDエラーはUI表示して停止。"""
    import gspread
    from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound

    gc = _gs_client()
    sheet_id = st.secrets["gsheet_id"]

    try:
        sh = gc.open_by_key(sheet_id)  # ← 権限なし(403) or ID違い(404)で例外
    except SpreadsheetNotFound:
        st.error("スプレッドシートが見つかりません。Secrets の gsheet_id を確認してください。")
        st.stop()
    except PermissionError:
        sa = _get_sa_dict()
        st.error("アクセス権がありません。対象シートを下記のサービスアカウントに『編集者』で共有してください。")
        st.code(sa.get("client_email", "(unknown)"))
        st.stop()

    try:
        ws = sh.worksheet("chat_logs")
    except WorksheetNotFound:
        ws = sh.add_worksheet(title="chat_logs", rows=1000, cols=10)
        ws.append_row(["timestamp", "conversation_id", "bot_type", "role", "name", "content"])
    return ws


def save_log(conversation_id: str, bot_type: str, role: str, name: str, content: str):
    """一行追記（指数バックオフの簡易リトライ付き）"""
    import gspread
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


@st.cache_data(ttl=3)  # 軽めのライブ更新
def load_history(conversation_id: str, bot_type: str | None = None) -> pd.DataFrame:
    """会話IDに対する履歴。bot_type が指定されれば複合キーで絞る"""
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
# Streamlit UI
# =========================
st.set_page_config(page_title="Dify連携チャット（チャットフロー/グループ）", layout="centered")

# 初期化
if "page" not in st.session_state:
    st.session_state.page = "login"
    st.session_state.cid = ""
    st.session_state.messages = []
    st.session_state.bot_type = ""
    st.session_state.user_avatar_data = None
    st.session_state.name = ""

# クエリから復元（共有リンク用）
qp = st.query_params
if qp.get("cid") and not st.session_state.cid:
    st.session_state.cid = qp.get("cid")
if qp.get("bot") and not st.session_state.bot_type:
    st.session_state.bot_type = qp.get("bot")
if qp.get("name") and not st.session_state.name:
    st.session_state.name = qp.get("name")
if qp.get("page") and st.session_state.page != qp.get("page"):
    st.session_state.page = qp.get("page")


# ========== STEP 1: ログイン ==========
if st.session_state.page == "login":
    st.title("ミノンＢＣファンＡＩとチャット")

    with st.form("user_info_form"):
        name = st.text_input("あなたの表示名", value=st.session_state.name or "")

        # 共有リンク経由などで既に cid が入っていれば選択不可にする
        lock_bot = bool(st.session_state.cid)

        bot_type = st.selectbox(
            "対話するミノンＢＣファンＡＩ",
            list(PERSONA_API_KEYS.keys()),
            index=(list(PERSONA_API_KEYS.keys()).index(st.session_state.bot_type)
                   if st.session_state.bot_type in PERSONA_API_KEYS else 0),
            disabled=lock_bot,  # ← 共有IDがあるときは固定
        )
        existing_cid = st.text_input("既存の会話ID（共有リンクで参加する場合に貼付）", value=st.session_state.cid or "")
        uploaded_file = st.file_uploader("あなたのアバター画像（任意）", type=["png", "jpg", "jpeg"])
        submitted = st.form_submit_button("チャット開始")

    # フォームの外に配置（重要：st.buttonはフォーム内で使わない）
    if st.button("Google Sheets 権限チェック", key="check_perm_login"):
        try:
            from google.oauth2.service_account import Credentials
            import gspread
            sa = _get_sa_dict()
            st.write("Service Account:", sa.get("client_email"))
            st.write("gsheet_id:", st.secrets["gsheet_id"])
            creds = Credentials.from_service_account_info(sa, scopes=["https://www.googleapis.com/auth/spreadsheets"])
            gc = gspread.authorize(creds)
            gc.open_by_key(st.secrets["gsheet_id"])
            st.success("OK: シートを開けました（共有・IDともに正しい）")
        except gspread.SpreadsheetNotFound:
            st.error("NG: gsheet_id が違うか、シートが存在しません。")
        except PermissionError:
            st.error("NG: 権限がありません。上の Service Account をシートの『編集者』で共有してください。")
        except Exception as e:
            st.error(f"権限チェック中に例外: {type(e).__name__}")
            st.exception(e)

    # 送信処理
    if submitted and name:
        st.session_state.name = (name or "").strip() or "anonymous"
        st.session_state.bot_type = bot_type
        st.session_state.cid = (existing_cid or "").strip()
        if uploaded_file is not None:
            st.session_state.user_avatar_data = uploaded_file.getvalue()
        else:
            st.session_state.user_avatar_data = None

        st.session_state.messages = []
        st.query_params.update({
            "page": "chat",
            "cid": st.session_state.cid or "",
            "bot": st.session_state.bot_type,
            "name": st.session_state.name,
        })
        st.rerun()

    # 新規会話開始ボタン（任意）
    if st.button("新しい会話を始める（会話IDをリセット）", key="new_conv_login"):
        st.session_state.page = "chat"
        st.session_state.cid = ""  # 空で開始→Difyが新規IDを採番
        st.session_state.messages = []
        st.query_params.update({
            "page": "chat",
            "cid": "",
            "bot": st.session_state.bot_type or list(PERSONA_API_KEYS.keys())[0],
            "name": st.session_state.name or "anonymous",
        })
        st.rerun()


# ========== STEP 2: チャット ==========
elif st.session_state.page == "chat":
    # ---- 会話IDがある場合は、そのIDの元ペルソナに自動切替（履歴表示より前に） ----
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
            st.info("会話IDのペルソナ自動判定に失敗しました（初回や未保存時は問題ありません）。")

    st.markdown(f"#### 💬 {st.session_state.bot_type}")
    st.caption("同じ会話IDを共有すれば、全員で同じコンテキストを利用できます。")

    # 権限チェックボタン（履歴読込より前に配置）
    if st.button("Google Sheets 権限チェック", key="check_perm_chat"):
        try:
            from google.oauth2.service_account import Credentials
            import gspread
            sa = _get_sa_dict()
            st.write("Service Account:", sa.get("client_email"))
            st.write("gsheet_id:", st.secrets["gsheet_id"])
            creds = Credentials.from_service_account_info(sa, scopes=["https://www.googleapis.com/auth/spreadsheets"])
            gc = gspread.authorize(creds)
            gc.open_by_key(st.secrets["gsheet_id"])
            st.success("OK: シートを開けました（共有・IDともに正しい）")
        except gspread.SpreadsheetNotFound:
            st.error("NG: gsheet_id が違うか、シートが存在しません。")
        except PermissionError:
            st.error("NG: 権限がありません。上の Service Account をシートの『編集者』で共有してください。")
        except Exception as e:
            st.error(f"権限チェック中に例外: {type(e).__name__}")
            st.exception(e)

    # 共有リンク表示
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

    # アバター
    assistant_avatar_file = PERSONA_AVATARS.get(st.session_state.bot_type, "default_assistant.png")
    user_avatar = st.session_state.get("user_avatar_data") if st.session_state.get("user_avatar_data") else "👤"
    assistant_avatar = assistant_avatar_file if os.path.exists(assistant_avatar_file) else "🤖"

    # 履歴（Sheets）を読み込み & 表示（複合キーで絞る）
    if st.session_state.cid:
        try:
            df = load_history(st.session_state.cid, st.session_state.bot_type)
            for _, r in df.iterrows():
                avatar = assistant_avatar if r["role"] == "assistant" else user_avatar
                with st.chat_message(r["role"], avatar=avatar):
                    st.markdown(r["content"])
        except PermissionError:
            st.error("Sheetsの権限がありません。上のボタンでチェックし、権限を付与してください。")
        except Exception as e:
            st.warning(f"履歴読み込みでエラー: {type(e).__name__}")
            st.exception(e)

    # ローカル未保存分も表示
    for msg in st.session_state.messages:
        avatar = assistant_avatar if msg["role"] == "assistant" else user_avatar
        with st.chat_message(msg["role"], avatar=avatar):
            st.markdown(msg["content"])

    # 入力
    if user_input := st.chat_input("メッセージを入力してください"):
        # 画面即時反映
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user", avatar=user_avatar):
            st.markdown(user_input)

        # 永続化（user）
        try:
            save_log(st.session_state.cid or "(allocating...)", st.session_state.bot_type,
                     "user", st.session_state.name or "anonymous", user_input)
        except Exception as e:
            st.warning(f"スプレッドシート保存に失敗（user）：{e}")

        # Dify へ送信
        api_key = PERSONA_API_KEYS.get(st.session_state.bot_type)
        if not api_key:
            st.error("選択されたペルソナのAPIキーが未設定です。Secrets かコードの辞書を確認してください。")
            answer = "⚠️ APIキー未設定です。"
        else:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "inputs": {},  # 既存の conversation_id がある場合 inputs は無視される
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

                        # 新規会話IDの採番・上書き判定（重要な変更）
                        new_cid = rj.get("conversation_id")
                        if (st.session_state.cid and new_cid and new_cid != st.session_state.cid):
                            st.error(
                                "この会話IDは現在選択中のペルソナでは引き継げません。"
                                "共有元と同じペルソナ（＝同じAPIキーのアプリ）を選んでください。"
                            )
                            # 上書きしない
                        else:
                            if new_cid and not st.session_state.cid:
                                st.session_state.cid = new_cid
                                st.query_params.update({"cid": new_cid})

                        st.markdown(answer)
                except requests.exceptions.HTTPError as e:
                    body = getattr(e.response, "text", "")
                    answer = f"⚠️ HTTPエラー: {e}\n\n```\n{body}\n```"
                    st.markdown(answer)
                except Exception as e:
                    answer = f"⚠️ 不明なエラー: {e}"
                    st.markdown(answer)

        # メモリ & 永続化（assistant）
        st.session_state.messages.append({"role": "assistant", "content": answer})
        try:
            save_log(st.session_state.cid, st.session_state.bot_type, "assistant", st.session_state.bot_type, answer)
        except Exception as e:
            st.warning(f"スプレッドシート保存に失敗（assistant）：{e}")

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

# ========== フォールバック ==========
else:
    st.error("不正なページ指定です。")
    if st.button("最初のページに戻る"):
        st.session_state.page = "login"
        st.session_state.cid = ""
        st.query_params.clear()
        st.rerun()
