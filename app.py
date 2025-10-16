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
            return None, None
            
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
        image_bytes = img_response.content
        
        return image, image_bytes
        
    except Exception as e:
        st.error(f"画像生成中にエラーが発生しました: {e}")
        return None, None

def generate_image_id():
    """画像の整理番号を生成（YYYY-MM-DD-HHMMSS-XXX形式）"""
    from datetime import datetime
    import random
    
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d-%H%M%S")
    random_suffix = f"{random.randint(100, 999):03d}"
    return f"{timestamp}-{random_suffix}"

def save_image_to_drive(image_bytes, image_id, prompt, conversation_id):
    """画像をGoogle Driveに保存"""
    try:
        drive_service = _drive_service()
        if not drive_service:
            return None, "Google Drive サービスが利用できません"
        
        # フォルダ確認・作成
        folder_name = "MinonBC_AI_Images"
        folder_id = get_or_create_drive_folder(drive_service, folder_name)
        
        if not folder_id:
            return None, "フォルダの作成に失敗しました"
        
        # ファイル名を作成
        filename = f"{image_id}_image.jpg"
        
        # メタデータを設定
        file_metadata = {
            'name': filename,
            'parents': [folder_id],
            'description': f'AI Generated Image\nPrompt: {prompt}\nConversation ID: {conversation_id}'
        }
        
        # 画像をアップロード
        from googleapiclient.http import MediaIoBaseUpload
        media = MediaIoBaseUpload(io.BytesIO(image_bytes), mimetype='image/jpeg')
        
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id,webViewLink,webContentLink'
        ).execute()
        
        return file.get('id'), file.get('webViewLink')
        
    except Exception as e:
        return None, f"Google Drive保存エラー: {e}"

def get_or_create_drive_folder(drive_service, folder_name):
    """Google Driveでフォルダを取得または作成"""
    try:
        # 既存フォルダを検索
        results = drive_service.files().list(
            q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder'",
            fields="files(id, name)"
        ).execute()
        
        folders = results.get('files', [])
        
        if folders:
            return folders[0]['id']
        
        # フォルダが存在しない場合は作成
        folder_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        
        folder = drive_service.files().create(
            body=folder_metadata,
            fields='id'
        ).execute()
        
        return folder.get('id')
        
    except Exception as e:
        st.error(f"フォルダ操作エラー: {e}")
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
                    generated_image, image_bytes = generate_image_with_dalle3(summary_item["image_prompt"])
                    
                if generated_image and image_bytes:
                    st.image(generated_image, caption=f"生成画像: {summary_item['image_prompt'][:50]}...", use_container_width=True)
                    
                    # Google Driveに画像を保存
                    if st.secrets.get("gcp_service_account") and st.secrets.get("gsheet_id"):
                        with st.spinner("Google Driveに画像を保存しています..."):
                            image_id = generate_image_id()
                            drive_file_id, drive_link_or_error = save_image_to_drive(
                                image_bytes, 
                                image_id, 
                                summary_item["image_prompt"],
                                st.session_state.get("cid", "unknown")
                            )
                            
                            if drive_file_id:
                                st.success(f"✅ **画像をGoogle Driveに保存しました**")
                                st.info(f"**整理番号:** `{image_id}`")
                                if drive_link_or_error:
                                    st.markdown(f"🔗 [Google Driveで表示]({drive_link_or_error})")
                                
                                # 画像情報をGoogle Sheetsに記録
                                save_log(
                                    st.session_state.get("cid", "unknown"),
                                    st.session_state.get("bot_type", "unknown"),
                                    "system",
                                    "image_save",
                                    f"画像保存: {summary_item['image_prompt'][:100]}...",
                                    image_id,
                                    drive_file_id,
                                    drive_link_or_error or ""
                                )
                                
                                # セッション状態に画像情報を保存（再表示用）
                                if "saved_images" not in st.session_state:
                                    st.session_state.saved_images = []
                                st.session_state.saved_images.append({
                                    "image_id": image_id,
                                    "drive_link": drive_link_or_error,
                                    "prompt": summary_item["image_prompt"]
                                })
                            else:
                                st.error(f"❌ 画像保存に失敗しました: {drive_link_or_error}")
                    else:
                        st.info("💡 Google Drive保存機能が無効です。SecretsにGoogle認証情報を設定すると自動保存されます。")
                        
                else:
                    st.error("画像の生成に失敗しました。")
    else:
        # 通常のテキストの場合
        st.markdown(parsed_data["raw_text"])

