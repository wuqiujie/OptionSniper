import math
from sellput_checker.calculations import (
    bs_d1_d2, put_delta, itm_probability, cash_secured_margin, annualized_return
)

def test_cash_secured_margin():
    assert cash_secured_margin(100, 2.5) == 9750.0

def test_annualized_return_basic():
    ar = annualized_return(1.0, 9500.0, 30)
    assert 0.0 < ar < 2.0  # 粗略区间

def test_bs_basic():
    S, K, r, sigma, T = 100.0, 100.0, 0.05, 0.2, 30/365
    d1, d2 = bs_d1_d2(S, K, r, sigma, T)
    assert isinstance(d1, float) and isinstance(d2, float)
    delta_abs = put_delta(S, K, r, sigma, T)
    prob = itm_probability(S, K, r, sigma, T)
    assert 0.0 <= delta_abs <= 1.0
    assert 0.0 <= prob <= 1.0
