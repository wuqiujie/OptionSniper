import streamlit as st
import pandas as pd
import numpy as np

from sellput_checker.yahoo_client import YahooClient
from sellput_checker.checklist import evaluate_chain_df
from sellput_checker.calculations import bs_d1_d2
from sellput_checker.utils import norm_cdf

# language helper
def tr(cn: str, en: str) -> str:
    return cn if st.session_state.get("lang_mode", "English") == "中文" else en

st.set_page_config(page_title="Covered Call", layout="wide")
st.title(tr("📈 Covered Call 合约筛选", "📈 Covered Call Screener"))

ticker = st.text_input(tr("股票代码 (Ticker)", "Ticker"), "NVDA").upper()
if not ticker:
    st.stop()
yc = YahooClient(ticker)

expirations_cc = list(yc.get_expirations() or [])
if not expirations_cc:
    st.error(tr("无法获取期权到期日，可能是网络问题或标的无期权。", "Failed to fetch expirations."))
    st.stop()
exp_options_cc = [tr("自动（全部到期）", "Auto (All Expirations)")] + expirations_cc
exp_choice_cc = st.selectbox(tr("选择到期日", "Expiration"), exp_options_cc,
                             help=tr("保持“自动”以包含全部到期。", "Keep 'Auto' to include all expirations."))
selected_exps_cc = expirations_cc if exp_choice_cc == exp_options_cc[0] else [exp_choice_cc]

delta_high_cc = st.slider(tr("Delta 上限", "Max Delta"), 0.0, 1.0, 0.30, 0.05)
min_premium_usd = st.number_input(tr("最小权利金（$）", "Min Premium ($)"), min_value=0.0, value=0.50, step=0.05)
iv_min_cc_pct, iv_max_cc_pct = st.slider(tr("隐含波动率 IV 区间（%）", "IV Range (%)"),
                                         0.0, 300.0, (0.0, 120.0), 0.5)
iv_min_cc, iv_max_cc = iv_min_cc_pct / 100.0, iv_max_cc_pct / 100.0
max_spread_cc = st.slider(tr("最大买卖价差（$）", "Max Bid-Ask Spread ($)"), 0.0, 3.0, 0.10, 0.01)
min_volume_cc = st.number_input(tr("最小成交量", "Min Volume"), min_value=0, value=100, step=10)
only_otm = st.checkbox(tr("仅显示价外（行权价 ≥ 现价）", "Only show OTM (Strike ≥ Spot)"), value=True)
min_strike_prem_pct = st.slider(tr("行权价相对现价的溢价（%）下限", "Min Strike Premium vs Spot (%)"),
                                0.0, 50.0, 5.0, 0.5)

