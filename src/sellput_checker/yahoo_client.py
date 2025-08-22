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