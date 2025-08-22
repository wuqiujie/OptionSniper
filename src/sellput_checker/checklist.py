from __future__ import annotations
import math
import datetime as dt
from typing import Dict, Any, Optional, Tuple
import pandas as pd


# ──────────────────────────────────────────────────────────────────────────────
# 简单的定价/统计辅助函数（内置，避免外部依赖）
# ──────────────────────────────────────────────────────────────────────────────

def _mid_and_source(bid: float, ask: float, last_price: float) -> tuple[float, str]:
    """优先使用 B/A；B 或 A 其一也可；否则用 last_price；都没有则 NaN。"""
    b = float(bid or 0.0)
    a = float(ask or 0.0)
    lp = float(last_price or 0.0)
    if b > 0 and a > 0:
        return 0.5 * (b + a), "B/A"
    if b > 0 and a <= 0:
        return b, "Bid"
    if a > 0 and b <= 0:
        return a, "Ask"
    if lp > 0:
        return lp, "LAST"
    return float("nan"), "UNKNOWN"


def _spread(bid: float, ask: float) -> float | float("nan"):
    """只有同时有 B 与 A 时才计算 spread；否则返回 NaN。"""
    b = float(bid or 0.0)
    a = float(ask or 0.0)
    return max(0.0, a - b) if (b > 0 and a > 0) else float("nan")


def _norm_cdf(x: float) -> float:
    # 误差函数近似：避免引入 scipy
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _put_delta(spot: float, strike: float, r: float, sigma: float, T: float) -> float:
    """
    Black-Scholes 欧式看跌期权 Delta（对标的的导数）。
    返回值为负数，调用处会取绝对值用于筛选。
    """
    if spot <= 0 or strike <= 0 or sigma <= 0 or T <= 0:
        return float("nan")
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    # Put delta = N(d1) - 1
    return _norm_cdf(d1) - 1.0


