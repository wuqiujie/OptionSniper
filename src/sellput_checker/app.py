import streamlit as st

st.set_page_config(page_title="Option Strategy Checker", layout="wide")

# Global language selector (stored in session state so pages can read it)
LANG_OPTIONS = ["English", "中文"]
if "lang_mode" not in st.session_state:
    st.session_state["lang_mode"] = LANG_OPTIONS[0]

st.sidebar.selectbox(
    "Language / 语言",
    LANG_OPTIONS,
    index=LANG_OPTIONS.index(st.session_state["lang_mode"]),
    key="lang_mode",
)

def tr(cn: str, en: str) -> str:
    return cn if st.session_state.get("lang_mode") == "中文" else en

st.title(tr("期权策略筛选器", "Option Strategy Checker"))

st.markdown(tr(
    """
    1. **卖出看跌 (Sell Put)** – 根据 Delta、IV、年化等筛选备选合约
    2. **备兑看涨 (Covered Call)** – 选择更优的 Covered Call 合约
    3. **铁蝶 (Iron Butterfly)** – 中性/轻波动情境的有限风险策略
    4. **铁鹰 (Iron Condor)** – 区间震荡情境的有限风险策略

    请在左侧导航栏选择页面进入。语言设置将保存在会话中，供各页面使用。
    """,
    """
    Welcome! 
    1. **Sell Put** – Screen candidates by Delta, IV, Annualized return, etc.
    2. **Covered Call** – Find attractive covered-call candidates
    3. **Iron Butterfly** – Limited-risk strategy for neutral/slightly volatile views
    4. **Iron Condor** – Limited-risk strategy for range-bound views

    Use the left navigation to open a page. Your language preference is stored in the session.
    """
))