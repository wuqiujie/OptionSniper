from __future__ import annotations
import math
import datetime as dt
import re
from typing import Dict, Any, Optional, Tuple, Literal
import pandas as pd

# ──────────────────────────────────────────────────────────────────────────────
# 数学/定价辅助（不依赖 scipy）
# ──────────────────────────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _mid_and_source(bid: float, ask: float, last_price: float) -> tuple[float, str]:
    """优先用 B/A；其一可用也接受；否则用 last；都没有则 NaN。"""
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
    b = float(bid or 0.0)
    a = float(ask or 0.0)
    return max(0.0, a - b) if (b > 0 and a > 0) else float("nan")


# ---- Black–Scholes 基元 ----

def _bs_d1_d2(S: float, K: float, r: float, sigma: float, T: float) -> tuple[float, float]:
    if S <= 0 or K <= 0 or sigma <= 0 or T <= 0:
        return float("nan"), float("nan")
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def _bs_put_price(S: float, K: float, r: float, sigma: float, T: float) -> float:
    d1, d2 = _bs_d1_d2(S, K, r, sigma, T)
    if d1 != d1 or d2 != d2:
        return float("nan")
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _bs_call_price(S: float, K: float, r: float, sigma: float, T: float) -> float:
    d1, d2 = _bs_d1_d2(S, K, r, sigma, T)
    if d1 != d1 or d2 != d2:
        return float("nan")
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def _put_delta(S: float, K: float, r: float, sigma: float, T: float) -> float:
    d1, _ = _bs_d1_d2(S, K, r, sigma, T)
    if d1 != d1:
        return float("nan")
    return _norm_cdf(d1) - 1.0  # 负值


def _call_delta(S: float, K: float, r: float, sigma: float, T: float) -> float:
    d1, _ = _bs_d1_d2(S, K, r, sigma, T)
    if d1 != d1:
        return float("nan")
    return _norm_cdf(d1)  # 0~1


def _itm_probability_put(S: float, K: float, r: float, sigma: float, T: float) -> float:
    _, d2 = _bs_d1_d2(S, K, r, sigma, T)
    if d2 != d2:
        return float("nan")
    return _norm_cdf(-d2)  # P(S_T < K)


def _itm_probability_call(S: float, K: float, r: float, sigma: float, T: float) -> float:
    _, d2 = _bs_d1_d2(S, K, r, sigma, T)
    if d2 != d2:
        return float("nan")
    return _norm_cdf(d2)  # P(S_T > K)


def _implied_vol_from_price(target: float, S: float, K: float, r: float, T: float,
                            kind: Literal["put", "call"],
                            lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-4, max_iter: int = 100) -> float:
    """从给定价格反推 IV 的简单二分法。失败返回 NaN。"""
    try:
        if not (target and target > 0) or S <= 0 or K <= 0 or T <= 0:
            return float("nan")
        # 初值
        def price(sig: float) -> float:
            return _bs_put_price(S, K, r, sig, T) if kind == "put" else _bs_call_price(S, K, r, sig, T)

        pl = price(lo); ph = price(hi)
        if not (pl == pl and ph == ph):
            return float("nan")
        if target < pl:
            return float("nan")
        if target > ph:
            for _ in range(10):
                hi *= 1.5
                ph = price(hi)
                if target <= ph or hi > 20:
                    break
        for _ in range(max_iter):
            mid = 0.5 * (lo + hi)
            pm = price(mid)
            if pm != pm:
                return float("nan")
            if abs(pm - target) < tol:
                return mid
            if pm < target:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)
    except Exception:
        return float("nan")


# ──────────────────────────────────────────────────────────────────────────────
# 资本/收益度量
# ──────────────────────────────────────────────────────────────────────────────

def _cash_secured_capital_put(strike: float, premium_mid: float, mode: Literal["strike", "net"] = "strike") -> float:
    """卖出看跌的占用资金估算。
    - mode="strike": 保守，K*100
    - mode="net":    常见，(K - premium)*100
    """
    K = float(strike or 0.0)
    p = float(premium_mid or 0.0)
    if mode == "net":
        return max(0.0, (K - p) * 100.0)
    return max(0.0, K * 100.0)


def _covered_call_capital(spot: float) -> float:
    """备兑看涨：以现价估计被占用的 100 股资金。"""
    S = float(spot or 0.0)
    return max(0.0, S * 100.0)


def _single_return(premium_mid: float, capital: float) -> float:
    if capital <= 0:
        return 0.0
    return (float(premium_mid or 0.0) * 100.0) / float(capital)


def _annualized_return(premium_mid: float, capital: float, days_to_exp: int) -> float:
    if capital <= 0 or days_to_exp <= 0:
        return 0.0
    return _single_return(premium_mid, capital) * (365.0 / float(days_to_exp))


# ──────────────────────────────────────────────────────────────────────────────
# 主评估函数（兼容 Put / Call）
# ──────────────────────────────────────────────────────────────────────────────

def _infer_kind_from_symbol(cs: str) -> Literal["put", "call"] | None:
    try:
        if not isinstance(cs, str):
            return None
        m = re.search(r"([CP])(\d{8})$", cs)
        if m:
            return "call" if m.group(1) == "C" else "put"
    except Exception:
        pass
    return None


