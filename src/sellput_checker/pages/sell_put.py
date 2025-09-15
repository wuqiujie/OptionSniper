import streamlit as st
import pandas as pd
import numpy as np

from sellput_checker.yahoo_client import YahooClient
from sellput_checker.checklist import evaluate_chain_df

# language helper (read from session if available)
LANG_OPTIONS = ["English", "中文"]
def tr(cn: str, en: str) -> str:
    return cn if st.session_state.get("lang_mode", "English") == "中文" else en

st.set_page_config(page_title="Sell Put", layout="wide")
st.title(tr("📉 卖出看跌合约筛选", "📉 Sell Put Screener"))

ticker = st.text_input(
    tr("股票代码 (Ticker)", "Ticker"),
    "NVDA",
    help=tr("例如 NVDA、AAPL、TSLA 等", "e.g., NVDA, AAPL, TSLA")
).upper()

if not ticker:
    st.stop()

yc = YahooClient(ticker)

expirations = list(yc.get_expirations() or [])
if not expirations:
    st.error(tr("无法获取期权到期日，可能是网络问题或标的无期权。", "Failed to fetch expirations."))
    st.stop()

exp_options = [tr("自动（全部到期）", "Auto (All Expirations)")] + expirations
exp_choice = st.selectbox(tr("选择到期日", "Expiration"), exp_options)
selected_exps = expirations if exp_choice == exp_options[0] else [exp_choice]

delta_high = st.slider(tr("Delta 上限", "Max |Delta|"), 0.0, 1.0, 0.35, 0.05)
min_annual_percent = st.slider(tr("最小年化收益率（%）", "Min Annualized Return (%)"), 0.0, 200.0, 15.0, 0.5)
min_annual = min_annual_percent / 100.0

iv_min_percent, iv_max_percent = st.slider(tr("隐含波动率 IV 区间（%）", "IV Range (%)"),
                                           0.0, 300.0, (0.0, 150.0), 0.5)
iv_min, iv_max = iv_min_percent / 100.0, iv_max_percent / 100.0

max_spread = st.slider(tr("最大买卖价差（美元）", "Max Bid-Ask Spread ($)"), 0.0, 3.0, 0.30, 0.01)
min_volume = st.number_input(tr("最小成交量", "Min Volume"), min_value=0, value=100, step=10)

st.caption(tr("折价百分比 = (现价 − 行权价) / 现价。通常选择比现价低 5%~15%。",
              "Discount = (Spot − Strike) / Spot. Common choice: 5%–15% below spot."))

