import streamlit as st
import pandas as pd
import numpy as np

from sellput_checker.yahoo_client import YahooClient
from sellput_checker.checklist import evaluate_chain_df

# ──────────────────────────────────────────────────────────────────────────────
# Language toggle and translation helper
# ──────────────────────────────────────────────────────────────────────────────
LANG_OPTIONS = ["Bilingual / 双语", "English", "中文"]
lang_mode = st.sidebar.selectbox("Language / 语言", LANG_OPTIONS, index=0)

def tr(cn: str, en: str) -> str:
    """Return a string based on the current language mode."""
    if lang_mode == "English":
        return en
    if lang_mode == "中文":
        return cn
    return f"{en} / {cn}"

# 页面基本设置
st.set_page_config(page_title="Sell Put Checker", layout="wide")
st.title(tr("📉 Sell Put 合约合理性检查", "📉 Sell Put Reasonableness Checker"))

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

    if st.button(tr("获取推荐合约", "Get Suggestions")):
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

        # 根据需要选择是否使用 last 兜底显示列（来自 evaluator 的 bid_display/ask_display/spread_display）
        use_display = st.sidebar.checkbox(
            tr("使用 Last 兜底显示 Bid/Ask", "Use 'Last' fallback for Bid/Ask display"),
            value=True,
            help=tr("当夜间 Yahoo 报价缺失时，用 last 作为展示占位，不影响筛选逻辑。",
                    "When Yahoo nightly quotes are missing, show 'last' as placeholder without affecting filters.")
        )

        has_disp = all(c in out.columns for c in ["bid_display", "ask_display", "spread_display"])

        if use_display and has_disp:
            cols = [
                "contract_symbol","strike","mid","single_return","annualized_return","iv","assign_prob_est",
                "days_to_exp","margin_cash_secured","volume","open_interest",
                "bid_display","ask_display","spread_display","itm_prob","delta","price_source"
            ]
        else:
            cols = [
                "contract_symbol","strike","mid","single_return","annualized_return","iv","assign_prob_est",
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

        st.dataframe(show, use_container_width=True)