def evaluate_chain_df(
    df: pd.DataFrame,
    spot: float,
    expiration: str,
    kind: Literal["put", "call", "auto"] = "auto",
    risk_free_rate: float = 0.05,
    delta_high: float = 0.35,
    iv_min: float = 0.0,
    iv_max: float = 10.0,
    max_spread: float = 0.10,
    min_volume: int = 0,
    min_annual: float = 0.10,
    default_days_if_unknown: int = 30,
    put_capital_mode: Literal["strike", "net"] = "strike",
) -> pd.DataFrame:
    """对期权链打分，返回包含评估字段的 DataFrame。

    - 支持 kind="put"/"call"/"auto"（从 contract_symbol 推断）
    - 年化/单期收益率：
        * Put：默认以 K*100 估算占用资金（可选 net: (K-premium)*100）
        * Call：以 S*100 估算（备兑持仓 100 股）
    - Delta：
        * Put：返回 |Delta_put| 便于设上限筛选
        * Call：返回 Delta_call（0~1）
    """
    if df is None or df.empty:
        return pd.DataFrame()

    # 解析到期日 → DTE
    today = dt.date.today()
    try:
        exp_date = dt.date.fromisoformat(expiration)
        days_to_exp = max(1, (exp_date - today).days)
    except Exception:
        days_to_exp = max(1, int(default_days_if_unknown))

    # 推断类型（auto）
    eff_kind = kind
    if eff_kind == "auto":
        cs0 = None
        try:
            if "contract_symbol" in df.columns and not df["contract_symbol"].isna().all():
                cs0 = df["contract_symbol"].dropna().astype(str).iloc[0]
        except Exception:
            cs0 = None
        inferred = _infer_kind_from_symbol(cs0) if cs0 else None
        eff_kind = inferred or "put"  # 默认按 put 处理，保持向后兼容

    rows = []
    S = float(spot or 0.0)
    T = max(1e-6, days_to_exp / 365.0)

    for _, r in df.iterrows():
        # 基础字段
        bid = float(r.get("bid", 0.0) or 0.0)
        ask = float(r.get("ask", 0.0) or 0.0)
        strike = float(r.get("strike", 0.0) or 0.0)
        last_price_candidates = [
            r.get("last_price"), r.get("lastPrice"), r.get("last"), r.get("mark"), r.get("regularMarketPrice")
        ]
        last_price = float(next((v for v in last_price_candidates if v is not None and str(v) != "" and float(v) > 0), 0.0))

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

        # mid 与价差
        mid, price_src = _mid_and_source(bid, ask, last_price)
        spr = _spread(bid, ask)

        # 若 mid 缺失，则用 BS 理论价兜底（区分 put/call）
        sigma_guess = None
        try:
            sigma_guess = float(iv) if (iv == iv and iv > 0) else None
        except Exception:
            sigma_guess = None
        if not (mid == mid and mid > 0):
            guess = sigma_guess if sigma_guess is not None else 0.4
            theo = _bs_put_price(S, strike, risk_free_rate, guess, T) if eff_kind == "put" else _bs_call_price(S, strike, risk_free_rate, guess, T)
            if theo == theo and theo > 0:
                mid = theo
                price_src = "THEO"

        premium = mid if (mid == mid and mid > 0) else 0.0

        # 若 IV 缺失但有价格，则反推 IV（区分 put/call）
        if not (iv == iv and iv > 0) and premium > 0 and S > 0:
            iv_b = _implied_vol_from_price(premium, S, strike, risk_free_rate, T, eff_kind)
            if iv_b == iv_b and iv_b > 0:
                iv = iv_b

        # Delta / 价内概率 / 指派概率近似
        if eff_kind == "put":
            dlt = _put_delta(S, strike, risk_free_rate, max(iv, 1e-6), T)
            delta_for_filter = abs(dlt) if (dlt == dlt) else float("nan")
            itm_prob = _itm_probability_put(S, strike, risk_free_rate, max(iv, 1e-6), T)
            assign_prob = delta_for_filter if (delta_for_filter == delta_for_filter and delta_for_filter > 0) else itm_prob
            capital = _cash_secured_capital_put(strike, premium, mode=put_capital_mode)
        else:
            dlt = _call_delta(S, strike, risk_free_rate, max(iv, 1e-6), T)
            delta_for_filter = dlt if (dlt == dlt) else float("nan")
            itm_prob = _itm_probability_call(S, strike, risk_free_rate, max(iv, 1e-6), T)
            assign_prob = delta_for_filter if (delta_for_filter == delta_for_filter and delta_for_filter > 0) else itm_prob
            capital = _covered_call_capital(S)

        sr = _single_return(premium, capital)
        ar = _annualized_return(premium, capital, days_to_exp)

        # 展示兜底（只做显示，不影响筛选）
        bid_disp = bid if bid > 0 else (last_price if last_price > 0 else (mid if (mid == mid and mid > 0) else float("nan")))
        ask_disp = ask if ask > 0 else (last_price if last_price > 0 else (mid if (mid == mid and mid > 0) else float("nan")))
        spread_disp = spr if (spr == spr) else float("nan")

        rows.append({
            "kind": eff_kind,
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
            "delta": delta_for_filter,
            "assign_prob_est": assign_prob,
            "itm_prob": itm_prob,
            "days_to_exp": days_to_exp,
            "margin_cash_secured": capital,  # 为兼容沿用旧列名
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

    # 规则（NaN 视为不通过；spread 允许 NaN 通过以保留夜间 LAST）
    out["ok_delta"] = (out["delta"] <= float(delta_high)) & out["delta"].notna()
    out["ok_iv"] = (out["iv"] >= float(iv_min)) & (out["iv"] <= float(iv_max)) & out["iv"].notna()
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