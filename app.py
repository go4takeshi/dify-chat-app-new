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
def should_generate_image(user_input, bot_response):
    """ユーザーの入力に画像生成の指示が含まれているかチェック"""
    image_keywords = [
        "画像にして", "画像を生成", "画像を作って", "イメージにして", "絵にして",
        "図にして", "ビジュアル化", "画像で表現", "画像化", "絵で表現",
        # 追加キーワード
        "描いて", "イラストにして", "チャートにして", "グラフにして", "写真風に",
        "アートにして", "スケッチにして", "デザインして"
    ]
    
    # ユーザー入力に画像生成キーワードが含まれているかチェック
    for keyword in image_keywords:
        if keyword in user_input:
            return True
    return False

def parse_image_specifications(user_input):
    """ユーザー入力から画像スタイルとサイズの指定を解析"""
    specifications = {
        "style": "professional",  # デフォルト
        "size": "1024x1024"      # デフォルト
    }
    
    # スタイル指定の解析（負荷：文字列検索のみ）
    style_patterns = {
        "シンプル": "minimalist",
        "ミニマル": "minimalist", 
        "写真風": "photorealistic",
        "アート": "artistic",
        "スケッチ": "sketch",
        "チャート": "chart",
        "グラフ": "diagram",
        "ビジネス": "business"
    }
    
    for japanese, english in style_patterns.items():
        if japanese in user_input:
            specifications["style"] = english
            break
    
    # サイズ指定の解析（負荷：文字列検索のみ）
    if "小さ" in user_input or "小さめ" in user_input:
        specifications["size"] = "512x512"
    elif "大き" in user_input or "大きめ" in user_input:
        specifications["size"] = "1792x1024"
    elif "正方形" in user_input:
        specifications["size"] = "1024x1024"
    elif "横長" in user_input:
        specifications["size"] = "1792x1024"
    elif "縦長" in user_input:
        specifications["size"] = "1024x1792"
    
    return specifications

def create_image_prompt_from_text(text_content, style="professional"):
    """テキスト内容から画像生成用のプロンプトを作成"""
    # テキストの長さを制限（DALL-E 3のプロンプト制限対応）
    if len(text_content) > 300:
        text_content = text_content[:300] + "..."
    
    # スタイル別のプロンプトテンプレート（負荷：辞書検索のみ）
    style_templates = {
        "minimalist": "Create a clean, minimalist illustration with simple lines and minimal colors that represents: {content}",
        "photorealistic": "Create a photorealistic image that accurately depicts: {content}",
        "artistic": "Create an artistic, creative illustration with vibrant colors that represents: {content}",
        "sketch": "Create a hand-drawn sketch style illustration that shows: {content}",
        "chart": "Create a professional chart or diagram that visualizes: {content}",
        "diagram": "Create a clear, professional diagram that explains: {content}",
        "business": "Create a professional business presentation style illustration for: {content}",
        "professional": "Create a professional, modern illustration that visually represents: {content}. Style: Clean, professional design with clear visual metaphors. Use bright, engaging colors."
    }
    
    # スタイルに応じたプロンプト生成（負荷：文字列フォーマットのみ）
    template = style_templates.get(style, style_templates["professional"])
    prompt = template.format(content=text_content)
    
    return prompt