if st.button(tr("获取 Covered Call 推荐", "Get Covered Call Suggestions")):
    spot_cc = yc.get_spot_price()
    all_rows_cc = []
    for exp in selected_exps_cc:
        dfc = yc.get_option_chain(exp, kind="call")
        if dfc.empty:
            continue
        dfc["ticker"] = ticker
        dfc["volume"] = pd.to_numeric(dfc.get("volume", pd.Series(dtype=float)), errors="coerce").fillna(0).astype(int)
        dfc["open_interest"] = pd.to_numeric(dfc.get("open_interest", pd.Series(dtype=float)), errors="coerce").fillna(0).astype(int)
        for col in ["bid", "ask", "strike"]:
            if col in dfc.columns:
                dfc[col] = pd.to_numeric(dfc[col], errors="coerce")
        out_cc = evaluate_chain_df(
            dfc, spot_cc, exp,
            delta_high=1.0,  # placeholder: we'll recompute delta for calls
            iv_min=iv_min_cc, iv_max=iv_max_cc,
            max_spread=max_spread_cc, min_volume=min_volume_cc, min_annual=0.0
        )
        all_rows_cc.append(out_cc)

    if not all_rows_cc:
        st.error(tr("未获取到期权链。", "No option chain retrieved."))
        st.stop()
    out_cc = pd.concat(all_rows_cc, ignore_index=True)
    out_cc["strike_premium_pct"] = ((out_cc["strike"] - float(spot_cc)) / float(spot_cc) * 100).round(2)

    def _call_delta_row(row):
        try:
            S = float(spot_cc)
            K = float(row.get("strike", 0.0))
            sigma = float(row.get("iv", 0.0))
            T = max(1e-6, float(row.get("days_to_exp", 0)) / 365.0)
            r = 0.05
            d1, _ = bs_d1_d2(S, K, r, max(sigma, 1e-6), T)
            return float(norm_cdf(d1))
        except Exception:
            return np.nan

    out_cc["delta"] = out_cc.apply(_call_delta_row, axis=1)

    if only_otm:
        out_cc = out_cc[out_cc["strike"] >= float(spot_cc)]
    out_cc = out_cc[out_cc["mid"].fillna(0) >= float(min_premium_usd)]
    out_cc = out_cc[out_cc["strike_premium_pct"].fillna(-1) >= float(min_strike_prem_pct)]
    out_cc = out_cc[out_cc["delta"].fillna(1.0) <= float(delta_high_cc)]

    out_cc = out_cc.sort_values(["annualized_return", "strike_premium_pct"], ascending=[False, False])

    cols_cc = [
        "contract_symbol", "strike", "strike_premium_pct", "mid", "annualized_return", "single_return",
        "iv", "delta", "days_to_exp", "volume", "open_interest", "bid", "ask", "spread"
    ]
    show_cc = out_cc[cols_cc].copy()
    if not show_cc.empty:
        show_cc["iv"] = (show_cc["iv"] * 100).round(2)
        show_cc["delta"] = (show_cc["delta"] * 100).round(2)
        show_cc["annualized_return"] = (show_cc["annualized_return"] * 100).round(2)
        show_cc["single_return"] = (show_cc["single_return"] * 100).round(2)

    if st.session_state.get("lang_mode", "English") == "English":
        cols_map_cc = {
            "contract_symbol": "Contract",
            "strike": "Strike",
            "strike_premium_pct": "Strike Premium vs Spot (%)",
            "mid": "Mid",
            "annualized_return": "Annualized (%)",
            "single_return": "Period Return (%)",
            "iv": "IV (%)",
            "delta": "Delta (%)",
            "days_to_exp": "DTE",
            "volume": "Volume",
            "open_interest": "OI",
            "bid": "Bid",
            "ask": "Ask",
            "spread": "Spread ($)",
        }
    else:
        cols_map_cc = {
            "contract_symbol": "合约代码",
            "strike": "行权价",
            "strike_premium_pct": "相对现价溢价（%）",
            "mid": "中间价",
            "annualized_return": "年化（%）",
            "single_return": "单期收益率（%）",
            "iv": "隐含波动率（%）",
            "delta": "Delta（%）",
            "days_to_exp": "剩余天数",
            "volume": "成交量",
            "open_interest": "未平仓量",
            "bid": "买价",
            "ask": "卖价",
            "spread": "价差（$）",
        }
    show_cc = show_cc.rename(columns=cols_map_cc)

    st.session_state["last_table_call"] = show_cc

    select_col_cc = "选择" if st.session_state.get("lang_mode") == "中文" else "Select"
    disp_cc = show_cc.copy()
    if select_col_cc not in disp_cc.columns:
        disp_cc.insert(0, select_col_cc, False)
    else:
        disp_cc = disp_cc[[select_col_cc] + [c for c in disp_cc.columns if c != select_col_cc]]

    edited_cc = st.data_editor(
        disp_cc,
        use_container_width=True,
        num_rows="fixed",
        hide_index=True,
        column_config={select_col_cc: st.column_config.CheckboxColumn(label=select_col_cc, default=False)},
        key="coveredcall_editor",
    )
    if st.button(tr("比较所选", "Compare selected")):
        chosen_cc = edited_cc[edited_cc[select_col_cc] == True].copy() if isinstance(edited_cc, pd.DataFrame) else pd.DataFrame()
        if chosen_cc.empty:
            st.warning(tr("请先勾选至少一条合约", "Please select at least one contract."))
        else:
            if select_col_cc in chosen_cc.columns:
                chosen_cc = chosen_cc.drop(columns=[select_col_cc])
            st.subheader(tr("🆚 所选合约对比", "🆚 Comparison"))
            st.dataframe(chosen_cc, use_container_width=True)

_cc_tbl = st.session_state.get("last_table_call")
if not (isinstance(_cc_tbl, pd.DataFrame) and not _cc_tbl.empty):
    st.info(tr("点击上方按钮以生成列表。", "Click the button above to generate the list."))