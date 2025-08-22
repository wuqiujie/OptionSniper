from __future__ import annotations
import pandas as pd
import numpy as np
import datetime
from typing import Optional

def evaluate_chain_df(
    df: pd.DataFrame,
    spot: float,
    exp: str,
    delta_high: float = 0.35,
    iv_min: float = 0.0,
    iv_max: float = 10.0,
    max_spread: float = 0.1,
    min_volume: int = 0,
    min_annual: float = 0.0,
) -> pd.DataFrame:
    """对期权链 DataFrame 进行评估，添加估算字段和过滤标记。"""
    if df.empty:
        return df

    # 计算 days_to_exp
    today = datetime.datetime.now().date()
    exp_date = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
    days_to_exp = (exp_date - today).days
    if days_to_exp <= 0:
        days_to_exp = 1  # 防止除以 0 或负数

    # ---- 价格标准化与中间价/价差计算 ----
    # 归一化最近成交价字段
    if "lastPrice" in df.columns:
        df["last_price"] = pd.to_numeric(df["lastPrice"], errors="coerce")
    elif "last_price" in df.columns:
        df["last_price"] = pd.to_numeric(df["last_price"], errors="coerce")
    elif "last" in df.columns:
        df["last_price"] = pd.to_numeric(df["last"], errors="coerce")
    elif "lastTradePrice" in df.columns:
        df["last_price"] = pd.to_numeric(df["lastTradePrice"], errors="coerce")
    else:
        df["last_price"] = np.nan

    # 兜底把 bid/ask/strike 转成数值
    for col in ["bid", "ask", "strike"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = np.nan

    # 价格来源与 mid 计算优先级：
    # 1) 同时有 B/A → mid = (bid+ask)/2, source=B/A
    # 2) 只有 Bid → mid = bid, source=Bid
    # 3) 只有 Ask → mid = ask, source=Ask
    # 4) 有 last_price → mid = last_price, source=LAST
    # 否则 mid=NaN, source=UNKNOWN
    has_bid = df["bid"].fillna(0) > 0
    has_ask = df["ask"].fillna(0) > 0
    has_ba = has_bid & has_ask
    has_last = df["last_price"].fillna(0) > 0

    df["mid"] = np.select(
        [
            has_ba,
            has_bid & (~has_ask),
            has_ask & (~has_bid),
            (~has_ba) & has_last,
        ],
        [
            (df["bid"].fillna(0) + df["ask"].fillna(0)) / 2.0,
            df["bid"],
            df["ask"],
            df["last_price"],
        ],
        default=np.nan,
    )

    df["price_source"] = np.select(
        [
            has_ba,
            has_bid & (~has_ask),
            has_ask & (~has_bid),
            (~has_ba) & has_last,
        ],
        ["B/A", "Bid", "Ask", "LAST"],
        default="UNKNOWN",
    )

    # 只有同时存在 B/A 时才计算 spread；否则为 NaN
    df["spread"] = np.where(
        has_ba,
        (df["ask"].fillna(0) - df["bid"].fillna(0)).clip(lower=0),
        np.nan,
    )

    # 价格有效性：mid>0 视为可用
    df["ok_price"] = df["mid"].fillna(0) > 0

    # 计算 delta 绝对值
    df["delta_abs"] = df["delta"].abs()

    # 计算 itm_prob 近似概率（简单公式）
    df["itm_prob"] = np.where(
        df["inTheMoney"],
        1 - df["delta_abs"],
        df["delta_abs"]
    )

    # 计算保证金需求（假设现金担保）
    df["margin_cash_secured"] = df["strike"] * 100

    # 计算单张收益（mid 价）
    df["single_return"] = df["mid"] * 100

    # 计算年化收益率
    df["annualized_return"] = df["single_return"] / df["margin_cash_secured"] * (365 / days_to_exp)

    # 标记各项是否符合要求
    df["ok_delta"] = df["delta_abs"] <= delta_high
    df["ok_iv"] = (df["impliedVolatility"] >= iv_min) & (df["impliedVolatility"] <= iv_max)
    df["ok_spread"] = df["spread"] <= max_spread
    df["ok_volume"] = df["volume"] >= min_volume
    df["ok_annual"] = df["annualized_return"] >= min_annual

    # 最终通过：包含价格有效性
    df["ok_all"] = df[["ok_delta", "ok_iv", "ok_spread", "ok_volume", "ok_annual", "ok_price"]].all(axis=1)

    # 添加 days_to_exp
    df["days_to_exp"] = days_to_exp

    # 重命名部分列方便后续使用
    df.rename(columns={"impliedVolatility": "iv", "inTheMoney": "in_the_money"}, inplace=True)

    return df.copy()
