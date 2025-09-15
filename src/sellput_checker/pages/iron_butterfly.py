import streamlit as st
import pandas as pd
import numpy as np

from sellput_checker.yahoo_client import YahooClient
from sellput_checker.calculations import bs_d1_d2
from sellput_checker.utils import norm_cdf

# language + mini helpers
def tr(cn: str, en: str) -> str:
    return cn if st.session_state.get("lang_mode", "English") == "中文" else en

def bs_price_theo(S: float, K: float, r: float, sigma: float, T: float, is_call: bool) -> float:
    try:
        if S <= 0 or K <= 0 or sigma <= 0 or T <= 0:
            return 0.0
        d1, d2 = bs_d1_d2(float(S), float(K), float(r), float(sigma), float(T))
        if is_call:
            return float(S) * float(norm_cdf(d1)) - float(K) * np.exp(-float(r) * float(T)) * float(norm_cdf(d2))
        else:
            return float(K) * np.exp(-float(r) * float(T)) * float(norm_cdf(-d2)) - float(S) * float(norm_cdf(-d1))
    except Exception:
        return 0.0

def robust_price_fields(df: pd.DataFrame, is_call: bool, S: float, T_years: float, r: float = 0.05) -> pd.DataFrame:
    df = df.copy()
    for c in ["bid", "ask", "strike", "iv", "volume", "open_interest"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "mid" not in df.columns:
        df["mid"] = (df.get("bid", 0).fillna(0) + df.get("ask", 0).fillna(0)) / 2
    df["spread"] = (df.get("ask", 0).fillna(0) - df.get("bid", 0).fillna(0)).clip(lower=0)
    if "last" in df.columns:
        df["last"] = pd.to_numeric(df["last"], errors="coerce")
    elif "last_price" in df.columns:
        df = df.rename(columns={"last_price": "last"})
        df["last"] = pd.to_numeric(df["last"], errors="coerce")
    else:
        df["last"] = np.nan

    def _theo(row):
        try:
            return bs_price_theo(float(S), float(row.get("strike", 0.0)), 0.05, max(float(row.get("iv", 0.0)), 1e-6), float(T_years), bool(is_call))
        except Exception:
            return 0.0
    df["theo"] = df.apply(_theo, axis=1)

    def _mid_used(row):
        b = float(row.get("bid", 0) or 0); a = float(row.get("ask", 0) or 0)
        m = (b + a) / 2.0 if (b > 0 and a > 0) else 0.0
        if m > 0: return m
        l = float(row.get("last", 0) or 0);  t = float(row.get("theo", 0) or 0)
        return l if l > 0 else (t if t > 0 else max(b, a, 0.0))
    def _bid_used(row):
        b = float(row.get("bid", 0) or 0)
        if b > 0: return b
        l = float(row.get("last", 0) or 0); t = float(row.get("theo", 0) or 0)
        return l if l > 0 else (t if t > 0 else 0.0)
    def _ask_used(row):
        a = float(row.get("ask", 0) or 0)
        if a > 0: return a
        l = float(row.get("last", 0) or 0); t = float(row.get("theo", 0) or 0)
        return l if l > 0 else (t if t > 0 else 0.0)

    df["mid_used"] = df.apply(_mid_used, axis=1)
    df["bid_used"] = df.apply(_bid_used, axis=1)
    df["ask_used"] = df.apply(_ask_used, axis=1)
    df["volume"] = df.get("volume", pd.Series(dtype=float)).fillna(0).astype(int)
    df["open_interest"] = df.get("open_interest", pd.Series(dtype=float)).fillna(0).astype(int)
    return df

st.set_page_config(page_title="Iron Butterfly", layout="wide")
st.title(tr("🦋 铁蝶策略筛选", "🦋 Iron Butterfly Screener"))

ticker = st.text_input(tr("股票代码 (Ticker)", "Ticker"), "NVDA").upper()
if not ticker:
    st.stop()
yc = YahooClient(ticker)

with st.expander(tr("什么时候适合用『铁蝶』？（指标建议）", "When to consider an Iron Butterfly?"), expanded=True):
    st.markdown(tr(
        """
**适用市场观点：** 中性或轻微波动，希望**限定最大风险**、赚取**较高权利金**。  
**建议：**
- **IV/IVR：** 中到偏高（IV≥30% 或 IVR≥30–50%）
- **DTE：** 7–20 天（更快衰减）或 20–45 天（更稳）
- **流动性：** 价差 ≤$0.10–$0.30；成交量≥100、未平仓量≥200
- **规避事件：** 避开财报/重磅消息周
        """,
        """
**Outlook:** Neutral to slightly volatile, seeking rich premium with limited risk.  
**Tips:** IV/IVR medium-high; DTE 7–20 or 20–45; avoid earnings week; prefer tight spreads and healthy volume/OI.
        """
    ))

expirations_bt = list(yc.get_expirations() or [])
if not expirations_bt:
    st.error(tr("无法获取期权到期日。", "Failed to fetch expirations."))
    st.stop()
exp_options_bt = [tr("自动（全部到期）", "Auto (All Expirations)")] + expirations_bt
exp_choice_bt = st.selectbox(tr("选择到期日", "Expiration"), exp_options_bt)
selected_exps_bt = expirations_bt if exp_choice_bt == exp_options_bt[0] else [exp_choice_bt]

wing_width_list_text = st.text_input(tr("翼宽列表（逗号分隔）", "Wing widths (comma)"),
                                     value="3,5,10", help=tr("仅使用此列表；示例：3,5,10", "Only values in this list, e.g., 3,5,10"))
min_credit = st.number_input(tr("最小净收权利金（$）", "Min Credit ($)"),
                             min_value=0.0, value=0.20, step=0.05)
max_spread_b = st.slider(tr("最大买卖价差（每腿，$）", "Max per-leg spread ($)"),
                         0.0, 1.0, 0.50, 0.01)
min_volume_b = st.number_input(tr("最小成交量（每腿）", "Min per-leg volume"),
                               min_value=0, value=0, step=10)
allow_shift = st.slider(tr("短腿行权价相对现价的偏移（$）", "AT-the-money strike offset ($)"),
                        -10.0, 10.0, 0.0, 0.5)

if st.button(tr("获取铁蝶候选", "Get Butterfly Candidates")):
    spot_b = yc.get_spot_price()
    all_rows_bt = []
    for exp_bt in selected_exps_bt:
        call_df = yc.get_option_chain(exp_bt, kind="call").copy()
        put_df  = yc.get_option_chain(exp_bt, kind="put").copy()
        if call_df.empty or put_df.empty:
            continue
        T_years_bt = max(1e-6, (pd.to_datetime(exp_bt) - pd.Timestamp.today()).days / 365.0)
        call_df = robust_price_fields(call_df, is_call=True,  S=float(spot_b), T_years=T_years_bt, r=0.05)
        put_df  = robust_price_fields(put_df,  is_call=False, S=float(spot_b), T_years=T_years_bt, r=0.05)

        target_k = float(spot_b) + float(allow_shift)
        def nearest_strike(df, k):
            return float(df.loc[(df["strike"] - k).abs().idxmin(), "strike"])
        K_call = nearest_strike(call_df, target_k)
        K_put  = nearest_strike(put_df,  target_k)
        K = K_put if abs(K_put - target_k) < abs(K_call - target_k) else K_call

        try:
            wing_list = [float(x.strip()) for x in str(wing_width_list_text).split(",") if x.strip() != ""]
        except Exception:
            wing_list = []
        if not wing_list:
            wing_list = [3.0, 5.0, 10.0]

        def row_at(df, k):
            return df.loc[(df["strike"] - k).abs().idxmin()]

        for w in wing_list:
            Ku = nearest_strike(call_df, K + float(w))
            Kd = nearest_strike(put_df,  K - float(w))
            sc = row_at(call_df, K)   # short call @ K
            sp = row_at(put_df,  K)   # short put  @ K
            lc = row_at(call_df, Ku)  # long call  @ K+W
            lp = row_at(put_df,  Kd)  # long put   @ K-W

            legs_ok = all([
                sc["spread"] <= max_spread_b, sp["spread"] <= max_spread_b,
                lc["spread"] <= max_spread_b, lp["spread"] <= max_spread_b,
                sc["volume"] >= min_volume_b, sp["volume"] >= min_volume_b,
                lc["volume"] >= min_volume_b, lp["volume"] >= min_volume_b
            ])

            sc_p = float(sc.get("mid_used", sc.get("mid", 0.0)))
            sp_p = float(sp.get("mid_used", sp.get("mid", 0.0)))
            lc_p = float(lc.get("mid_used", lc.get("mid", 0.0)))
            lp_p = float(lp.get("mid_used", lp.get("mid", 0.0)))
            credit = float(sc_p + sp_p - lc_p - lp_p)
            width  = abs(Ku - K)
            if (not np.isfinite(credit)) or (width <= 0) or (credit <= 0):
                continue

            dte = (pd.to_datetime(exp_bt) - pd.Timestamp.today()).days
            be_low  = float(K - credit)
            be_high = float(K + credit)
            profit_range = f"{round(be_low,2)} ~ {round(be_high,2)}"
            loss_range   = f"< {round(be_low,2)} 或 > {round(be_high,2)}"
            max_profit = credit
            max_loss   = max(width - credit, 0.0)
            ror = (credit / max(1e-9, (width - credit)))
            ann = ror * (365.0 / max(1, dte)) if dte and dte > 0 else np.nan

            all_rows_bt.append({
                "到期": exp_bt, "DTE": dte,
                "卖Call@K": float(K), "卖Put@K": float(K),
                "买Call@K+W": float(Ku), "买Put@K−W": float(Kd),
                "净收权利金Credit($)": round(float(credit), 2),
                "翼宽W($)": round(float(w), 2),
                "最大盈利($)": round(float(max_profit), 2),
                "最大亏损($)": round(float(max_loss), 2),
                "盈亏平衡下界": round(be_low, 2), "盈亏平衡上界": round(be_high, 2),
                "盈利价格范围": profit_range, "亏损价格范围": loss_range,
                "每腿最大价差($)": max(float(sc["spread"]), float(sp["spread"]), float(lc["spread"]), float(lp["spread"])),
                "每腿最小成交量": int(min(sc["volume"], sp["volume"], lc["volume"], lp["volume"])),
                "是否通过流动性检查": "是" if legs_ok else "否",
            })

    res = pd.DataFrame(all_rows_bt)
    if not res.empty:
        res = res[res["净收权利金Credit($)"].fillna(0) >= float(min_credit)]

    st.subheader(tr("✅ 铁蝶候选", "✅ Butterfly Candidates"))
    st.dataframe(res, use_container_width=True)
    if res.empty:
        st.info(tr("可尝试：增大翼宽、降低最小权利金门槛、放宽价差或成交量要求。", "Try wider wings, lower min credit, or looser liquidity hints."))