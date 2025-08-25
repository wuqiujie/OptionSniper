# src/sellput_checker/yahoo_client.py
import time
import pandas as pd
import yfinance as yf

class YahooClient:
    def __init__(self, ticker: str):
        self.ticker = ticker

    def get_expirations(self):
        tk = yf.Ticker(self.ticker)
        for _ in range(3):
            try:
                exps = tk.options
                if exps:
                    return exps
            except Exception:
                time.sleep(0.8)
        return []

    def get_spot_price(self) -> float:
        tk = yf.Ticker(self.ticker)
        for _ in range(3):
            try:
                info = tk.fast_info
                for k in ["last_price", "lastPrice", "regularMarketPrice", "last", "previousClose"]:
                    v = getattr(info, k, None)
                    if v:
                        return float(v)
                hist = tk.history(period="1d")
                if not hist.empty:
                    return float(hist["Close"].iloc[-1])
            except Exception:
                time.sleep(0.8)
        return float("nan")

    def get_option_chain(self, exp: str, kind: str = "put") -> pd.DataFrame:
        tk = yf.Ticker(self.ticker)
        last_exc = None
        for _ in range(3):
            try:
                oc = tk.option_chain(exp)
                raw = oc.puts if kind.lower() == "put" else oc.calls
                df = raw.copy()

                # --- Normalize to snake_case your code expects ---
                df = df.rename(
                    columns={
                        "contractSymbol": "contract_symbol",
                        "openInterest": "open_interest",
                        "impliedVolatility": "implied_vol",
                        "inTheMoney": "in_the_money",
                        "lastPrice": "last_price",
                    }
                )

                # Ensure required columns exist
                for need in ["contract_symbol","strike","bid","ask","implied_vol","in_the_money","volume","open_interest","last_price"]:
                    if need not in df.columns:
                        df[need] = pd.NA

                # Make the key numerics numeric
                for col in ["strike","bid","ask","implied_vol","volume","open_interest","last_price"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

                # Attach ticker for downstream
                df["ticker"] = self.ticker
                return df
            except Exception as e:
                last_exc = e
                time.sleep(0.8)

        # Fallback: empty dataframe with expected columns to not crash downstream
        cols = ["contract_symbol","strike","bid","ask","implied_vol","in_the_money","volume","open_interest","last_price","ticker"]
        return pd.DataFrame(columns=cols)
# src/sellput_checker/yahoo_client.py

import time
import datetime as dt
from typing import List

import pandas as pd
import yfinance as yf


class YahooClient:
    """Thin wrapper around yfinance with a few robustness tweaks.

    - Keeps a single Ticker instance (avoid recreating each call)
    - Robust spot price detection from fast_info / info / history
    - Normalizes option chain columns to snake_case expected by app
    - Adds useful metadata columns: expiration, updated_at, ticker
    """

    def __init__(self, ticker: str):
        self.ticker = (ticker or "").upper()
        # cache the yfinance Ticker instance
        self._tkr = yf.Ticker(self.ticker)

    # ----------------------------- helpers -----------------------------
    @staticmethod
    def _sleep_backoff(i: int) -> None:
        time.sleep(0.6 + 0.2 * i)

    # --------------------------- public APIs ---------------------------
    def get_expirations(self) -> List[str]:
        """Return available option expirations as ISO date strings."""
        last_exc = None
        for i in range(3):
            try:
                exps = self._tkr.options
                if exps:
                    # yfinance returns list[str]
                    return list(exps)
            except Exception as e:
                last_exc = e
                self._sleep_backoff(i)
        # fallback empty list
        return []

    def get_spot_price(self) -> float:
        """Best-effort spot price.

        Priority: fast_info -> info -> 1d history close.
        """
        # 1) fast_info (dict-like)
        for i in range(3):
            try:
                fi = getattr(self._tkr, "fast_info", None)
                if fi:
                    # fast_info can behave like dict or has attributes in some versions
                    candidates = (
                        (fi.get("last_price") if hasattr(fi, "get") else None),
                        (fi.get("regularMarketPrice") if hasattr(fi, "get") else None),
                        (fi.get("last") if hasattr(fi, "get") else None),
                        (fi.get("previousClose") if hasattr(fi, "get") else None),
                    )
                    for v in candidates:
                        if v is not None:
                            return float(v)
                break
            except Exception:
                self._sleep_backoff(i)

        # 2) info (heavier; sometimes None)
        for i in range(2):
            try:
                inf = getattr(self._tkr, "info", None) or {}
                if isinstance(inf, dict):
                    for k in ("regularMarketPrice", "currentPrice", "previousClose"):
                        v = inf.get(k)
                        if v is not None:
                            return float(v)
                break
            except Exception:
                self._sleep_backoff(i)

        # 3) history (reliable but slower)
        for i in range(2):
            try:
                hist = self._tkr.history(period="1d")
                if isinstance(hist, pd.DataFrame) and not hist.empty and "Close" in hist.columns:
                    return float(hist["Close"].iloc[-1])
            except Exception:
                self._sleep_backoff(i)

        return float("nan")

    def get_option_chain(self, exp: str, kind: str = "put") -> pd.DataFrame:
        """Fetch option chain for one expiration.

        Args:
            exp: expiration date like '2025-09-19'
            kind: 'put' or 'call'
        Returns:
            DataFrame with normalized columns and metadata.
        """
        last_exc = None
        kind = (kind or "put").lower()
        for i in range(3):
            try:
                oc = self._tkr.option_chain(exp)
                raw = oc.puts if kind == "put" else oc.calls
                df = raw.copy()

                # normalize to snake_case expected by downstream
                df = df.rename(
                    columns={
                        "contractSymbol": "contract_symbol",
                        "openInterest": "open_interest",
                        "impliedVolatility": "implied_vol",
                        "inTheMoney": "in_the_money",
                        "lastPrice": "last_price",
                    }
                )

                # ensure required columns exist
                needed = [
                    "contract_symbol",
                    "strike",
                    "bid",
                    "ask",
                    "last_price",
                    "implied_vol",
                    "in_the_money",
                    "volume",
                    "open_interest",
                ]
                for col in needed:
                    if col not in df.columns:
                        df[col] = pd.NA

                # numeric coercion
                for col in ["strike", "bid", "ask", "last_price", "implied_vol", "volume", "open_interest"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

                # metadata
                df["ticker"] = self.ticker
                df["expiration"] = exp
                df["updated_at"] = dt.datetime.utcnow().isoformat() + "Z"

                # yfinance uses IV in decimal (e.g., 0.25 = 25%); keep as-is
                return df
            except Exception as e:
                last_exc = e
                self._sleep_backoff(i)

        # fallback: empty dataframe with expected columns
        cols = [
            "contract_symbol",
            "strike",
            "bid",
            "ask",
            "last_price",
            "implied_vol",
            "in_the_money",
            "volume",
            "open_interest",
            "ticker",
            "expiration",
            "updated_at",
        ]
        return pd.DataFrame(columns=cols)