def generate_image_with_dalle3(prompt, size="1024x1024"):
    """DALL-E 3を使用して画像を生成"""
    try:
        client = get_openai_client()
        if not client:
            return None, None
            
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size=size,  # サイズ指定を反映
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
        st.info("🔍 Google Drive サービスを初期化中...")
        drive_service = _drive_service()
        if not drive_service:
            return None, "Google Drive サービスが利用できません"
        
        st.info("📁 フォルダを確認・作成中...")
        # フォルダ確認・作成（Secretsから取得、未設定時はデフォルト値を使用）
        folder_name = st.secrets.get("drive_folder_name", "MinonBC_AI_Images")
        folder_id = get_or_create_drive_folder(drive_service, folder_name)
        
        if not folder_id:
            return None, "フォルダの作成に失敗しました"
        
        st.info(f"📁 フォルダID: {folder_id}")
        
        # ファイル名を作成
        filename = f"{image_id}_image.jpg"
        st.info(f"📄 ファイル名: {filename}")
        
        # メタデータを設定
        file_metadata = {
            'name': filename,
            'parents': [folder_id],
            'description': f'AI Generated Image\nPrompt: {prompt}\nConversation ID: {conversation_id}'
        }
        
        # 画像をアップロード
        try:
            st.info("⬆️ Google Driveにアップロード中...")
            from googleapiclient.http import MediaIoBaseUpload
            media = MediaIoBaseUpload(io.BytesIO(image_bytes), mimetype='image/jpeg')
            
            # 共有ドライブIDが設定されているかチェック
            shared_drive_id = st.secrets.get("shared_drive_id")
            
            if shared_drive_id:
                # 共有ドライブ対応でアップロード
                file = drive_service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id,webViewLink,webContentLink',
                    supportsAllDrives=True
                ).execute()
            else:
                # 従来の個人ドライブ方式
                file = drive_service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id,webViewLink,webContentLink'
                ).execute()
            
            file_id = file.get('id')
            web_view_link = file.get('webViewLink')
            st.info(f"✅ アップロード成功 - ファイルID: {file_id}")
            
            return file_id, web_view_link
        except ImportError as e:
            error_msg = f"Google API Client ライブラリが不足しています: {e}"
            st.error(error_msg)
            return None, error_msg
        except Exception as upload_error:
            error_msg = f"アップロードエラー: {upload_error}"
            st.error(error_msg)
            return None, error_msg
        
    except Exception as e:
        error_msg = f"Google Drive保存エラー: {e}"
        st.error(error_msg)
        return None, error_msg

def get_or_create_drive_folder(drive_service, folder_name):
    """Google Driveでフォルダを取得または作成（共有ドライブ対応）"""
    try:
        st.info(f"🔍 フォルダ '{folder_name}' を検索中...")
        
        # 共有ドライブIDがSecretsに設定されているかチェック
        shared_drive_id = st.secrets.get("shared_drive_id")
        
        if shared_drive_id:
            st.info(f"📁 共有ドライブを使用: {shared_drive_id}")
            try:
                # 共有ドライブ内でフォルダを検索
                results = drive_service.files().list(
                    q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and parents in '{shared_drive_id}'",
                    fields="files(id, name)",
                    driveId=shared_drive_id,
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                    corpora='drive'
                ).execute()
                
                folders = results.get('files', [])
                st.info(f"📁 検索結果: {len(folders)}個のフォルダが見つかりました")
                
                if folders:
                    folder_id = folders[0]['id']
                    st.info(f"✅ 既存フォルダを使用: {folder_id}")
                    return folder_id
                
                # フォルダが存在しない場合は共有ドライブ内に作成
                st.info("📁 共有ドライブ内に新しいフォルダを作成中...")
                folder_metadata = {
                    'name': folder_name,
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [shared_drive_id]
                }
                
                folder = drive_service.files().create(
                    body=folder_metadata,
                    fields='id',
                    supportsAllDrives=True
                ).execute()
                
                folder_id = folder.get('id')
                st.success(f"✅ 共有ドライブ内にフォルダを作成: {folder_id}")
                return folder_id
                
            except Exception as shared_drive_error:
                st.error(f"⚠️ 共有ドライブエラー: {shared_drive_error}")
                st.warning("個人ドライブでの保存を試行します...")
                # 共有ドライブでエラーの場合、個人ドライブにフォールバック
        
        # 個人ドライブでの処理（フォールバック）
        st.info("📁 個人ドライブでフォルダを検索中...")
        
        # 既存フォルダを検索
        results = drive_service.files().list(
            q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder'",
            fields="files(id, name)"
        ).execute()
        
        folders = results.get('files', [])
        st.info(f"📁 検索結果: {len(folders)}個のフォルダが見つかりました")
        
        if folders:
            folder_id = folders[0]['id']
            st.info(f"✅ 既存フォルダを使用: {folder_id}")
            return folder_id
        
        # フォルダが存在しない場合は作成
        st.info("📁 新しいフォルダを作成中...")
        folder_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        
        folder = drive_service.files().create(
            body=folder_metadata,
            fields='id'
        ).execute()
        
        folder_id = folder.get('id')
        st.info(f"✅ フォルダ作成成功: {folder_id}")
        return folder_id
        
    except Exception as e:
        error_msg = f"フォルダ操作エラー: {e}"
        st.error(error_msg)
        return None