if st.button(tr("获取推荐合约", "Get Sell Put Suggestions")):
    spot = yc.get_spot_price()
    all_rows = []
    for exp in selected_exps:
        df = yc.get_option_chain(exp, kind="put")
        if df.empty:
            continue
        df["ticker"] = ticker
        df["volume"] = pd.to_numeric(df.get("volume", pd.Series(dtype=float)), errors="coerce").fillna(0).astype(int)
        df["open_interest"] = pd.to_numeric(df.get("open_interest", pd.Series(dtype=float)), errors="coerce").fillna(0).astype(int)
        for col in ["bid", "ask", "strike"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        out_exp = evaluate_chain_df(
            df, spot, exp,
            delta_high=delta_high,
            iv_min=iv_min, iv_max=iv_max,
            max_spread=max_spread, min_volume=min_volume, min_annual=min_annual
        )
        all_rows.append(out_exp)

    if not all_rows:
        st.error(tr("未获取到期权链。", "No option chain retrieved."))
        st.stop()

    out = pd.concat(all_rows, ignore_index=True)
    out = out[out["ok_all"] == True]

    try:
        if spot and float(spot) > 0:
            out["discount_pct"] = ((float(spot) - out["strike"]) / float(spot) * 100).round(2)
        else:
            out["discount_pct"] = np.nan
    except Exception:
        out["discount_pct"] = np.nan

    if len(out) == 0:
        st.info(tr(
            "当前筛选过于严格：尝试将最小年化调低至 5–10%、最大价差放宽到 $0.30–$0.50、最小成交量降到 20–50，或把 Delta 上限调到 0.40。",
            "Filters look too strict. Try Min Annualized 5–10%, Max Spread $0.30–$0.50, Min Volume 20–50, and/or Max |Delta| up to 0.40."
        ))

    use_display = st.sidebar.checkbox(
        tr("使用 Last 兜底显示 Bid/Ask", "Use 'Last' fallback for Bid/Ask display"),
        value=True
    )
    has_disp = all(c in out.columns for c in ["bid_display", "ask_display", "spread_display"])

    if use_display and has_disp:
        cols = [
            "contract_symbol","strike","discount_pct","mid","single_return","annualized_return","iv","assign_prob_est",
            "days_to_exp","margin_cash_secured","volume","open_interest",
            "bid_display","ask_display","spread_display","itm_prob","delta","price_source"
        ]
    else:
        cols = [
            "contract_symbol","strike","discount_pct","mid","single_return","annualized_return","iv","assign_prob_est",
            "days_to_exp","margin_cash_secured","volume","open_interest",
            "bid","ask","spread","itm_prob","delta","price_source"
        ]

    show = out[cols].copy()
    if not show.empty:
        show["iv"] = (show["iv"] * 100).round(2)
        show["delta"] = (show["delta"] * 100).round(2)
        show["assign_prob_est"] = (show["assign_prob_est"] * 100).round(2)
        show["itm_prob"] = (show["itm_prob"] * 100).round(2)
        show["single_return"] = (show["single_return"] * 100).round(2)
        show["annualized_return"] = (show["annualized_return"] * 100).round(2)

    try:
        bid_col = "bid_display" if (use_display and has_disp) else "bid"
        ask_col = "ask_display" if (use_display and has_disp) else "ask"
        spr_col = "spread_display" if (use_display and has_disp) else "spread"
        zero_ba = (out.get("bid", pd.Series(dtype=float)).fillna(0) == 0) & (out.get("ask", pd.Series(dtype=float)).fillna(0) == 0)
        if zero_ba.any() and bid_col in show.columns and ask_col in show.columns:
            show.loc[zero_ba, [bid_col, ask_col]] = np.nan
        if spr_col in show.columns:
            show.loc[show[spr_col] == 0, spr_col] = np.nan
    except Exception:
        pass

    if st.session_state.get("lang_mode", "English") == "English":
        cols_map = {
            "contract_symbol": "Contract",
            "strike": "Strike",
            "discount_pct": "Strike Discount vs Spot (%)",
            "bid": "Bid", "ask": "Ask", "mid": "Mid",
            "iv": "IV (%)","delta": "Delta (%)","itm_prob": "ITM Prob (%)",
            "days_to_exp": "DTE","margin_cash_secured": "Cash Secured ($)",
            "single_return": "Period Return (%)","annualized_return": "Annualized (%)",
            "spread": "Spread ($)","volume": "Volume","open_interest": "OI",
            "price_source": "Price Src","assign_prob_est": "Assign Prob ~|Δ| (%)",
            "bid_display": "Bid (disp)","ask_display": "Ask (disp)","spread_display": "Spread (disp)",
        }
    else:
        cols_map = {
            "contract_symbol": "合约代码",
            "strike": "行权价",
            "discount_pct": "相对现价折价（%）",
            "bid": "买价", "ask": "卖价", "mid": "中间价",
            "iv": "隐含波动率（%）","delta": "Delta（%）","itm_prob": "价内概率（%）",
            "days_to_exp": "剩余天数","margin_cash_secured": "现金担保（$）",
            "single_return": "单期收益率（%）","annualized_return": "年化（%）",
            "spread": "价差（$）","volume": "成交量","open_interest": "未平仓量",
            "price_source": "价格来源","assign_prob_est": "行权概率估算~|Δ|（%）",
            "bid_display": "买价(兜底)","ask_display": "卖价(兜底)","spread_display": "价差(兜底)",
        }

    show = show.rename(columns=cols_map)
    st.session_state["last_table"] = show
    st.success(tr("列表已更新。可在下方勾选进行比较。", "List updated. Use the checkboxes below to compare."))

current = st.session_state.get("last_table")
if isinstance(current, pd.DataFrame) and not current.empty:
    select_col = "选择" if st.session_state.get("lang_mode") == "中文" else "Select"
    disp = current.copy()
    if select_col not in disp.columns:
        disp.insert(0, select_col, False)
    else:
        disp = disp[[select_col] + [c for c in disp.columns if c != select_col]]
    edited = st.data_editor(
        disp, use_container_width=True, num_rows="fixed", hide_index=True,
        column_config={select_col: st.column_config.CheckboxColumn(label=select_col, default=False)},
        key="sellput_editor",
    )
    if st.button(tr("比较所选", "Compare selected")):
        chosen = edited[edited[select_col] == True].copy() if isinstance(edited, pd.DataFrame) else pd.DataFrame()
        if chosen.empty:
            st.warning(tr("请先勾选至少一条合约", "Please select at least one contract."))
        else:
            if select_col in chosen.columns:
                chosen = chosen.drop(columns=[select_col])
            pref = [
                tr("合约代码", "Contract"), tr("行权价", "Strike"), tr("相对现价折价（%）", "Strike Discount vs Spot (%)"),
                tr("年化（%）", "Annualized (%)"), tr("单期收益率（%）", "Period Return (%)"),
                tr("隐含波动率（%）", "IV (%)"), tr("Delta（%）", "Delta (%)"), tr("价内概率（%）", "ITM Prob (%)"),
                tr("剩余天数", "DTE"), tr("价差（$）", "Spread ($)"), tr("成交量", "Volume"), tr("未平仓量", "OI"),
                tr("买价", "Bid"), tr("卖价", "Ask"), tr("中间价", "Mid")
            ]
            cols_exist = [c for c in pref if c in chosen.columns]
            chosen = chosen[cols_exist] if cols_exist else chosen
            st.subheader(tr("🆚 所选合约对比", "🆚 Comparison"))
            st.dataframe(chosen, use_container_width=True)
else:
    st.info(tr("点击上方按钮以生成列表。", "Click the button above to generate the list."))