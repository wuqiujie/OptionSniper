import streamlit as st
import pandas as pd
import numpy as np

from sellput_checker.yahoo_client import YahooClient
from sellput_checker.checklist import evaluate_chain_df
from sellput_checker.calculations import bs_d1_d2
from sellput_checker.utils import norm_cdf

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
    ["put", "call"],
    index=0,
    format_func=lambda x: tr("卖出看跌", "Sell Put") if x == "put" else tr("备兑看涨", "Covered Call")
)

# 子标题：根据模式在 Ticker 输入框之前显示
if mode_key == "call":
    st.subheader(tr("📈 Covered Call 合约筛选", "📈 Covered Call Screener"))
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