from math import erf, sqrt

def norm_cdf(x: float) -> float:
    """标准正态分布累积分布函数 N(x)，不依赖 SciPy。"""
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def safe_float(x, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default