# =========================
# Google Sheets & Google Drive 接続ユーティリティ
# =========================
def _get_sa_dict():
    """Secretsの gcp_service_account から dict を返す（JSON文字列/TOMLテーブル両対応）"""
    if "gcp_service_account" not in st.secrets:
        return None
    try:
        raw = st.secrets["gcp_service_account"]
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                # private_key の実改行を \n に自動補正して再トライ（貼付ミス救済）
                fixed = raw.replace("\r\n", "\n").replace("\n", "\\n")
                return json.loads(fixed)
        return dict(raw)
    except Exception as e:
        if st.secrets.get("DEBUG_MODE", False):
            st.error(f"サービスアカウント情報の読み込みエラー: {e}")
        return None

@st.cache_resource
def _gs_client():
    """gspread クライアントを返す（キャッシュする）"""
    import gspread
    from google.oauth2.service_account import Credentials

    sa_info = _get_sa_dict()
    if not sa_info:
        st.error("`gcp_service_account` がSecretsに設定されていません。")
        st.stop()

    # Google SheetsとGoogle Driveの両方にアクセスするためのスコープ
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file"
    ]
    creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
    return gspread.authorize(creds)

@st.cache_resource
def _drive_service():
    """Google Drive API サービスを返す（キャッシュする）"""
    from googleapiclient.discovery import build
    from google.oauth2.service_account import Credentials

    sa_info = _get_sa_dict()
    if not sa_info:
        return None

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file"
    ]
    creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
    return build('drive', 'v3', credentials=creds)

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
        # 既存のシートに新しいカラムが必要かチェック
        headers = ws.row_values(1)
        if "image_id" not in headers:
            # 新しいカラムを追加
            current_cols = len(headers)
            ws.update_cell(1, current_cols + 1, "image_id")
            ws.update_cell(1, current_cols + 2, "drive_file_id")
            ws.update_cell(1, current_cols + 3, "drive_link")
    except WorksheetNotFound:
        ws = sh.add_worksheet(title="chat_logs", rows=1000, cols=12)
        ws.append_row(["timestamp", "conversation_id", "bot_type", "role", "name", "content", "image_id", "drive_file_id", "drive_link"])
    return ws

def save_log(conversation_id: str, bot_type: str, role: str, name: str, content: str, image_id: str = "", drive_file_id: str = "", drive_link: str = ""):
    """一行追記（指数バックオフの簡易リトライ付き）"""
    from gspread.exceptions import APIError
    
    # Google Sheets機能が無効な場合はスキップ
    if "gcp_service_account" not in st.secrets or "gsheet_id" not in st.secrets:
        return

    try:
        ws = _open_sheet()
        row = [datetime.now(timezone.utc).isoformat(), conversation_id, bot_type, role, name, content, image_id, drive_file_id, drive_link]

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
        # より詳細なエラー情報を表示
        error_type = type(e).__name__
        st.warning(f"Google Sheetsへのログ保存中にエラーが発生しました ({error_type}): {e}")
        # デバッグ用（開発時のみ）
        if st.secrets.get("DEBUG_MODE", False):
            st.error(f"詳細エラー: {e}", icon="🐛")

