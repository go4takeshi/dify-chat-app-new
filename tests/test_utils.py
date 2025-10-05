import pandas as pd
from utils import prepare_keyword_split_csv


def test_prepare_keyword_split_csv_basic():
    messages = [
        {"role": "user", "name": "U", "content": "hello"},
        {"role": "assistant", "name": "Bot", "content": "kw1\nkw2\nkw3"},
    ]
    b = prepare_keyword_split_csv(messages, max_keywords=10)
    df = pd.read_csv(pd.io.common.BytesIO(b))
    assert list(df.columns) == ["role", "name", "content", "keyword_1", "keyword_2", "keyword_3"]
    assert df.iloc[1]["keyword_1"] == "kw1"
    assert df.iloc[1]["keyword_3"] == "kw3"


def test_prepare_keyword_split_csv_truncate():
    messages = [
        {"role": "assistant", "name": "Bot", "content": "".join([f"k{i}\n" for i in range(1, 21)])}
    ]
    b = prepare_keyword_split_csv(messages, max_keywords=10)
    df = pd.read_csv(pd.io.common.BytesIO(b))
    # 列数は role,name,content + 10 keywords
    assert df.shape[1] == 3 + 10
    # 最後のキーワードに truncated 表示が入る
    last_kw = df.iloc[0][f"keyword_10"]
    assert "truncated" in str(last_kw)
