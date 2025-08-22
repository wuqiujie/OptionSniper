from pydantic import BaseModel, Field
from typing import Optional

class OptionContract(BaseModel):
    ticker: str
    expiration: str
    contract_symbol: str
    strike: float
    bid: float
    ask: float
    last_price: float
    implied_vol: float
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    in_the_money: Optional[bool] = None
    updated_at: Optional[str] = None

class EvaluationResult(BaseModel):
    ticker: str
    expiration: str
    strike: float
    spot: float
    bid: float
    ask: float
    mid: float
    premium: float
    iv: float
    delta: float
    itm_prob: float
    days_to_exp: int
    margin_cash_secured: float
    single_return: float     # 单次收益率 = premium / margin
    annualized_return: float
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    in_the_money: Optional[bool] = None
    contract_symbol: Optional[str] = None
