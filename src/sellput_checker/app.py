import streamlit as st
import pandas as pd
import numpy as np

from sellput_checker.yahoo_client import YahooClient
from sellput_checker.checklist import evaluate_chain_df
from sellput_checker.calculations import bs_d1_d2
from sellput_checker.utils import norm_cdf

# ──────────────────────────────────────────────────────────────────────────────
# Black-Scholes 价格（无分红，兜底用）
# ──────────────────────────────────────────────────────────────────────────────
def bs_price_theo(S: float, K: float, r: float, sigma: float, T: float, is_call: bool) -> float:
    try:
        if S <= 0 or K <= 0 or sigma <= 0 or T <= 0:
            return 0.0
        d1, d2 = bs_d1_d2(float(S), float(K), float(r), float(sigma), float(T))
        if is_call:
            # C = S*N(d1) - K*e^{-rT}*N(d2)
            return float(S) * float(norm_cdf(d1)) - float(K) * np.exp(-float(r) * float(T)) * float(norm_cdf(d2))
        else:
            # P = K*e^{-rT}*N(-d2) - S*N(-d1)
            return float(K) * np.exp(-float(r) * float(T)) * float(norm_cdf(-d2)) - float(S) * float(norm_cdf(-d1))
    except Exception:
        return 0.0