def display_response_with_conditional_image(bot_response, user_input, generate_image=False):
    """ボットの応答を表示し、必要に応じて画像を生成"""
    # ボットの応答テキストを表示
    st.markdown(bot_response)
    
    # 画像生成が指示されている場合
    if generate_image:
        # ユーザー入力から画像仕様を解析（負荷：軽微な文字列処理のみ）
        specs = parse_image_specifications(user_input)
        
        st.markdown("🎨 **画像を生成中...**")
        st.info(f"テキスト内容を元に画像を生成します（スタイル: {specs['style']}, サイズ: {specs['size']}）")
        
        with st.spinner("DALL-E 3で画像を生成しています..."):
            # テキストから画像生成用プロンプトを作成（スタイル指定付き）
            image_prompt = create_image_prompt_from_text(bot_response, specs['style'])
            generated_image, image_bytes = generate_image_with_dalle3(image_prompt, specs['size'])
            
        if generated_image and image_bytes:
            st.image(generated_image, caption=f"生成画像（{specs['style']}スタイル, {specs['size']}）", width="stretch")
            
            # Google Drive保存の条件をチェック
            has_gcp = st.secrets.get("gcp_service_account") is not None
            has_gsheet = st.secrets.get("gsheet_id") is not None
            
            st.info(f"🔍 設定確認: GCP認証={has_gcp}, GSheet ID={has_gsheet}")
            
            # Google Driveに画像を保存
            if has_gcp and has_gsheet:
                with st.spinner("Google Driveに画像を保存しています..."):
                    try:
                        image_id = generate_image_id()
                        st.info(f"🆔 画像ID生成: {image_id}")
                        
                        drive_file_id, drive_link_or_error = save_image_to_drive(
                            image_bytes, 
                            image_id, 
                            image_prompt,
                            st.session_state.get("cid", "unknown")
                        )
                        
                        if drive_file_id:
                            st.success(f"✅ **画像をGoogle Driveに保存しました**")
                            st.info(f"**整理番号:** `{image_id}`")
                            if drive_link_or_error:
                                st.markdown(f"🔗 [Google Driveで表示]({drive_link_or_error})")
                            
                            # 画像情報をGoogle Sheetsに記録
                            try:
                                save_log(
                                    st.session_state.get("cid", "unknown"),
                                    st.session_state.get("bot_type", "unknown"),
                                    "system",
                                    "image_save",
                                    f"画像保存: {bot_response[:100]}...",
                                    image_id,
                                    drive_file_id,
                                    drive_link_or_error or ""
                                )
                                st.info("📊 Google Sheetsにログ記録完了")
                            except Exception as log_error:
                                st.warning(f"⚠️ ログ記録エラー: {log_error}")
                            
                            # セッション状態に画像情報を保存（再表示用）
                            if "saved_images" not in st.session_state:
                                st.session_state.saved_images = []
                            st.session_state.saved_images.append({
                                "image_id": image_id,
                                "drive_link": drive_link_or_error,
                                "prompt": image_prompt
                            })
                        else:
                            st.error(f"❌ 画像保存に失敗しました: {drive_link_or_error}")
                    
                    except Exception as save_error:
                        st.error(f"❌ 保存処理中にエラーが発生しました: {save_error}")
            else:
                missing_items = []
                if not has_gcp:
                    missing_items.append("GCP認証情報")
                if not has_gsheet:
                    missing_items.append("Google Sheets ID")
                
                st.info(f"💡 Google Drive保存機能が無効です。不足: {', '.join(missing_items)}")
                st.info("SecretsにGoogle認証情報を設定すると自動保存されます。")
                
        else:
            st.error("画像の生成に失敗しました。")

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
    try:
        from googleapiclient.discovery import build
        from google.oauth2.service_account import Credentials

        sa_info = _get_sa_dict()
        if not sa_info:
            st.error("❌ サービスアカウント情報が取得できません")
            return None

        st.info("🔑 Google Drive API 認証中...")
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.file"
        ]
        creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
        service = build('drive', 'v3', credentials=creds)
        st.info("✅ Google Drive API 認証成功")
        return service
    except ImportError as e:
        error_msg = f"Google API Client ライブラリがインストールされていません: {e}"
        st.error(error_msg)
        st.info("📦 **インストール方法:**\n```bash\npip install google-api-python-client\n```")
        return None
    except Exception as e:
        error_msg = f"Google Drive API サービスの初期化に失敗しました: {e}"
        st.error(error_msg)
        return None

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
                st.info("4️⃣ **依存関係**:\nrequirements.txtに`google-api-python-client>=2.100.0`が含まれています")
                
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
                    except ImportError:
                        st.error("❌ Google Drive API: google-api-python-client ライブラリがインストールされていません")
                        st.info("📦 **インストール方法:**\n```bash\npip install google-api-python-client\n```")
                    except Exception as e:
                        st.error(f"❌ Google Drive API: 接続失敗 - {e}")
                        
            except Exception as e:
                st.error(f"サービスアカウント情報の読み取りエラー: {e}")
    
    # 画像生成機能の説明
    with st.expander("🎨 画像生成機能について", expanded=False):
        st.markdown("""
        **画像生成機能**
        
        チャット中に以下のキーワードを使用すると、ボットの応答内容を元に自動的に画像が生成されます：
        
        **画像生成キーワード:**
        - 「画像にして」「画像を生成」「画像を作って」
        - 「イメージにして」「絵にして」「図にして」
        - 「ビジュアル化」「画像で表現」「画像化」「絵で表現」
        - 「描いて」「イラストにして」「チャートにして」
        - 「グラフにして」「写真風に」「アートにして」
        - 「スケッチにして」「デザインして」
        
        **スタイル指定キーワード:**
        - 「シンプルな」「ミニマルな」→ ミニマリストスタイル
        - 「写真風の」→ フォトリアリスティック
        - 「アート風の」→ アーティスティック
        - 「スケッチ風の」→ 手描きスケッチ
        - 「チャート」「グラフ」→ 図表スタイル
        - 「ビジネス用の」→ ビジネスプレゼン風
        
        **サイズ指定キーワード:**
        - 「小さな」「小さめの」→ 512×512 (コスト削減)
        - 「大きな」「大きめの」→ 1792×1024
        - 「正方形の」→ 1024×1024 (デフォルト)
        - 「横長の」→ 1792×1024
        - 「縦長の」→ 1024×1792
        
        **使用例:**
        ```
        ユーザー: 「新商品のアイデアをシンプルな図にして」
        → ミニマリストスタイルの画像生成
        
        ユーザー: 「売上データを小さめのチャートで表現して」
        → 512×512のチャート形式で生成
        
        ユーザー: 「企画書用に大きめの写真風画像を作って」
        → 1792×1024のフォトリアリスティック画像
        ```
        
        **特徴:**
        - DALL-E 3による高品質な画像生成
        - スタイルとサイズの柔軟な指定
        - Google Driveへの自動保存（設定済みの場合）
        - 整理番号による画像管理
        - コスト効率的（明示的な指示がある場合のみ生成）
        """)

    with st.form("user_info_form"):
        name = st.text_input("あなたの表示名", value=st.session_state.name or "")
        bot_type = st.selectbox(
            "対話するひらめ１号",
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

    # --- 画像生成機能（コンパクト・扉形式） ---
    # OpenAI APIキーの確認
    has_openai_key = st.secrets.get("OPENAI_API_KEY") is not None
    
    with st.expander("🎨 画像生成機能", expanded=False):
        if not has_openai_key:
            st.caption("⚠️ OpenAI APIキーが設定されていません")
            st.caption("画像生成機能を使用するには、Streamlit CloudのSecretsに `OPENAI_API_KEY` を設定してください。")
        else:
            # 最新のチャット内容を取得（画像生成の元ネタ用）
            latest_messages = st.session_state.messages[-5:] if st.session_state.messages else []
            
            # 参考にするメッセージを選択
            message_options = ["手動入力"] + [f"{msg['name']}: {msg['content'][:30]}..." for msg in latest_messages if msg['content']]
            
            with st.form("image_generation_form"):
                # コンパクトなレイアウト用の列
                col_ref, col_style = st.columns(2)
                
                with col_ref:
                    reference_message = st.selectbox(
                        "参考メッセージ",
                        message_options,
                        help="最近のチャット内容から選択"
                    )
                
                with col_style:
                    # スタイル選択（コンパクト）
                    style_options = {
                        "professional": "🏢 プロ", "minimalist": "✨ ミニマル", 
                        "photorealistic": "📸 写真風", "artistic": "🎨 アート",
                        "sketch": "✏️ スケッチ", "chart": "📊 図表", "business": "💼 ビジネス"
                    }
                    selected_style = st.selectbox("スタイル", list(style_options.keys()), format_func=lambda x: style_options[x])
                
                # 手動入力またはメッセージ内容を設定
                if reference_message == "手動入力":
                    image_content = st.text_area("画像にしたい内容", placeholder="例: 革新的な電動バイクのデザイン案", height=80)
                else:
                    # 選択されたメッセージの内容を取得
                    selected_index = message_options.index(reference_message) - 1
                    if selected_index >= 0 and selected_index < len(latest_messages):
                        auto_content = latest_messages[selected_index]['content']
                        image_content = st.text_area("画像にしたい内容", value=auto_content, height=80)
                    else:
                        image_content = ""
                
                # サイズとボタンを横並び
                col_size, col_btn = st.columns([1, 1])
                
                with col_size:
                    size_options = {
                        "1024x1024": "📐 正方形", "1792x1024": "📱 横長", 
                        "1024x1792": "📱 縦長", "512x512": "💰 小サイズ"
                    }
                    selected_size = st.selectbox("サイズ", list(size_options.keys()), format_func=lambda x: size_options[x])
                
                with col_btn:
                    st.write("")  # スペース調整
                    generate_button = st.form_submit_button("🎨 画像生成", use_container_width=True)
                
                if generate_button:
                    if not image_content.strip():
                        st.error("画像にしたい内容を入力してください。")
                    else:
                        # 画像生成実行
                        with st.spinner("DALL-E 3で画像を生成中..."):
                            image_prompt = create_image_prompt_from_text(image_content, selected_style)
                            generated_image, image_bytes = generate_image_with_dalle3(image_prompt, selected_size)
                            
                        if generated_image and image_bytes:
                            # セッションステートに保存（フォーム外で使用するため）
                            st.session_state.generated_image = generated_image
                            st.session_state.generated_image_bytes = image_bytes
                            st.session_state.generated_image_prompt = image_prompt
                            st.session_state.generated_image_content = image_content
                            st.session_state.generated_image_style = style_options[selected_style]
                            st.session_state.generated_image_size = selected_size
                            st.success("✅ 画像生成完了！下に表示されます。")
                            st.rerun()  # 画面を再描画して結果を表示

                                
                            
                            with col_save:
                                # Google Drive保存の条件をチェック
                                has_gcp = st.secrets.get("gcp_service_account") is not None
                                has_gsheet = st.secrets.get("gsheet_id") is not None
                                
                                if has_gcp and has_gsheet:
                                    if st.button("💾 Drive保存", key="save_generated_image", use_container_width=True):
                                        with st.spinner("保存中..."):
                                            try:
                                                image_id = generate_image_id()
                                                drive_file_id, drive_link_or_error = save_image_to_drive(
                                                    image_bytes, image_id, image_prompt,
                                                    st.session_state.get("cid", "manual_generation")
                                                )
                                                
                                                if drive_file_id:
                                                    st.success("✅ 保存完了！")
                                                    st.caption(f"ID: `{image_id}`")
                                                    if drive_link_or_error:
                                                        st.link_button("🔗 Drive表示", drive_link_or_error)
                                                    
                                                    # ログ記録
                                                    save_log(
                                                        st.session_state.get("cid", "manual_generation"),
                                                        "manual_image_generation", "system", "image_save",
                                                        f"手動画像生成: {image_content[:100]}...",
                                                        image_id, drive_file_id, drive_link_or_error or ""
                                                    )
                                                else:
                                                    st.error(f"❌ 保存失敗: {drive_link_or_error}")
                                            except Exception as e:
                                                st.error(f"❌ 保存エラー: {e}")
                                else:
                                    st.caption("� Drive保存には認証設定が必要")
                        else:
                            st.error("❌ 画像生成に失敗しました")
            
            # フォーム外で画像表示と保存処理（セッションステートから取得）
            if hasattr(st.session_state, 'generated_image') and st.session_state.generated_image is not None:
                # 画像表示をコンパクトに
                col_img, col_save = st.columns([2, 1])
                
                with col_img:
                    st.image(
                        st.session_state.generated_image, 
                        caption=f"{st.session_state.generated_image_style} ({st.session_state.generated_image_size})", 
                        width=300
                    )
                
                with col_save:
                    # Google Drive保存の条件をチェック
                    has_gcp = st.secrets.get("gcp_service_account") is not None
                    has_gsheet = st.secrets.get("gsheet_id") is not None
                    shared_drive_id = st.secrets.get("shared_drive_id")
                    
                    # Google Drive保存の状態を表示
                    if has_gcp and has_gsheet:
                        if shared_drive_id:
                            st.caption(f"📁 共有ドライブ設定: `{shared_drive_id[:20]}...`")
                        else:
                            st.caption("💡 共有ドライブ未設定（個人ドライブを使用）")
                            
                        col_save_btn, col_download = st.columns(2)
                        
                        with col_save_btn:
                            if st.button("💾 Drive保存", key="save_generated_image", use_container_width=True):
                                with st.spinner("保存中..."):
                                    try:
                                        image_id = generate_image_id()
                                        drive_file_id, drive_link_or_error = save_image_to_drive(
                                            st.session_state.generated_image_bytes, 
                                            image_id, 
                                            st.session_state.generated_image_prompt,
                                            st.session_state.get("cid", "manual_generation")
                                        )
                                        
                                        if drive_file_id:
                                            st.success("✅ 保存完了！")
                                            st.caption(f"ID: `{image_id}`")
                                            if drive_link_or_error:
                                                st.link_button("🔗 Drive表示", drive_link_or_error)
                                            
                                            # ログ記録
                                            save_log(
                                                st.session_state.get("cid", "manual_generation"),
                                                "manual_image_generation", "system", "image_save",
                                                f"手動画像生成: {st.session_state.generated_image_content[:100]}...",
                                                image_id, drive_file_id, drive_link_or_error or ""
                                            )
                                            
                                            # 保存後はセッションステートをクリア
                                            for key in ['generated_image', 'generated_image_bytes', 'generated_image_prompt', 
                                                      'generated_image_content', 'generated_image_style', 'generated_image_size']:
                                                if key in st.session_state:
                                                    del st.session_state[key]
                                            st.rerun()
                                        else:
                                            st.error(f"❌ 保存失敗: {drive_link_or_error}")
                                            st.info("💡 画像は生成されています。右のダウンロードボタンをお使いください。")
                                    except Exception as e:
                                        st.error(f"❌ 保存エラー: {e}")
                                        st.info("💡 画像は生成されています。右のダウンロードボタンをお使いください。")
                        
                        with col_download:
                            # 画像ダウンロード機能を追加
                            if st.download_button(
                                label="📥 ダウンロード",
                                data=st.session_state.generated_image_bytes,
                                file_name=f"ai_image_{generate_image_id()}.jpg",
                                mime="image/jpeg",
                                use_container_width=True
                            ):
                                st.success("✅ ダウンロード開始！")
                        
                        # クリアボタンを追加
                        if st.button("🗑️ クリア", key="clear_generated_image", use_container_width=True):
                            for key in ['generated_image', 'generated_image_bytes', 'generated_image_prompt', 
                                      'generated_image_content', 'generated_image_style', 'generated_image_size']:
                                if key in st.session_state:
                                    del st.session_state[key]
                            st.rerun()
                    else:
                        st.caption("💡 Google Drive保存には認証設定が必要です")
                        
                        # Drive保存ができない場合でもダウンロードは提供
                        col_download_only, col_clear = st.columns(2)
                        
                        with col_download_only:
                            if st.download_button(
                                label="📥 ダウンロード",
                                data=st.session_state.generated_image_bytes,
                                file_name=f"ai_image_{generate_image_id()}.jpg",
                                mime="image/jpeg",
                                use_container_width=True
                            ):
                                st.success("✅ ダウンロード開始！")
                        
                        with col_clear:
                            if st.button("🗑️ クリア", key="clear_generated_image_no_drive", use_container_width=True):
                                for key in ['generated_image', 'generated_image_bytes', 'generated_image_prompt', 
                                          'generated_image_content', 'generated_image_style', 'generated_image_size']:
                                    if key in st.session_state:
                                        del st.session_state[key]
                                st.rerun()

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
            # すべてのメッセージはテキストとして表示
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

        # --- Dify APIへリクエスト（画像生成キーワードチェックを削除） ---
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
                            # JSON形式で解析を試行
                            errj = res.json()
                            emsg = (errj.get("message") or errj.get("error") or errj.get("detail") or "")
                        except Exception:
                            # JSONで解析できない場合はテキストとして扱う
                            emsg = res.text
                        # "conversation", "invalid" 等の語を含む場合に会話IDを外して再送
                        if any(k in emsg.lower() for k in ["conversation", "invalid id", "must not be empty"]):
                            bad_cid = payload.pop("conversation_id")
                            res = call_dify(payload)
                            if res.ok:
                                st.warning(f"無効な会話IDだったため新規会話で再開しました（old={bad_cid}）")

                    res.raise_for_status()
                    
                    # レスポンスの形式を判定して処理
                    try:
                        # JSON形式での解析を試行
                        rj = res.json()
                        answer = rj.get("answer", "⚠️ 応答がありませんでした。")
                        
                        # 新規会話IDが発行されたら保存
                        new_cid = rj.get("conversation_id")
                        if new_cid and not st.session_state.cid:
                            st.session_state.cid = new_cid
                            
                    except (json.JSONDecodeError, ValueError):
                        # JSON形式でない場合はテキストとして処理
                        answer = res.text.strip()
                        
                        # テキスト応答から会話IDを抽出しようと試みる（オプション）
                        # 例: "conversation_id: xxxx" のような形式がテキストに含まれている場合
                        import re
                        cid_match = re.search(r'conversation_id:\s*([a-zA-Z0-9\-_]+)', answer)
                        if cid_match and not st.session_state.cid:
                            st.session_state.cid = cid_match.group(1)
                            # 会話IDが含まれている場合は、その部分を除去
                            answer = re.sub(r'conversation_id:\s*[a-zA-Z0-9\-_]+\s*', '', answer).strip()

                    # 応答をテキストのみ表示（画像生成は別セクションで実行）
                    st.markdown(answer)

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
            assistant_message = {"role": "assistant", "content": answer, "name": st.session_state.bot_type}
            st.session_state.messages.append(assistant_message)
            save_log(
                st.session_state.cid or "(allocating...)",
                st.session_state.bot_type,
                "assistant",
                st.session_state.bot_type,
                answer
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