import io
import pandas as pd


def prepare_keyword_split_csv(messages, max_keywords=100):
    """messages: list of dicts with keys role, content, name

    - Keep role,name,content columns
    - For assistant messages, split content by lines and place into keyword_1..keyword_N columns
    - Truncate to max_keywords and annotate the last keyword with truncation info if truncated
    Returns: bytes (utf-8-sig)
    """
    rows = []
    max_kw = 0
    for m in messages:
        role = m.get("role", "")
        name = m.get("name", "")
        content = m.get("content", "")
        if role == "assistant":
            kws = [k.strip() for k in str(content).splitlines() if k.strip()]
            if len(kws) > max_keywords:
                remaining = len(kws) - max_keywords
                kws = kws[:max_keywords]
                if kws:
                    kws[-1] = f"{kws[-1]} (...+{remaining} truncated)"
        else:
            kws = []
        rows.append({"role": role, "name": name, "content": content, "_kws": kws})
        if len(kws) > max_kw:
            max_kw = len(kws)

    out_rows = []
    for r in rows:
        out = {"role": r["role"], "name": r["name"], "content": r["content"]}
        for i in range(max_kw):
            out[f"keyword_{i+1}"] = r["_kws"][i] if i < len(r["_kws"]) else ""
        out_rows.append(out)

    df_out = pd.DataFrame(out_rows)
    buf = io.StringIO()
    df_out.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")