def _itm_probability(spot: float, strike: float, r: float, sigma: float, T: float) -> float:
    """
    风险中性概率的一个近似：P(S_T < K) = N(-d2)。
    """
    if spot <= 0 or strike <= 0 or sigma <= 0 or T <= 0:
        return float("nan")
    d2 = (math.log(spot / strike) + (r - 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(-d2)


def _cash_secured_margin(strike: float) -> float:
    """现金担保保证金（保守）：strike * 100。"""
    return float(strike or 0.0) * 100.0


def _single_return(premium_mid: float, margin_needed: float) -> float:
    """单期收益率（小数）。"""
    if margin_needed <= 0:
        return 0.0
    return (float(premium_mid or 0.0) * 100.0) / margin_needed


def _annualized_return(premium_mid: float, margin_needed: float, days_to_exp: int) -> float:
    """年化收益率（小数）。"""
    if margin_needed <= 0 or days_to_exp <= 0:
        return 0.0
    return _single_return(premium_mid, margin_needed) * (365.0 / float(days_to_exp))


def _bs_put_price(S: float, K: float, r: float, sigma: float, T: float) -> float:
    if S <= 0 or K <= 0 or sigma <= 0 or T <= 0:
        return float("nan")
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    # P = K*e^{-rT}*N(-d2) - S*N(-d1)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)

def _implied_vol_put_from_price(target_p: float, S: float, K: float, r: float, T: float,
                                lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-4, max_iter: int = 100) -> float:
    """Simple bisection to back out IV from a put price. Returns NaN if fails."""
    try:
        if not (target_p and target_p > 0) or S <= 0 or K <= 0 or T <= 0:
            return float("nan")
        pl = _bs_put_price(S, K, r, lo, T)
        ph = _bs_put_price(S, K, r, hi, T)
        if not (pl == pl and ph == ph):
            return float("nan")
        # ensure target within bracket; if not, expand hi a bit
        if target_p < pl:
            return float("nan")
        if target_p > ph:
            # expand hi up to a cap
            for _ in range(10):
                hi *= 1.5
                ph = _bs_put_price(S, K, r, hi, T)
                if target_p <= ph or hi > 20:
                    break
        # Bisection
        for _ in range(max_iter):
            mid = 0.5 * (lo + hi)
            pm = _bs_put_price(S, K, r, mid, T)
            if pm != pm:
                return float("nan")
            if abs(pm - target_p) < tol:
                return mid
            # monotonic increasing in sigma
            if pm < target_p:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)
    except Exception:
        return float("nan")


# ──────────────────────────────────────────────────────────────────────────────
# 主评估函数
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_chain_df(
    df: pd.DataFrame,
    spot: float,
    expiration: str,
    risk_free_rate: float = 0.05,  # 5%
    delta_high: float = 0.35,      # 只要上限
    iv_min: float = 0.0,           # 小数：0.25 表示 25%
    iv_max: float = 10.0,
    max_spread: float = 0.10,
    min_volume: int = 0,
    min_annual: float = 0.10,      # 小数：0.15 表示 15%
    default_days_if_unknown: int = 30,  # 手动模式或无法解析到期日时的默认天数
) -> pd.DataFrame:
    """
    对期权链打分，返回包含评估字段的 DataFrame。
    需要 df 至少包含：strike, bid, ask, last_price（可空）, implied_vol（可空）, volume, open_interest, in_the_money, contract_symbol, ticker
    """
    if df is None or df.empty:
        return pd.DataFrame()

    # 解析到期日 → days_to_exp
    today = dt.date.today()
    try:
        exp_date = dt.date.fromisoformat(expiration)
        days_to_exp = max(1, (exp_date - today).days)
    except Exception:
        # 手动模式等无法解析时给默认值
        days_to_exp = max(1, int(default_days_if_unknown))

    rows = []
    for _, r in df.iterrows():
        # parse prices; treat 0/empty as missing (Yahoo nightly often 0)
        bid_raw = r.get("bid", None)
        ask_raw = r.get("ask", None)
        bid = float(bid_raw) if bid_raw not in (None, "") else 0.0
        ask = float(ask_raw) if ask_raw not in (None, "") else 0.0
        strike = float(r.get("strike", 0.0) or 0.0)

        # last price fallback chain
        last_price_candidates = [
            r.get("last_price", None),
            r.get("lastPrice", None),
            r.get("last", None),
            r.get("mark", None),
            r.get("regularMarketPrice", None),
        ]
        last_price = float(next((v for v in last_price_candidates if v is not None and str(v) != "" and float(v) > 0), 0.0))

        # IV: support implied_vol or iv; treat <=0 as missing
        iv_val = r.get("implied_vol", None)
        if iv_val is None and "iv" in r:
            iv_val = r.get("iv")
        try:
            iv = float(iv_val)
            if not (iv > 0):
                iv = float("nan")
        except Exception:
            iv = float("nan")

        vol = int(float(r.get("volume", 0) or 0))
        oi = int(float(r.get("open_interest", 0) or 0))
        cs = r.get("contract_symbol")
        itm = bool(r.get("in_the_money", False))
        ticker = r.get("ticker") or ""

        # mid / price_source / spread（使用 last_price 兜底）
        mid, price_src = _mid_and_source(bid, ask, last_price)
        spr = _spread(bid, ask)

        # 如果 mid 仍不可用/<=0，使用简易理论价作为兜底（不影响 B/A 依赖的过滤）
        T = max(1e-6, days_to_exp / 365.0)
        this_spot = float(spot or 0.0)
        iv_guess = None
        try:
            iv_guess = float(iv) if (iv == iv and iv > 0) else None
        except Exception:
            iv_guess = None
        if not (mid == mid and mid > 0):
            guess = iv_guess if iv_guess is not None else 0.4  # 默认 40% 兜底
            theo = _bs_put_price(this_spot, strike, risk_free_rate, guess, T)
            if theo == theo and theo > 0:
                mid = theo
                price_src = "THEO"

        premium = mid if (mid == mid and mid > 0) else 0.0  # NaN 保护
        spr = _spread(bid, ask)

        # Fallback: if iv missing/<=0 but we have a positive premium and spot, back out IV from price
        iv_from_price = float("nan")
        this_spot = float(spot or 0.0)
        T = max(1e-6, days_to_exp / 365.0)
        if (not (iv == iv and iv > 0)) and (premium > 0) and (this_spot > 0):
            iv_from_price = _implied_vol_put_from_price(premium, this_spot, strike, risk_free_rate, T)
            if iv_from_price == iv_from_price and iv_from_price > 0:
                iv = iv_from_price

        sigma = iv if (iv == iv and iv > 0) else float("nan")
        put_delta_val = _put_delta(this_spot, strike, risk_free_rate, sigma, T)
        delta_abs = abs(put_delta_val) if (put_delta_val == put_delta_val) else float("nan")
        # nightly data often reports delta as 0; if no B/A and delta==0, treat as missing
        if (delta_abs == 0 or delta_abs != delta_abs) and (bid <= 0 and ask <= 0):
            delta_abs = float("nan")
        prob_itm = _itm_probability(this_spot, strike, risk_free_rate, sigma, T)

        # 如果 |Delta| 缺失或为 0，则用价格法的概率（N(-d2)）兜底
        assign_prob = delta_abs
        if (not (assign_prob == assign_prob)) or (assign_prob == 0):
            if prob_itm == prob_itm and prob_itm > 0:
                assign_prob = prob_itm

        margin_needed = _cash_secured_margin(strike)
        sr = _single_return(premium, margin_needed)
        ar = _annualized_return(premium, margin_needed, days_to_exp)

        # Display fallbacks: prefer B/A; else use last_price; else use mid (which may be THEO)
        bid_disp = bid if bid > 0 else (last_price if last_price > 0 else (mid if (mid == mid and mid > 0) else float("nan")))
        ask_disp = ask if ask > 0 else (last_price if last_price > 0 else (mid if (mid == mid and mid > 0) else float("nan")))
        spread_disp = spr if (spr == spr) else float("nan")

        rows.append({
            "ticker": ticker,
            "expiration": expiration,
            "contract_symbol": cs,
            "strike": strike,
            "bid": bid,
            "ask": ask,
            "last_price": last_price,
            "mid": mid,
            "premium": premium,
            "iv": iv,
            "iv_from_price": iv_from_price,
            "delta": delta_abs,
            "assign_prob_est": assign_prob,  # |Delta| ≈ estimated assignment probability (decimal)
            "itm_prob": prob_itm,
            "days_to_exp": days_to_exp,
            "margin_cash_secured": margin_needed,
            "single_return": sr,
            "annualized_return": ar,
            "spread": spr,
            "volume": vol,
            "open_interest": oi,
            "in_the_money": itm,
            "price_source": price_src,
            "bid_display": bid_disp,
            "ask_display": ask_disp,
            "spread_display": spread_disp,
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    # 各项规则（NaN 视为不通过）
    out["ok_delta"] = (out["delta"] <= float(delta_high)) & out["delta"].notna()
    out["ok_iv"] = (out["iv"] >= float(iv_min)) & (out["iv"] <= float(iv_max)) & out["iv"].notna()
    # spread：只有有 B/A 才有值，NaN 视为“信息不足”，默认不过滤太严：允许 NaN 通过（否则大量 LAST 会被刷掉）
    out["ok_spread"] = (out["spread"] <= max_spread) | out["spread"].isna()
    out["ok_volume"] = out["volume"] >= int(min_volume)
    out["ok_annual"] = out["annualized_return"] >= float(min_annual)

    out["ok_all"] = out["ok_delta"] & out["ok_iv"] & out["ok_spread"] & out["ok_volume"] & out["ok_annual"]

    # 排序：先通过，再看年化高/量大/价差小
    out = out.sort_values(
        by=["ok_all", "annualized_return", "volume", "spread"],
        ascending=[False, False, False, True]
    ).reset_index(drop=True)

    return out