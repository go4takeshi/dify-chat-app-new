#!/usr/bin/env python3
"""
Google Service Accountの情報を確認するスクリプト
Streamlit CloudのSecretsに設定されたgcp_service_accountからメールアドレスを取得
"""

import json
import streamlit as st

def check_service_account_info():
    """Service Accountの情報を表示"""
    try:
        # Secretsからサービスアカウント情報を取得
        gcp_creds = st.secrets.get("gcp_service_account")
        
        if not gcp_creds:
            st.error("❌ gcp_service_account が設定されていません")
            return
        
        # JSON文字列をパース
        if isinstance(gcp_creds, str):
            creds_dict = json.loads(gcp_creds)
        else:
            creds_dict = dict(gcp_creds)
        
        # 重要な情報を表示
        st.success("✅ Service Account情報:")
        st.info(f"📧 Email: `{creds_dict.get('client_email', 'N/A')}`")
        st.info(f"🆔 Client ID: `{creds_dict.get('client_id', 'N/A')}`")
        st.info(f"📂 Project ID: `{creds_dict.get('project_id', 'N/A')}`")
        
        st.markdown("---")
        st.markdown("### 📋 共有ドライブへの追加手順")
        st.markdown(f"""
        1. Google Driveで共有ドライブを作成
        2. 共有ドライブを右クリック → 「メンバーを管理」
        3. 以下のメールアドレスを追加:
           ```
           {creds_dict.get('client_email', 'N/A')}
           ```
        4. 権限を「編集者」に設定
        """)
        
    except json.JSONDecodeError as e:
        st.error(f"❌ JSON解析エラー: {e}")
    except Exception as e:
        st.error(f"❌ エラー: {e}")

if __name__ == "__main__":
    st.title("🔍 Service Account情報確認")
    check_service_account_info()