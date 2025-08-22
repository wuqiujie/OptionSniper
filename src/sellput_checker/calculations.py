from __future__ import annotations
from math import log, sqrt, exp
from typing import Tuple
from .utils import norm_cdf, clamp

def bs_d1_d2(S: float, K: float, r: float, sigma: float, T: float) -> Tuple[float, float]:
    if S <= 0 or K <= 0 or sigma <= 0 or T <= 0:
        return 0.0, 0.0
    d1 = (log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    return d1, d2

def put_delta(S: float, K: float, r: float, sigma: float, T: float) -> float:
    # 欧式看跌期权 Delta（对标的价格的一阶导）
    d1, _ = bs_d1_d2(S, K, r, sigma, T)
    # 对于 Put：Delta = N(d1) - 1
    delta = norm_cdf(d1) - 1.0
    # 常见习惯：返回绝对值方便筛选
    return abs(delta)

def itm_probability(S: float, K: float, r: float, sigma: float, T: float) -> float:
    # 使用 N(-d2) 估算到期价内概率（看跌为 S_T < K 的概率）
    _, d2 = bs_d1_d2(S, K, r, sigma, T)
    return clamp(norm_cdf(-d2), 0.0, 1.0)

def spread(bid: float, ask: float) -> float:
    if bid is None or ask is None:
        return float('inf')
    return max(0.0, float(ask) - float(bid))

def mid_price(bid: float, ask: float) -> float:
    if bid is None or ask is None:
        return 0.0
    return (float(bid) + float(ask)) / 2.0

def cash_secured_margin(strike: float, premium: float, multiplier: int = 100) -> float:
    # 现金担保卖出：需要准备买入 100 股的现金，实际占用现金 ≈ (K - premium) * 100
    return max(0.0, (float(strike) - float(premium)) * multiplier)

def single_return(premium: float, margin: float, multiplier: int = 100) -> float:
    if margin <= 0:
        return 0.0
    return (float(premium) * multiplier) / float(margin)

def annualized_return(premium: float, margin: float, days_to_exp: int, multiplier: int = 100) -> float:
    if margin <= 0 or days_to_exp <= 0:
        return 0.0
    return single_return(premium, margin, multiplier) / (days_to_exp / 365.0)