def robust_price_fields(df: pd.DataFrame, is_call: bool, S: float, T_years: float, r: float = 0.05) -> pd.DataFrame:
    """
    为期权链 DataFrame 增加：
      - mid, spread（若未有则计算）
      - last（解析 last/last_price）
      - theo（BS 理论价）
      - mid_used（优先 mid；否则 last；再否则 theo；最后 max(bid,ask)）
      - bid_used（优先 bid；否则 last；否则 theo）
      - ask_used（优先 ask；否则 last；否则 theo）
    """
    df = df.copy()
    # 规范数值列
    for c in ["bid", "ask", "strike", "iv", "volume", "open_interest"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "mid" not in df.columns:
        df["mid"] = (df.get("bid", 0).fillna(0) + df.get("ask", 0).fillna(0)) / 2
    df["spread"] = (df.get("ask", 0).fillna(0) - df.get("bid", 0).fillna(0)).clip(lower=0)
    # last
    if "last" in df.columns:
        df["last"] = pd.to_numeric(df["last"], errors="coerce")
    elif "last_price" in df.columns:
        df = df.rename(columns={"last_price": "last"})
        df["last"] = pd.to_numeric(df["last"], errors="coerce")
    else:
        df["last"] = np.nan
    # theo
    def _theo(row):
        try:
            return bs_price_theo(float(S), float(row.get("strike", 0.0)), 0.05, max(float(row.get("iv", 0.0)), 1e-6), float(T_years), bool(is_call))
        except Exception:
            return 0.0
    df["theo"] = df.apply(_theo, axis=1)
    # used fields
    def _mid_used(row):
        b = float(row.get("bid", 0) or 0)
        a = float(row.get("ask", 0) or 0)
        m = (b + a) / 2.0 if (b > 0 and a > 0) else 0.0
        if m > 0:
            return m
        l = float(row.get("last", 0) or 0)
        if l > 0:
            return l
        t = float(row.get("theo", 0) or 0)
        if t > 0:
            return t
        return max(b, a, 0.0)
    def _bid_used(row):
        b = float(row.get("bid", 0) or 0)
        if b > 0:
            return b
        l = float(row.get("last", 0) or 0)
        if l > 0:
            return l
        t = float(row.get("theo", 0) or 0)
        if t > 0:
            return t
        return 0.0
    def _ask_used(row):
        a = float(row.get("ask", 0) or 0)
        if a > 0:
            return a
        l = float(row.get("last", 0) or 0)
        if l > 0:
            return l
        t = float(row.get("theo", 0) or 0)
        if t > 0:
            return t
        return 0.0
    df["mid_used"] = df.apply(_mid_used, axis=1)
    df["bid_used"] = df.apply(_bid_used, axis=1)
    df["ask_used"] = df.apply(_ask_used, axis=1)
    # 整理成交量/OI
    df["volume"] = df.get("volume", pd.Series(dtype=float)).fillna(0).astype(int)
    df["open_interest"] = df.get("open_interest", pd.Series(dtype=float)).fillna(0).astype(int)
    return df

# ──────────────────────────────────────────────────────────────────────────────
# Language toggle and translation helper
# ──────────────────────────────────────────────────────────────────────────────
LANG_OPTIONS = ["English", "中文"]
lang_mode = st.sidebar.selectbox("Language / 语言", LANG_OPTIONS, index=0)

def tr(cn: str, en: str) -> str:
    """Return a string based on the current language mode (English/中文)."""
    return cn if lang_mode == "中文" else en

# 页面基本设置
st.set_page_config(page_title="Option Strategy Checker", layout="wide")
st.title(tr("期权策略筛选器", "Option Strategy Checker"))

# 模式切换：卖出看跌 / 备兑看涨（Sidebar）
mode_key = st.sidebar.radio(
    tr("模式", "Mode"),
    ["put", "call", "iron_butterfly", "iron_condor"],
    index=0,
    format_func=lambda x: {
        "put": tr("卖出看跌", "Sell Put"),
        "call": tr("备兑看涨", "Covered Call"),
        "iron_butterfly": tr("铁蝶（Iron Butterfly）", "Iron Butterfly"),
        "iron_condor": tr("铁鹰（Iron Condor）", "Iron Condor"),
    }[x]
)

# 子标题：根据模式在 Ticker 输入框之前显示
if mode_key == "call":
    st.subheader(tr("📈 Covered Call 合约筛选", "📈 Covered Call Screener"))
elif mode_key == "iron_butterfly":
    st.subheader(tr("🦋 铁蝶策略筛选（仅中文）", "🦋 Iron Butterfly Screener"))
elif mode_key == "iron_condor":
    st.subheader(tr("🦅 铁鹰策略筛选（仅中文）", "🦅 Iron Condor Screener"))
else:
    st.subheader(tr("📉 卖出看跌合约筛选", "📉 Sell Put Screener"))

# ──────────────────────────────────────────────────────────────────────────────
# 输入区
# ──────────────────────────────────────────────────────────────────────────────
ticker = st.text_input(
    tr("股票代码 (Ticker)", "Ticker"),
    "NVDA",
    help=tr("例如 NVDA、AAPL、TSLA 等", "e.g., NVDA, AAPL, TSLA")
).upper()  # ← 标的代码；越大盘/流动性越好，期权链质量越高

if ticker:
    yc = YahooClient(ticker)

    # ──────────────────────────────────────────────────────────────────────────
    # Covered Call 页面（若选择了“备兑看涨”模式，则渲染并中止后续 Sell Put 页面）
    # ──────────────────────────────────────────────────────────────────────────
    if mode_key == "call":

        # 取到期日
        expirations_cc = list(yc.get_expirations() or [])
        if not expirations_cc:
            st.error(tr("无法获取期权到期日，可能是网络问题或标的无期权。", "Failed to fetch expirations. Network issue or no options available for this ticker."))
            st.stop()
        exp_options_cc = [tr("自动（全部到期）", "Auto (All Expirations)")] + expirations_cc
        exp_choice_cc = st.selectbox(
            tr("选择到期日", "Expiration"),
            exp_options_cc,
            help=tr("常见到期：本周/次周/当月/季度/LEAPS。可保持“自动”以包含全部到期。",
                    "Common expirations: weekly/monthly/quarterly/LEAPS. Keep 'Auto' to include all.")
        )
        selected_exps_cc = expirations_cc if exp_choice_cc == exp_options_cc[0] else [exp_choice_cc]

        # 关键筛选因素（放在上面）
        delta_high_cc = st.slider(
            tr("Delta 上限", "Max Delta"), 0.0, 1.0, 0.30, 0.05,
            help=tr("建议 0.20~0.30，越低=更保守（更远 OTM），越高=更激进（接近 ATM）。",
                    "Suggested 0.20–0.30. Lower = more conservative (further OTM), higher = more aggressive (near ATM).")
        )
        min_premium_usd = st.number_input(
            tr("最小权利金（$）", "Min Premium ($)"), min_value=0.0, value=0.50, step=0.05,
            help=tr("卖出 Call 至少希望拿到的权利金（按中间价计算）。", "Minimum premium you want to receive (based on mid price).")
        )
        iv_min_cc_pct, iv_max_cc_pct = st.slider(
            tr("隐含波动率 IV 区间（%）", "IV Range (%)"), 0.0, 300.0, (0.0, 120.0), 0.5
        )
        iv_min_cc, iv_max_cc = iv_min_cc_pct / 100.0, iv_max_cc_pct / 100.0
        max_spread_cc = st.slider(tr("最大买卖价差（$）", "Max Bid-Ask Spread ($)"), 0.0, 3.0, 0.10, 0.01)
        min_volume_cc = st.number_input(tr("最小成交量", "Min Volume"), min_value=0, value=100, step=10)
        only_otm = st.checkbox(tr("仅显示价外（行权价 ≥ 现价）", "Only show OTM (Strike ≥ Spot)"), value=True)
        min_strike_prem_pct = st.slider(
            tr("行权价相对现价的溢价（%）下限", "Min Strike Premium vs Spot (%)"), 0.0, 50.0, 5.0, 0.5,
            help=tr("溢价% = (行权价 − 现价) / 现价 × 100%（常见 5%~10%）",
                    "Premium% = (Strike − Spot)/Spot × 100% (commonly 5%–10%).")
        )

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
                # 先复用统一评估（此处不使用 put-delta 过滤，稍后覆盖为 call-delta）
                out_cc = evaluate_chain_df(
                    dfc, spot_cc, exp,
                    delta_high=1.0,  # 占位：稍后替换为 call delta 再过滤
                    iv_min=iv_min_cc, iv_max=iv_max_cc,
                    max_spread=max_spread_cc, min_volume=min_volume_cc, min_annual=min_premium_usd * 0.0  # 年化门槛保持与卖PUT无关，此处先不过滤
                )
                all_rows_cc.append(out_cc)

            if not all_rows_cc:
                st.error(tr("未获取到期权链。", "No option chain retrieved."))
                st.stop()
            out_cc = pd.concat(all_rows_cc, ignore_index=True)

            # 计算：行权价相对现价的溢价（%）
            out_cc["strike_premium_pct"] = ((out_cc["strike"] - float(spot_cc)) / float(spot_cc) * 100).round(2)

            # 用 BS 计算 Call Delta（替换掉评估里基于 Put 的 delta）
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

            # 基于参数做过滤
            if only_otm:
                out_cc = out_cc[out_cc["strike"] >= float(spot_cc)]
            out_cc = out_cc[out_cc["mid"].fillna(0) >= float(min_premium_usd)]
            out_cc = out_cc[out_cc["strike_premium_pct"].fillna(-1) >= float(min_strike_prem_pct)]
            out_cc = out_cc[out_cc["delta"].fillna(1.0) <= float(delta_high_cc)]

            # 排序（年化优先，其次溢价%）
            out_cc = out_cc.sort_values(["annualized_return", "strike_premium_pct"], ascending=[False, False])

            # 组装展示列
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

            # 本地化列名
            if lang_mode == "English":
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

            # 覆盖会话态并渲染（Covered Call 专用表）
            st.session_state["last_table_call"] = show_cc

            # 交互表（多选对比）
            select_col_cc = "选择" if lang_mode == "中文" else "Select"
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
                column_config={
                    select_col_cc: st.column_config.CheckboxColumn(
                        label=select_col_cc,
                        help=tr("勾选要对比的合约", "Tick contracts to compare"),
                        default=False,
                    )
                },
                key="coveredcall_editor",
            )
            if st.button(tr("比较所选", "Compare selected")):
                try:
                    chosen_cc = edited_cc[edited_cc[select_col_cc] == True].copy()
                except Exception:
                    chosen_cc = pd.DataFrame()
                if chosen_cc.empty:
                    st.warning(tr("请先勾选至少一条合约", "Please select at least one contract."))
                else:
                    if select_col_cc in chosen_cc.columns:
                        chosen_cc = chosen_cc.drop(columns=[select_col_cc])
                    st.subheader(tr("🆚 所选合约对比", "🆚 Comparison"))
                    st.dataframe(chosen_cc, use_container_width=True)

        # 若尚未生成 Covered Call 列表，则给出提示
        _cc_tbl = st.session_state.get("last_table_call")
        if not (isinstance(_cc_tbl, pd.DataFrame) and not _cc_tbl.empty):
            st.info(tr("点击上方按钮以生成列表。", "Click the button above to generate the list."))
        # 结束 Covered Call 分支，避免继续执行 Sell Put 页面
        st.stop()

    # ──────────────────────────────────────────────────────────────────────────
    # 铁蝶（Iron Butterfly）页面
    # ──────────────────────────────────────────────────────────────────────────
    elif mode_key == "iron_butterfly":
        # 说明与使用场景（中文）
        with st.expander("什么时候适合用『铁蝶』？（指标建议）", expanded=True):
            st.markdown("""
            **适用市场观点：** 中性或轻微波动（不强趋势），希望 **限定最大风险**、赚取 **较高权利金**。  
            **建议指标范围：**
            - **IV / IV Rank：** 中到偏高（例如 IV≥30% 或 IVR≥30–50%），越高越有利于收取较厚权利金  
            - **到期天数（DTE）：** 常见 **7–20 天**（更快释放时间价值）或 **20–45 天**（更稳健）  
            - **流动性：** 价差 **≤$0.10–$0.30**；**成交量 ≥100**、**未平仓量 ≥200**  
            - **事件规避：** 尽量避开财报 / 重磅事件当周  
            
            **结构：**  
            卖出 **ATM 看涨** + **ATM 看跌**（同一行权价 K，构成短跨式），同时 **买入** 两翼（K+W 与 K−W）的保护腿，形成 **买卖价差对称的蝶形**。  
            
            **风险回报：**  
            - **最大收益：** 净收权利金（Credit）  
            - **最大亏损：** 翼宽（W） − Credit  
            - **盈亏平衡：** 约在 **K ± Credit**  
            - **胜率直觉：** 越高的 **Credit/W**，潜在胜率越低；反之越高  
            """)
        
        # 选择到期
        expirations_bt = list(yc.get_expirations() or [])
        if not expirations_bt:
            st.error("无法获取期权到期日，可能是网络问题或标的无期权。")
            st.stop()
        exp_options_bt = [tr("自动（全部到期）", "Auto (All Expirations)")] + expirations_bt
        exp_choice_bt = st.selectbox(tr("选择到期日", "Expiration"), exp_options_bt)
        selected_exps_bt = expirations_bt if exp_choice_bt == exp_options_bt[0] else [exp_choice_bt]
        
        # 参数（默认更宽松以避免空结果）
        wing_width_list_text = st.text_input("翼宽列表（逗号分隔）", value="3,5,10", help="仅使用此列表；示例：3,5,10")
        min_credit = st.number_input("最小净收权利金（$）", min_value=0.0, value=0.20, step=0.05, help="若无结果，可先降到 $0.20 或增大翼宽。")
        max_spread_b = st.slider("最大买卖价差（每腿，$）", 0.0, 1.0, 0.50, 0.01, help="仅用于流动性诊断，不直接过滤结果。")
        min_volume_b = st.number_input("最小成交量（每腿）", min_value=0, value=0, step=10, help="仅用于流动性诊断，不直接过滤结果。")
        allow_shift = st.slider("短腿行权价相对现价的偏移（$）", -10.0, 10.0, 0.0, 0.5, help="0 表示严格 ATM；正值=向上偏移，负值=向下偏移。")
        
        if st.button("获取铁蝶候选"):
            spot_b = yc.get_spot_price()
            all_rows_bt = []
            for exp_bt in selected_exps_bt:
                call_df = yc.get_option_chain(exp_bt, kind="call").copy()
                put_df  = yc.get_option_chain(exp_bt, kind="put").copy()
                if call_df.empty or put_df.empty:
                    continue
                # 统一增强预处理：含 last/theo/bid_used/ask_used/mid_used
                T_years_bt = max(1e-6, (pd.to_datetime(exp_bt) - pd.Timestamp.today()).days / 365.0)
                call_df = robust_price_fields(call_df, is_call=True,  S=float(spot_b), T_years=T_years_bt, r=0.05)
                put_df  = robust_price_fields(put_df,  is_call=False, S=float(spot_b), T_years=T_years_bt, r=0.05)
                # 选择短腿 K
                target_k = float(spot_b) + float(allow_shift)
                def nearest_strike(df, k):
                    return float(df.loc[(df["strike"] - k).abs().idxmin(), "strike"])
                K = nearest_strike(call_df, target_k)
                K_put = nearest_strike(put_df, target_k)
                if abs(K_put - target_k) < abs(K - target_k):
                    K = K_put
                # 解析翼宽列表
                try:
                    wing_list = [float(x.strip()) for x in str(wing_width_list_text).split(",") if x.strip() != ""]
                except Exception:
                    wing_list = []
                if not wing_list:
                    # 若用户清空输入，使用保守默认
                    wing_list = [3.0, 5.0, 10.0]

                def row_at(df, k):
                    return df.loc[(df["strike"] - k).abs().idxmin()]

                # 针对多个翼宽逐一生成候选
                for w in wing_list:
                    up_wing_strike   = K + float(w)
                    down_wing_strike = K - float(w)
                    Ku = nearest_strike(call_df, up_wing_strike)
                    Kd = nearest_strike(put_df,  down_wing_strike)
                    sc = row_at(call_df, K)   # 卖 Call@K
                    sp = row_at(put_df,  K)   # 卖 Put@K
                    lc = row_at(call_df, Ku)  # 买 Call@K+W
                    lp = row_at(put_df,  Kd)  # 买 Put@K−W
                    # 检查（仅作标注）
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
                    # 盈亏
                    be_low  = float(K - credit)
                    be_high = float(K + credit)
                    profit_range = f"{round(be_low,2)} 至 {round(be_high,2)}"
                    loss_range   = f"小于 {round(be_low,2)} 或 大于 {round(be_high,2)}"
                    max_profit = credit
                    max_loss   = max(width - credit, 0.0)
                    ror = (credit / max(1e-9, (width - credit)))
                    ann = ror * (365.0 / max(1, dte)) if dte and dte > 0 else np.nan
                    # 结果行
                    all_rows_bt.append({
                        "到期": exp_bt,
                        "DTE": dte,
                        "卖Call@K": float(K),
                        "卖Put@K": float(K),
                        "买Call@K+W": float(Ku),
                        "买Put@K−W": float(Kd),
                        "净收权利金Credit($)": round(float(credit), 2),
                        "翼宽W($)": round(float(w), 2),
                        "最大盈利($)": round(float(max_profit), 2),
                        "最大亏损($)": round(float(max_loss), 2),
                        "盈亏平衡下界": round(be_low, 2),
                        "盈亏平衡上界": round(be_high, 2),
                        "盈利价格范围": profit_range,
                        "亏损价格范围": loss_range,
                        "每腿最大价差($)": max(float(sc["spread"]), float(sp["spread"]), float(lc["spread"]), float(lp["spread"])),
                        "每腿最小成交量": int(min(sc["volume"], sp["volume"], lc["volume"], lp["volume"])),
                        "是否通过流动性检查": "是" if legs_ok else "否",
                    })
            res = pd.DataFrame(all_rows_bt)
            if not res.empty:
                res = res[res["净收权利金Credit($)"].fillna(0) >= float(min_credit)]
            st.subheader("✅ 铁蝶候选")
            st.dataframe(res, use_container_width=True)
            if res.empty:
                st.info("可尝试：增大翼宽、降低最小权利金门槛、放宽价差或成交量要求。")
        
        st.stop()

    # ──────────────────────────────────────────────────────────────────────────
    # 铁鹰（Iron Condor）页面
    # ──────────────────────────────────────────────────────────────────────────
    elif mode_key == "iron_condor":
        # 说明与使用场景（中文）
        with st.expander("什么时候适合用『铁鹰』？（指标建议）", expanded=True):
            st.markdown("""
            **适用市场观点：** 中性或「区间震荡」，认为标的 **不会大幅单边**。  
            **建议指标范围：**
            - **短腿 Delta：** **0.15–0.30**（两侧对称），越小越保守  
            - **翼宽（$）：** 固定宽度（如 $3 / $5 / $10），越宽越保守  
            - **IV / IV Rank：** 中到偏高（IVR≥30–50% 更佳）  
            - **到期天数（DTE）：** 常见 **20–45 天**（时间价值衰减与风险平衡）  
            - **目标净收：** 一般 **Credit 占翼宽的 20–35%**  
            - **流动性：** 价差 **≤$0.10–$0.30**；**成交量 ≥100**、**未平仓量 ≥200**  
            - **事件规避：** 避开财报/重磅消息当周
            
            **结构：**  
            PUT 端：卖出较高 Delta 的看跌，买入更低行权价的看跌（形成牛市看跌价差）  
            CALL 端：卖出较高 Delta 的看涨，买入更高行权价的看涨（形成熊市看涨价差）  
            两侧组成 **有限风险** 的铁鹰。  
            
            **风险回报（对称翼宽）**  
            - **最大收益：** 净收权利金（Credit）  
            - **最大亏损：** 翼宽（W） − Credit  
            - **胜率直觉：** 约 **1 − Credit/W**  
            """)
        
        expirations_ic = list(yc.get_expirations() or [])
        if not expirations_ic:
            st.error("无法获取期权到期日，可能是网络问题或标的无期权。")
            st.stop()
        exp_options_ic = [tr("自动（全部到期）", "Auto (All Expirations)")] + expirations_ic
        exp_choice_ic = st.selectbox(tr("选择到期日", "Expiration"), exp_options_ic)
        selected_exps_ic = expirations_ic if exp_choice_ic == exp_options_ic[0] else [exp_choice_ic]
        
        # 参数（默认更宽松以避免空结果）
        short_delta_low, short_delta_high = st.slider("短腿 |Delta| 目标区间", 0.00, 0.60, (0.10, 0.35), 0.01)
        wing_width_list_text_ic = st.text_input("翼宽列表（逗号分隔）", value="3,5,10,15,20", help="仅使用此列表；示例：3,5,10")
        min_credit_ic = st.number_input("最小净收权利金（$）", min_value=0.0, value=0.20, step=0.05, help="若无结果，可先降到 $0.20 或增大翼宽。")
        max_spread_ic = st.slider("最大买卖价差（每腿，$）", 0.0, 1.0, 0.30, 0.01, help="仅用于流动性诊断，不直接过滤结果。")
        min_volume_ic = st.number_input("最小成交量（每腿）", min_value=0, value=100, step=10, help="仅用于流动性诊断，不直接过滤结果。")
        top_k_short_ic = st.number_input(
            "每侧短腿候选数", min_value=1, max_value=10, value=3, step=1,
            help="从各侧最优短腿中取前 N 个进行两两配对，生成多组铁鹰候选"
        )
        
        if st.button("获取铁鹰候选"):
            spot_ic = yc.get_spot_price()
            all_rows_ic = []
            for exp_ic in selected_exps_ic:
                call_df = yc.get_option_chain(exp_ic, kind="call").copy()
                put_df  = yc.get_option_chain(exp_ic, kind="put").copy()
                if call_df.empty or put_df.empty:
                    continue
                T_years_ic = max(1e-6, (pd.to_datetime(exp_ic) - pd.Timestamp.today()).days / 365.0)
                call_df = robust_price_fields(call_df, is_call=True,  S=float(spot_ic), T_years=T_years_ic, r=0.05)
                put_df  = robust_price_fields(put_df,  is_call=False, S=float(spot_ic), T_years=T_years_ic, r=0.05)
                # Delta
                def add_delta(df, is_call: bool):
                    rows = []
                    for _, row in df.iterrows():
                        try:
                            S = float(spot_ic)
                            K = float(row.get("strike", 0.0))
                            sigma = float(row.get("iv", 0.0))
                            T = max(1e-6, (pd.to_datetime(exp_ic) - pd.Timestamp.today()).days / 365.0)
                            r = 0.05
                            d1, _ = bs_d1_d2(S, K, r, max(sigma, 1e-6), T)
                            if is_call:
                                delta = float(norm_cdf(d1))
                            else:
                                delta = float(1.0 - norm_cdf(d1))
                        except Exception:
                            delta = np.nan
                        rows.append(delta)
                    df["delta_abs"] = pd.Series(rows, index=df.index)
                add_delta(call_df, True)
                add_delta(put_df, False)
                # --- Improved short-leg selection (maximize credit within Delta band & keep OTM) ---
                target_delta = (short_delta_low + short_delta_high) / 2.0

                def _short_put_candidates(df, top_k: int) -> pd.DataFrame:
                    S = float(spot_ic)
                    # 1) Strict OTM + Delta band
                    cand = df[(df["strike"] < S) & (df["delta_abs"] >= short_delta_low) & (df["delta_abs"] <= short_delta_high)].copy()
                    # 2) If empty, relax Delta but keep OTM
                    if cand.empty:
                        cand = df[df["strike"] < S].copy()
                    # 3) If still empty, return nearest-below
                    if cand.empty:
                        below = df[df["strike"] < S].copy()
                        if not below.empty:
                            # just return the single nearest-below
                            below["k_gap"] = (below["strike"] - S).abs()
                            return below.sort_values(["k_gap"], ascending=[True]).head(1)
                    # Sort by premium then higher strike
                    price_col = "mid_used" if "mid_used" in cand.columns else ("bid_used" if "bid_used" in cand.columns else "mid")
                    cand["_price_for_sort"] = pd.to_numeric(cand.get(price_col, 0), errors="coerce").fillna(0)
                    cand = cand.sort_values(["_price_for_sort", "strike"], ascending=[False, False])
                    return cand.head(int(top_k)).copy()

                def _short_call_candidates(df, top_k: int) -> pd.DataFrame:
                    S = float(spot_ic)
                    # 1) Strict OTM + Delta band
                    cand = df[(df["strike"] > S) & (df["delta_abs"] >= short_delta_low) & (df["delta_abs"] <= short_delta_high)].copy()
                    # 2) If empty, relax Delta but keep OTM
                    if cand.empty:
                        cand = df[df["strike"] > S].copy()
                    # 3) If still empty, return nearest-above
                    if cand.empty:
                        above = df[df["strike"] > S].copy()
                        if not above.empty:
                            # just return the single nearest-above
                            above["k_gap"] = (above["strike"] - S).abs()
                            return above.sort_values(["k_gap"], ascending=[True]).head(1)
                    # Sort by premium then lower strike (closer to spot)
                    price_col = "mid_used" if "mid_used" in cand.columns else ("bid_used" if "bid_used" in cand.columns else "mid")
                    cand["_price_for_sort"] = pd.to_numeric(cand.get(price_col, 0), errors="coerce").fillna(0)
                    cand = cand.sort_values(["_price_for_sort", "strike"], ascending=[False, True])
                    return cand.head(int(top_k)).copy()

                put_cands  = _short_put_candidates(put_df,  top_k_short_ic)
                call_cands = _short_call_candidates(call_df, top_k_short_ic)

                # Build combinations of (sp, sc)
                combo_pairs = []
                for _, sp_row in put_cands.iterrows():
                    for _, sc_row in call_cands.iterrows():
                        sp_k = float(sp_row.get("strike", -1e9))
                        sc_k = float(sc_row.get("strike",  1e9))
                        # enforce iron condor shape (put < call). If not, try to skip
                        if sp_k >= sc_k:
                            continue
                        combo_pairs.append((sp_row, sc_row))

                # if nothing passed (e.g., malformed chain), fall back to nearest-OTM pair
                if not combo_pairs:
                    try:
                        # nearest-below for put, nearest-above for call
                        S = float(spot_ic)
                        below = put_df[put_df["strike"] < S].copy()
                        above = call_df[call_df["strike"] > S].copy()
                        if not below.empty and not above.empty:
                            below["k_gap"] = (below["strike"] - S).abs()
                            above["k_gap"] = (above["strike"] - S).abs()
                            sp_fallback = below.sort_values(["k_gap"], ascending=[True]).iloc[0]
                            sc_fallback = above.sort_values(["k_gap"], ascending=[True]).iloc[0]
                            combo_pairs = [(sp_fallback, sc_fallback)]
                    except Exception:
                        combo_pairs = []

                def nearest_row(df, target_strike):
                    idx = (df["strike"] - target_strike).abs().idxmin()
                    return df.loc[idx]
                # 解析翼宽列表
                try:
                    wing_list_ic = [float(x.strip()) for x in str(wing_width_list_text_ic).split(",") if x.strip() != ""]
                except Exception:
                    wing_list_ic = []
                if not wing_list_ic:
                    wing_list_ic = [3.0, 5.0, 10.0]

                for sp, sc in combo_pairs:
                    # Recompute a quick sanity check per pair (keep OTM)
                    try:
                        S = float(spot_ic)
                        if float(sp.get("strike", -1e9)) >= S:
                            below = put_df[put_df["strike"] < S].copy()
                            if not below.empty:
                                sp = below.loc[below["strike"].idxmax()]
                        if float(sc.get("strike", 1e9)) <= S:
                            above = call_df[call_df["strike"] > S].copy()
                            if not above.empty:
                                sc = above.loc[above["strike"].idxmin()]
                    except Exception:
                        pass

                    # Prevent accidental iron butterfly (call <= put)
                    try:
                        if float(sp["strike"]) >= float(sc["strike"]):
                            # push call up minimally
                            try:
                                strikes = np.sort(call_df["strike"].dropna().unique())
                                step = float(np.min(np.diff(strikes))) if len(strikes) >= 2 else 1.0
                            except Exception:
                                step = 1.0
                            sc = call_df.loc[(call_df["strike"] - (float(sp["strike"]) + step)).abs().idxmin()]
                    except Exception:
                        pass

                    for w in wing_list_ic:
                        lp = nearest_row(put_df,  float(sp["strike"]) - float(w))   # 买 Put（保护）
                        lc = nearest_row(call_df, float(sc["strike"]) + float(w))   # 买 Call（保护）
                        # 流动性标注
                        legs_ok = all([
                            float(sp["spread"]) <= max_spread_ic, float(lp["spread"]) <= max_spread_ic,
                            float(sc["spread"]) <= max_spread_ic, float(lc["spread"]) <= max_spread_ic,
                            int(sp["volume"]) >= min_volume_ic,   int(lp["volume"]) >= min_volume_ic,
                            int(sc["volume"]) >= min_volume_ic,   int(lc["volume"]) >= min_volume_ic,
                        ])
                        sp_p = float(sp.get("mid_used", sp.get("mid", 0.0)))
                        lp_p = float(lp.get("mid_used", lp.get("mid", 0.0)))
                        sc_p = float(sc.get("mid_used", sc.get("mid", 0.0)))
                        lc_p = float(lc.get("mid_used", lc.get("mid", 0.0)))
                        credit = float(sp_p - lp_p + sc_p - lc_p)
                        width  = float(w)
                        if (not np.isfinite(credit)) or (width <= 0) or (credit <= 0):
                            continue
                        dte = (pd.to_datetime(exp_ic) - pd.Timestamp.today()).days
                        be_low  = float(sp["strike"]) - float(credit)
                        be_high = float(sc["strike"]) + float(credit)
                        profit_range = f"{round(be_low,2)} 至 {round(be_high,2)}"
                        loss_range   = f"小于 {round(be_low,2)} 或 大于 {round(be_high,2)}"
                        max_profit = credit
                        max_loss   = max(width - credit, 0.0)
                        ror = (credit / max(1e-9, (width - credit)))
                        ann = ror * (365.0 / max(1, dte)) if dte and dte > 0 else np.nan
                        all_rows_ic.append({
                            "到期": exp_ic,
                            "DTE": dte,
                            "卖Put(短PUT)": float(sp["strike"]),
                            "买Put(保护)": float(lp["strike"]),
                            "卖Call(短CALL)": float(sc["strike"]),
                            "买Call(保护)": float(lc["strike"]),
                            "净收权利金Credit($)": round(float(credit), 2),
                            "翼宽W($)": round(float(w), 2),
                            "最大盈利($)": round(float(max_profit), 2),
                            "最大亏损($)": round(float(max_loss), 2),
                            "盈亏平衡下界": round(be_low, 2),
                            "盈亏平衡上界": round(be_high, 2),
                            "盈利价格范围": profit_range,
                            "亏损价格范围": loss_range,
                            "每腿最大价差($)": max(float(sp["spread"]), float(lp["spread"]), float(sc["spread"]), float(lc["spread"])) ,
                            "每腿最小成交量": int(min(sp["volume"], lp["volume"], sc["volume"], lc["volume"])) ,
                            "是否通过流动性检查": "是" if legs_ok else "否",
                        })
            res = pd.DataFrame(all_rows_ic)
            if not res.empty:
                res = res[res["净收权利金Credit($)"].fillna(0) >= float(min_credit_ic)]
            st.subheader("✅ 铁鹰候选")
            st.dataframe(res, use_container_width=True)
            if res.empty:
                st.info("可尝试：增大翼宽、降低最小权利金门槛、放宽价差或成交量要求、调整 Delta 区间。")
        
        st.stop()

    expirations = list(yc.get_expirations() or [])
    exp_options = [tr("自动（全部到期）", "Auto (All Expirations)")] + expirations
    if not expirations:
        st.error(tr("无法获取期权到期日，可能是网络问题或标的无期权。", "Failed to fetch expirations. Network issue or no options available for this ticker."))
        st.stop()

    # 到期日选择（可选）：支持“自动（全部到期）”
    exp_options = [tr("自动（全部到期）", "Auto (All Expirations)")] + expirations
    exp_choice = st.selectbox(
        tr("选择到期日", "Expiration"),
        exp_options,
        help=tr("常见到期：本周/次周/当月/季度/LEAPS。可保持“自动”以包含全部到期；期限越长，年化一般越低但更稳定。", "Common expirations: weekly/monthly/quarterly/LEAPS. Keep 'Auto' to include all expirations; longer DTE often means lower annualized but more stability.")
    )  # ← 不强制选择；默认自动
    if exp_choice == exp_options[0]:
        selected_exps = expirations
    else:
        selected_exps = [exp_choice]

    # 参数（注意用百分比展示）
    delta_high = st.slider(
        tr("Delta 上限", "Max |Delta|"),
        0.0, 1.0, 0.35, 0.05,
        help=tr("建议范围：0.1~0.4。数值越小=更保守（更远虚值，较小权利金）；越大=更激进（更近或价内，较大权利金）。", "Suggested: 0.1–0.4. Lower = more conservative (further OTM, smaller premium); higher = more aggressive (closer/ITM, larger premium).")
    )  # ← 对卖PUT而言，|Δ|越小=越保守，越大=越激进

    min_annual_percent = st.slider(
        tr("最小年化收益率（%）", "Min Annualized Return (%)"),
        0.0, 200.0, 15.0, 0.5,
        help=tr("建议范围：10%~40%。设得越高=筛得越严，可能无可选合约；越低=更容易命中但报酬降低。", "Suggested: 10%–40%. Higher = stricter, might filter out all; lower = easier to pass but less reward.")
    )  # ← 年化门槛；高=追求高回报，低=更容易满足
    min_annual = min_annual_percent / 100.0  # ← 转为小数，供后续 evaluate_chain_df 使用

    iv_min_percent, iv_max_percent = st.slider(
        tr("隐含波动率 IV 区间（%）", "IV Range (%)"),
        0.0, 300.0, (0.0, 120.0), 0.5,
        help=tr("建议范围：20%~120%。IV 越高=权利金越丰但波动/风险更大；IV 越低=更稳但权利金偏少。", "Suggested: 20%–120%. Higher IV = richer premium but more volatility/risk; lower IV = steadier but smaller premium.")
    )  # ← IV过滤；高IV=高权利金高风险，低IV=低权利金低风险
    iv_min = iv_min_percent / 100.0  # ← 转为小数
    iv_max = iv_max_percent / 100.0  # ← 转为小数

    max_spread = st.slider(
        tr("最大买卖价差（美元）", "Max Bid-Ask Spread ($)"),
        0.0, 3.0, 0.10, 0.01,
        help=tr("建议范围：$0.05~$0.30。越小=流动性越好、滑点小；越大=成交更困难、真实收益打折。", "Suggested: $0.05–$0.30. Smaller = better liquidity, less slippage; larger = harder fills, lower realized return.")
    )  # ← 价差上限；小=流动性友好，大=滑点风险高

    min_volume = st.number_input(
        tr("最小成交量", "Min Volume"),
        min_value=0, value=100, step=10,
        help=tr("建议范围：50~500。越高=要求更活跃的合约，成交更容易；越低=可能较难成交。", "Suggested: 50–500. Higher = more active contracts, easier fills; lower = fills may be harder.")
    )  # ← 成交量门槛；高=更活跃更易成交，低=成交可能困难
    # 提示：行权价相对现价的折价比例
    st.caption(tr(
        "折价百分比 = (现价 − 行权价) / 现价。通常选择比现价低 5%~15%。",
        "Discount = (Spot − Strike) / Spot. Common choice: 5%–15% below spot."
    ))

    if st.button(tr("获取推荐合约", "Get Sell Put Suggestions")):
        # ──────────────────────────────────────────────────────────────────────
        # 数据抓取
        # ──────────────────────────────────────────────────────────────────────
        spot = yc.get_spot_price()  # ← 标的现价；越高说明标的价格越高

        # 拉取所选到期（或全部到期）的期权链并合并
        all_rows = []
        for exp in selected_exps:
            df = yc.get_option_chain(exp, kind="put")
            if df.empty:
                continue

            # 标记ticker
            df["ticker"] = ticker

            # 预处理：把 volume / open_interest 的 NaN / 非数字转为 0，避免下游 int() 报错
            df["volume"] = pd.to_numeric(df.get("volume", pd.Series(dtype=float)), errors="coerce").fillna(0).astype(int)    # ← 成交量；高=更活跃更易成交
            df["open_interest"] = pd.to_numeric(df.get("open_interest", pd.Series(dtype=float)), errors="coerce").fillna(0).astype(int)  # ← 未平仓量；高=更活跃更稳价

            # 兜底：确保 bid/ask/strike/iv/delta 列是数值
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

        # # 调试信息：查看报价质量
        # try:
        #     st.sidebar.markdown("**[DEBUG] Quotes Overview**")
        #     st.sidebar.write("Rows:", len(out))
        #     st.sidebar.write("Bid>0 count:", int((out["bid"] > 0).sum()))
        #     st.sidebar.write("Ask>0 count:", int((out["ask"] > 0).sum()))
        #     st.sidebar.write("Mid>0 count:", int((out["mid"] > 0).sum()) if "mid" in out.columns else "N/A")
        #     st.sidebar.write("Spread>0 count:", int((out["spread"] > 0).sum()) if "spread" in out.columns else "N/A")
        # except Exception:
        #     pass

        # # 各单项条件的通过统计（在最终筛选前）
        # try:
        #     if all(col in out.columns for col in ["ok_delta", "ok_iv", "ok_spread", "ok_volume", "ok_annual"]):
        #         st.sidebar.markdown("**[DEBUG] Per-criterion pass (pre-filter)**")
        #         st.sidebar.write("ok_delta:", int(out["ok_delta"].sum()))
        #         st.sidebar.write("ok_iv:", int(out["ok_iv"].sum()))
        #         st.sidebar.write("ok_spread:", int(out["ok_spread"].sum()))
        #         st.sidebar.write("ok_volume:", int(out["ok_volume"].sum()))
        #         st.sidebar.write("ok_annual:", int(out["ok_annual"].sum()))
        # except Exception:
        #     pass

        # # 诊断：条件交集与失败原因分布（在最终筛选前）
        # try:
        #     need_cols = ["ok_delta", "ok_iv", "ok_spread", "ok_volume", "ok_annual"]
        #     if all(c in out.columns for c in need_cols):
        #         m = out[need_cols].astype(bool)
        #         st.sidebar.markdown("**[DEBUG] Intersections (pre-filter)**")
        #         both_va = int((m["ok_volume"] & m["ok_annual"]).sum())
        #         both_vd = int((m["ok_volume"] & m["ok_delta"]).sum())
        #         both_vs = int((m["ok_volume"] & m["ok_spread"]).sum())
        #         both_vi = int((m["ok_volume"] & m["ok_iv"]).sum())
        #         both_ad = int((m["ok_annual"] & m["ok_delta"]).sum())
        #         both_as = int((m["ok_annual"] & m["ok_spread"]).sum())
        #         both_ai = int((m["ok_annual"] & m["ok_iv"]).sum())
        #         st.sidebar.write("ok_volume ∩ ok_annual:", both_va)
        #         st.sidebar.write("ok_volume ∩ ok_delta:", both_vd)
        #         st.sidebar.write("ok_volume ∩ ok_spread:", both_vs)
        #         st.sidebar.write("ok_volume ∩ ok_iv:", both_vi)
        #         st.sidebar.write("ok_annual ∩ ok_delta:", both_ad)
        #         st.sidebar.write("ok_annual ∩ ok_spread:", both_as)
        #         st.sidebar.write("ok_annual ∩ ok_iv:", both_ai)

        #         # 失败原因标签（每行列出未通过的条件）
        #         fail_cols = []
        #         for c in need_cols:
        #             fail_cols.append(np.where(m[c], "", c.replace("ok_", "")))
        #         # 逐列拼接失败标签
        #         fail_tags = fail_cols[0]
        #         for arr in fail_cols[1:]:
        #             fail_tags = np.core.defchararray.add(
        #                 np.where((fail_tags != "") & (arr != ""), fail_tags + "|", fail_tags), arr
        #             )
        #         out["why_not"] = pd.Series(fail_tags, index=out.index)

        #         # 展示最常见的失败组合 Top 10
        #         top_fail = (
        #             out["why_not"]
        #             .replace("", "pass_all")
        #             .value_counts()
        #             .head(10)
        #             .rename_axis("failed_reasons")
        #             .reset_index(name="count")
        #         )
        #         st.sidebar.markdown("**[DEBUG] Top failure combos**")
        #         for _, row in top_fail.iterrows():
        #             st.sidebar.write(f"{row['failed_reasons']}: {int(row['count'])}")
        # except Exception:
        #     pass

        # 百分比转小数
        # (Removed redundant conversion here)

        # 合并后的结果再统一做最终筛选
        out = out[out["ok_all"] == True]
        # 计算：行权价相对现价的折价（%）
        try:
            if 'spot' in locals() and spot and float(spot) > 0:
                out["discount_pct"] = ((float(spot) - out["strike"]) / float(spot) * 100).round(2)
            else:
                out["discount_pct"] = np.nan
        except Exception:
            out["discount_pct"] = np.nan

        # 额外可选过滤：只保留有真实 B/A 的合约；或隐藏 THEO 兜底的行
        require_ba = st.sidebar.checkbox(
            tr("只保留有买卖双边报价 (B/A)", "Require real Bid & Ask"), value=False,
            help=tr("开启后，仅显示同时有买价和卖价的合约。","Show rows that have both Bid and Ask > 0.")
        )
        hide_theo = st.sidebar.checkbox(
            tr("隐藏 THEO 兜底行", "Hide THEO fallback rows"), value=False,
            help=tr("不展示 price_source 为 THEO（理论价兜底）的行。","Hide rows where price_source is THEO (theoretical fallback).")
        )
        if require_ba and ("bid" in out.columns) and ("ask" in out.columns):
            out = out[(out["bid"].fillna(0) > 0) & (out["ask"].fillna(0) > 0)]
        if hide_theo and ("price_source" in out.columns):
            out = out[out["price_source"].astype(str).str.upper() != "THEO"]

        # 排序选项
        sort_key = st.sidebar.selectbox(
            tr("排序字段", "Sort by"),
            [
                "annualized_return", "single_return", "days_to_exp",
                "iv", "delta", "assign_prob_est", "volume", "open_interest", "strike"
            ], index=0
        )
        sort_asc = st.sidebar.checkbox(tr("升序排序", "Ascending order"), value=False)
        if sort_key in out.columns:
            out = out.sort_values(sort_key, ascending=sort_asc, kind="mergesort")

        # 若仍为 0，给出放宽建议（只显示提示，不改变参数）
        if len(out) == 0:
            try:
                st.info(tr(
                    "当前筛选过于严格。可以尝试：将最小年化调低至 5–10%、将最大价差放宽到 $0.30–$0.50、将最小成交量降到 20–50，或把 Delta 上限调到 0.40。",
                    "Filters look too strict. Try: Min Annualized 5–10%, Max Spread $0.30–$0.50, Min Volume 20–50, and/or Max |Delta| up to 0.40."
                ))
            except Exception:
                pass

        # 调试信息（已筛选后的数据）
        # try:
        #     st.sidebar.markdown("**[DEBUG] Filtered (ok_all=True)**")
        #     st.sidebar.write("Rows (filtered):", len(out))
        #     st.sidebar.write("Bid>0 (filtered):", int((out["bid"] > 0).sum()))
        #     st.sidebar.write("Ask>0 (filtered):", int((out["ask"] > 0).sum()))
        #     st.sidebar.write("Mid>0 (filtered):", int((out["mid"] > 0).sum()) if "mid" in out.columns else "N/A")
        #     st.sidebar.write("Spread>0 (filtered):", int((out["spread"] > 0).sum()) if "spread" in out.columns else "N/A")
        # except Exception:
        #     pass

        # 展示列（包含折价%）
        use_display = st.sidebar.checkbox(
            tr("使用 Last 兜底显示 Bid/Ask", "Use 'Last' fallback for Bid/Ask display"),
            value=True,
            help=tr("当夜间 Yahoo 报价缺失时，用 last 作为展示占位，不影响筛选逻辑。",
                    "When Yahoo nightly quotes are missing, show 'last' as placeholder without affecting filters.")
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

        # 友好百分比显示（复制一份显示用 DataFrame）
        show = out[cols].copy()
        if not show.empty:
            show["iv"] = (show["iv"] * 100).round(2)                     # ← 隐含波动率（%）
            show["delta"] = (show["delta"] * 100).round(2)               # ← Delta（%）
            show["assign_prob_est"] = (show["assign_prob_est"] * 100).round(2)
            show["itm_prob"] = (show["itm_prob"] * 100).round(2)         # ← 到期价内概率（%）
            show["single_return"] = (show["single_return"] * 100).round(2)       # ← 单期收益率（%）
            show["annualized_return"] = (show["annualized_return"] * 100).round(2) # ← 年化（%）

        # 显示清理：若原始 Bid/Ask 都为 0，则在展示时把 Bid/Ask 设为 NaN；Spread 的 0 显示为空白
        try:
            bid_col = "bid_display" if (use_display and has_disp) else "bid"
            ask_col = "ask_display" if (use_display and has_disp) else "ask"
            spr_col = "spread_display" if (use_display and has_disp) else "spread"

            if bid_col in out.columns and ask_col in out.columns and bid_col in show.columns and ask_col in show.columns:
                zero_ba = (out.get("bid", pd.Series(dtype=float)).fillna(0) == 0) & (out.get("ask", pd.Series(dtype=float)).fillna(0) == 0)
                if zero_ba.any():
                    show.loc[zero_ba, [bid_col, ask_col]] = np.nan
            if spr_col in show.columns:
                show.loc[show[spr_col] == 0, spr_col] = np.nan
        except Exception:
            pass

        # 本地化列名（根据语言模式）
        if lang_mode == "English":
            cols_map = {
                "contract_symbol": "Contract",
                "strike": "Strike",
                "discount_pct": "Strike Discount vs Spot (%)",
                "bid": "Bid",
                "ask": "Ask",
                "mid": "Mid",
                "iv": "IV (%)",
                "delta": "Delta (%)",
                "itm_prob": "ITM Prob (%)",
                "days_to_exp": "DTE",
                "margin_cash_secured": "Cash Secured ($)",
                "single_return": "Period Return (%)",
                "annualized_return": "Annualized (%)",
                "spread": "Spread ($)",
                "volume": "Volume",
                "open_interest": "OI",
                "price_source": "Price Src",
                "assign_prob_est": "Assign Prob ~|Δ| (%)",
                "bid_display": "Bid (disp)",
                "ask_display": "Ask (disp)",
                "spread_display": "Spread (disp)",
            }
        elif lang_mode == "中文":
            cols_map = {
                "contract_symbol": "合约代码",
                "strike": "行权价",
                "discount_pct": "相对现价折价（%）",
                "bid": "买价",
                "ask": "卖价",
                "mid": "中间价",
                "iv": "隐含波动率（%）",
                "delta": "Delta（%）",
                "itm_prob": "价内概率（%）",
                "days_to_exp": "剩余天数",
                "margin_cash_secured": "现金担保（$）",
                "single_return": "单期收益率（%）",
                "annualized_return": "年化（%）",
                "spread": "价差（$）",
                "volume": "成交量",
                "open_interest": "未平仓量",
                "price_source": "价格来源",
                "assign_prob_est": "行权概率估算~|Δ|（%）",
                "bid_display": "买价(兜底)",
                "ask_display": "卖价(兜底)",
                "spread_display": "价差(兜底)",
            }
        else:
            cols_map = {
                "contract_symbol": "合约代码 / Contract",
                "strike": "行权价 / Strike",
                "discount_pct": "相对现价折价（%） / Strike Discount vs Spot (%)",
                "bid": "买价 / Bid",
                "ask": "卖价 / Ask",
                "mid": "中间价 / Mid",
                "price_source": "价格来源 / Price Src",
                "iv": "隐含波动率（%） / IV (%)",
                "delta": "Delta（%） / Delta (%)",
                "itm_prob": "价内概率（%） / ITM Prob (%)",
                "days_to_exp": "剩余天数 / DTE",
                "margin_cash_secured": "现金担保（$） / Cash Secured ($)",
                "single_return": "单期收益率（%） / Period Return (%)",
                "annualized_return": "年化（%） / Annualized (%)",
                "spread": "价差（$） / Spread ($)",
                "volume": "成交量 / Volume",
                "open_interest": "未平仓量 / OI",
                "assign_prob_est": "行权概率估算~|Δ|（%） / Assign Prob ~|Δ| (%)",
                "bid_display": "买价(兜底) / Bid(disp)",
                "ask_display": "卖价(兜底) / Ask(disp)",
                "spread_display": "价差(兜底) / Spread(disp)",
            }
        show = show.rename(columns=cols_map)

        # 保存结果到会话，避免勾选触发重跑导致表消失
        st.session_state["last_table"] = show
        st.success(tr("列表已更新。可在下方勾选进行比较。", "List updated. Use the checkboxes below to compare."))

# ──────────────────────────────────────────────────────────────────────
# 持久渲染：始终基于会话中的表显示（支持复选与对比）
# ──────────────────────────────────────────────────────────────────────
current = st.session_state.get("last_table")
if isinstance(current, pd.DataFrame) and not current.empty:
    select_col = "选择" if lang_mode == "中文" else "Select"
    disp = current.copy()
    if select_col not in disp.columns:
        disp.insert(0, select_col, False)
    else:
        disp = disp[[select_col] + [c for c in disp.columns if c != select_col]]

    edited = st.data_editor(
        disp,
        use_container_width=True,
        num_rows="fixed",
        hide_index=True,
        column_config={
            select_col: st.column_config.CheckboxColumn(
                label=select_col,
                help=tr("勾选要对比的合约", "Tick contracts to compare"),
                default=False,
            )
        },
        key="sellput_editor",
    )

    if st.button(tr("比较所选", "Compare selected")):
        try:
            chosen = edited[edited[select_col] == True].copy()
        except Exception:
            chosen = pd.DataFrame()
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