@st.cache_data(ttl=60)  # ライブ更新のため短めのTTL
def load_history(conversation_id: str) -> pd.DataFrame:
    """指定された会話IDの履歴をGoogle Sheetsから読み込む"""
    # Google Sheets機能が無効な場合は空のDataFrameを返す
    if "gcp_service_account" not in st.secrets or "gsheet_id" not in st.secrets:
        return pd.DataFrame(columns=["timestamp", "conversation_id", "bot_type", "role", "name", "content", "image_id", "drive_file_id", "drive_link"])
        
    try:
        ws = _open_sheet()
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["timestamp", "conversation_id", "bot_type", "role", "name", "content", "image_id", "drive_file_id", "drive_link"])

        df_filtered = df[df["conversation_id"] == conversation_id].copy()
        if not df_filtered.empty and "timestamp" in df_filtered.columns:
            df_filtered["timestamp"] = pd.to_datetime(df_filtered["timestamp"], errors="coerce", utc=True)
            df_filtered = df_filtered.sort_values("timestamp")
        return df_filtered
    except Exception as e:
        error_type = type(e).__name__
        st.warning(f"Google Sheetsからの履歴読み込み中にエラーが発生しました ({error_type}): {e}")
        return pd.DataFrame(columns=["timestamp", "conversation_id", "bot_type", "role", "name", "content", "image_id", "drive_file_id", "drive_link"])

# =========================
# Streamlit UI
# =========================
st.set_page_config(page_title="ミノンBC AIファンチャット", layout="centered")

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
    st.title("ひらめ１号との対話")

    # APIキーが一つも設定されていない場合はエラー表示
    if not PERSONA_API_KEYS:
        st.error("APIキーが一つも設定されていません。Streamlit CloudのSecretsに `PERSONA_1_KEY` などを設定してください。")
        st.stop()
    
    # OpenAI APIキーの確認
    if not st.secrets.get("OPENAI_API_KEY"):
        st.warning("⚠️ OpenAI APIキーが設定されていません。画像生成機能を使用するには、Streamlit CloudのSecretsに `OPENAI_API_KEY` を設定してください。")
    
    # Google Sheets設定の確認
    if not st.secrets.get("gcp_service_account") or not st.secrets.get("gsheet_id"):
        st.info("💡 Google Sheets設定が不完全です。チャット履歴の永続化機能は無効になります。")
    
    # デバッグ情報の表示（Google API設定確認用）
    if st.secrets.get("gcp_service_account") and st.secrets.get("gsheet_id"):
        with st.expander("🔧 Google API設定確認", expanded=False):
            try:
                sa_info = json.loads(st.secrets["gcp_service_account"]) if isinstance(st.secrets["gcp_service_account"], str) else dict(st.secrets["gcp_service_account"])
                st.write("**サービスアカウント email:**")
                st.code(sa_info.get("client_email", "不明"))
                st.write("**プロジェクトID:**")
                st.code(sa_info.get("project_id", "不明"))
                st.write("**スプレッドシートID:**")
                st.code(st.secrets["gsheet_id"])
                
                st.markdown("### 📋 必要な設定")
                st.info("1️⃣ **Google Cloud Console**でAPI有効化:\n- Google Sheets API ✅\n- Google Drive API ✅")
                st.info("2️⃣ **スプレッドシート共有**:\n上記のサービスアカウントemailを「編集者」権限で共有")
                st.info("3️⃣ **Google Drive権限**:\nサービスアカウントが画像保存用フォルダを作成できます")
                
                # API接続テスト
                st.markdown("### 🔍 API接続テスト")
                if st.button("Google Sheets API テスト"):
                    try:
                        ws = _open_sheet()
                        st.success("✅ Google Sheets API: 接続成功")
                    except Exception as e:
                        st.error(f"❌ Google Sheets API: 接続失敗 - {e}")
                        
                if st.button("Google Drive API テスト"):
                    try:
                        drive_service = _drive_service()
                        if drive_service:
                            # 簡単なテスト（フォルダ検索）
                            results = drive_service.files().list(
                                q="mimeType='application/vnd.google-apps.folder'",
                                pageSize=1,
                                fields="files(id, name)"
                            ).execute()
                            st.success("✅ Google Drive API: 接続成功")
                        else:
                            st.error("❌ Google Drive API: サービス取得失敗")
                    except Exception as e:
                        st.error(f"❌ Google Drive API: 接続失敗 - {e}")
                        
            except Exception as e:
                st.error(f"サービスアカウント情報の読み取りエラー: {e}")
    
    # JSON出力フォーマットの説明
    with st.expander("📖 Dify出力フォーマットについて", expanded=False):
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
