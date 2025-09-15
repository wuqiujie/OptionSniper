


import streamlit as st
import pandas as pd
import numpy as np
from sellput_checker.yahoo_client import YahooClient
from sellput_checker.calculations import bs_d1_d2
from sellput_checker.utils import norm_cdf
from sellput_checker.app import tr


def robust_price_fields(df: pd.DataFrame, is_call: bool, S: float, T_years: float, r: float = 0.05) -> pd.DataFrame:
    df = df.copy()
    for c in ["bid", "ask", "strike", "iv", "volume", "open_interest"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "mid" not in df.columns:
        df["mid"] = (df.get("bid", 0).fillna(0) + df.get("ask", 0).fillna(0)) / 2
    df["spread"] = (df.get("ask", 0).fillna(0) - df.get("bid", 0).fillna(0)).clip(lower=0)
    return df

def render():
    st.subheader("🦅 铁鹰策略筛选 / Iron Condor Screener")

    ticker = st.text_input(tr("股票代码 (Ticker)", "Ticker"), "NVDA").upper()
    if not ticker:
        st.stop()

    yc = YahooClient(ticker)
    expirations_ic = list(yc.get_expirations() or [])
    if not expirations_ic:
        st.error(tr("无法获取期权到期日，可能是网络问题或标的无期权。", "Failed to fetch expirations."))
        st.stop()
    exp_options_ic = [tr("自动（全部到期）", "Auto (All Expirations)")] + expirations_ic
    exp_choice_ic = st.selectbox(tr("选择到期日", "Expiration"), exp_options_ic)
    selected_exps_ic = expirations_ic if exp_choice_ic == exp_options_ic[0] else [exp_choice_ic]

    short_delta_low, short_delta_high = st.slider("短腿 |Delta| 目标区间", 0.00, 0.60, (0.10, 0.35), 0.01)
    wing_width_list_text_ic = st.text_input("翼宽列表（逗号分隔）", value="3,5,10")
    min_credit_ic = st.number_input("最小净收权利金（$）", min_value=0.0, value=0.20, step=0.05)
    if st.button(tr("获取铁鹰候选", "Get Iron Condor Suggestions")):
        spot_ic = yc.get_spot_price()
        all_rows_ic = []
        for exp_ic in selected_exps_ic:
            call_df = yc.get_option_chain(exp_ic, kind="call").copy()
            put_df = yc.get_option_chain(exp_ic, kind="put").copy()
            if call_df.empty or put_df.empty:
                continue
            T_years_ic = max(1e-6, (pd.to_datetime(exp_ic) - pd.Timestamp.today()).days / 365.0)
            call_df = robust_price_fields(call_df, True, float(spot_ic), T_years_ic)
            put_df = robust_price_fields(put_df, False, float(spot_ic), T_years_ic)

            # 简化: 选择一个最近的put和call构造示例
            sp = put_df.loc[put_df["strike"] < float(spot_ic)].iloc[-1]
            sc = call_df.loc[call_df["strike"] > float(spot_ic)].iloc[0]

            try:
                wing_list = [float(x.strip()) for x in wing_width_list_text_ic.split(",") if x.strip() != ""]
            except Exception:
                wing_list = [5.0]

            for w in wing_list:
                lp = put_df.loc[(put_df["strike"] - (sp["strike"] - w)).abs().idxmin()]
                lc = call_df.loc[(call_df["strike"] - (sc["strike"] + w)).abs().idxmin()]
                sp_p = float(sp.get("mid", 0))
                sc_p = float(sc.get("mid", 0))
                lp_p = float(lp.get("mid", 0))
                lc_p = float(lc.get("mid", 0))
                credit = sp_p - lp_p + sc_p - lc_p
                if credit <= 0:
                    continue
                dte = (pd.to_datetime(exp_ic) - pd.Timestamp.today()).days
                be_low = float(sp["strike"]) - credit
                be_high = float(sc["strike"]) + credit
                all_rows_ic.append({
                    "到期": exp_ic,
                    "DTE": dte,
                    "卖Put": float(sp["strike"]),
                    "买Put": float(lp["strike"]),
                    "卖Call": float(sc["strike"]),
                    "买Call": float(lc["strike"]),
                    "净收权利金($)": round(float(credit), 2),
                    "翼宽($)": round(float(w), 2),
                    "盈亏平衡下界": round(be_low, 2),
                    "盈亏平衡上界": round(be_high, 2),
                })
        res = pd.DataFrame(all_rows_ic)
        if not res.empty:
            res = res[res["净收权利金($)"].fillna(0) >= float(min_credit_ic)]
        st.dataframe(res, use_container_width=True)

if __name__ == "__main__":
    render()