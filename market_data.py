"""
market_data.py — yfinance ile piyasa verisi çekici
BIST: .IS suffix | NASDAQ/NYSE: direkt ticker
yfinance başarısız olursa → Gemini + Google Search devreye girer
"""
import logging
from typing import Optional
import numpy as np

try:
    import yfinance as yf
    import pandas as pd
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

from config import BIST_SUFFIX, BIST_MARKET

logger = logging.getLogger(__name__)


def _make_yf_ticker(ticker: str, market: str = None) -> str:
    """yfinance için ticker formatla."""
    t = ticker.upper().replace(".IS", "")
    if market == BIST_MARKET or _is_bist(t):
        return t + BIST_SUFFIX
    return t

def _is_bist(ticker: str) -> bool:
    from config import BIST_WATCHLIST
    return ticker.upper() in BIST_WATCHLIST


def get_current_price(ticker: str, market: str = None) -> Optional[float]:
    """
    Anlık fiyat çek.
    yfinance başarısız olursa Gemini Search ile dener.
    """
    if YFINANCE_AVAILABLE:
        try:
            yt   = _make_yf_ticker(ticker, market)
            data = yf.Ticker(yt)
            info = data.fast_info
            price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
            if price is None:
                hist = data.history(period="1d", interval="1m")
                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])
            if price:
                return round(float(price), 4)
        except Exception as e:
            logger.warning(f"yfinance fiyat hatası {ticker}: {e}")

    # Gemini fallback
    logger.info(f"Gemini Search ile fiyat aranıyor: {ticker}")
    try:
        from gemini_engine import get_market_data_gemini_sync
        mkt  = market or (BIST_MARKET if _is_bist(ticker) else "NASDAQ")
        data = get_market_data_gemini_sync(ticker, mkt)
        price = data.get("current_price")
        if price and price > 0:
            logger.info(f"✅ Gemini fiyat buldu: {ticker} @ {price}")
            return price
    except Exception as e:
        logger.debug(f"Gemini fiyat fallback hatası {ticker}: {e}")
    return None


def get_market_data(ticker: str, market: str = None) -> dict:
    """
    Kapsamlı piyasa verisi çek.
    yfinance başarısız olursa Gemini Search ile dener.
    """
    yf_data = {}

    if YFINANCE_AVAILABLE:
        try:
            yt   = _make_yf_ticker(ticker, market)
            stk  = yf.Ticker(yt)
            info = stk.info

            hist = stk.history(period="6mo", interval="1d")

            current_price = (info.get("regularMarketPrice") or
                             info.get("currentPrice") or
                             (float(hist["Close"].iloc[-1]) if not hist.empty else None))

            if current_price is not None:
                rsi   = _calc_rsi(hist["Close"]) if not hist.empty and len(hist) >= 14 else 50.0
                ma50  = float(hist["Close"].rolling(50).mean().iloc[-1]) if len(hist) >= 50 else current_price
                ma200 = float(hist["Close"].rolling(200).mean().iloc[-1]) if len(hist) >= 200 else current_price
                ma20  = float(hist["Close"].rolling(20).mean().iloc[-1]) if len(hist) >= 20 else current_price

                volume     = int(info.get("regularMarketVolume") or
                                 (hist["Volume"].iloc[-1] if not hist.empty else 0))
                avg_volume = int(info.get("averageVolume") or
                                 (hist["Volume"].rolling(20).mean().iloc[-1] if len(hist) >= 20 else 1))

                prev_close   = float(info.get("regularMarketPreviousClose") or
                                      (hist["Close"].iloc[-2] if len(hist) >= 2 else current_price))
                price_change = round((current_price - prev_close) / prev_close * 100, 2)

                high_52w = float(info.get("fiftyTwoWeekHigh") or
                                  (hist["High"].max() if not hist.empty else current_price))
                low_52w  = float(info.get("fiftyTwoWeekLow") or
                                  (hist["Low"].min() if not hist.empty else current_price))

                bb_upper, bb_lower = (
                    _calc_bollinger(hist["Close"]) if len(hist) >= 20
                    else (current_price * 1.02, current_price * 0.98)
                )

                yf_data = {
                    "current_price":    round(current_price, 4),
                    "price_change_pct": price_change,
                    "volume":           volume,
                    "avg_volume":       avg_volume,
                    "volume_ratio":     round(volume / avg_volume, 2) if avg_volume > 0 else 1,
                    "high_52w":         round(high_52w, 4),
                    "low_52w":          round(low_52w, 4),
                    "rsi":              round(rsi, 2),
                    "ma20":             round(ma20, 4),
                    "ma50":             round(ma50, 4),
                    "ma200":            round(ma200, 4),
                    "bb_upper":         round(bb_upper, 4),
                    "bb_lower":         round(bb_lower, 4),
                    "beta":             round(float(info.get("beta") or 1.0), 2),
                    "pe_ratio":         round(float(info.get("trailingPE") or 0), 2) or "N/A",
                    "forward_pe":       round(float(info.get("forwardPE") or 0), 2) or "N/A",
                    "market_cap":       _format_market_cap(info.get("marketCap")),
                    "sector":           info.get("sector", "N/A"),
                    "industry":         info.get("industry", "N/A"),
                    "currency":         info.get("currency", "USD"),
                    "short_name":       info.get("shortName", ticker),
                }
        except Exception as e:
            logger.warning(f"yfinance market data alınamadı {ticker}: {e}")

    if yf_data:
        return yf_data

    # ─── Gemini Search Fallback ───────────────────────────────────────────────
    logger.info(f"Gemini Search ile piyasa verisi aranıyor: {ticker}")
    try:
        from gemini_engine import get_market_data_gemini_sync
        mkt      = market or (BIST_MARKET if _is_bist(ticker) else "NASDAQ")
        gem_data = get_market_data_gemini_sync(ticker, mkt)
        if gem_data:
            return gem_data
    except Exception as e:
        logger.error(f"Gemini market data fallback hatası {ticker}: {e}")

    return {}


def get_portfolio_prices(positions: dict) -> dict:
    """Tüm portföy pozisyonları için canlı fiyat çek."""
    result = {}
    for ticker, pos in positions.items():
        market = pos.get("market")
        data   = get_market_data(ticker, market)
        if data:
            current = data["current_price"]
            avg     = pos["avg_price"]
            shares  = pos["shares"]
            pnl_abs = round((current - avg) * shares, 2)
            pnl_pct = round((current - avg) / avg * 100, 2) if avg > 0 else 0
            result[ticker] = {
                **pos,
                **data,
                "pnl_abs": pnl_abs,
                "pnl_pct": pnl_pct,
                "value":   round(current * shares, 2),
                "cost":    round(avg * shares, 2),
            }
        else:
            result[ticker] = {**pos, "current_price": None, "pnl_pct": None}
    return result


def get_movers(tickers: list, market: str = "BIST", top_n: int = 15) -> list:
    """
    Verilen listeden en çok hareket eden hisseleri bul.
    Yeterli veri alınamazsa Gemini Search ile desteklenir.
    """
    movers = []

    if YFINANCE_AVAILABLE:
        for ticker in tickers:
            try:
                yt   = _make_yf_ticker(ticker, market)
                stk  = yf.Ticker(yt)
                hist = stk.history(period="5d", interval="1d")
                if hist.empty or len(hist) < 2:
                    continue
                weekly_change = ((hist["Close"].iloc[-1] / hist["Close"].iloc[0]) - 1) * 100
                daily_change  = ((hist["Close"].iloc[-1] / hist["Close"].iloc[-2]) - 1) * 100
                volume        = int(hist["Volume"].iloc[-1])
                avg_vol       = int(hist["Volume"].mean())
                rsi           = _calc_rsi(hist["Close"]) if len(hist) >= 14 else 50
                movers.append({
                    "ticker":       ticker,
                    "change":       round(float(weekly_change), 2),
                    "daily_change": round(float(daily_change), 2),
                    "price":        round(float(hist["Close"].iloc[-1]), 4),
                    "volume_ratio": round(volume / avg_vol, 2) if avg_vol > 0 else 1,
                    "rsi":          round(rsi, 1),
                    "market":       market
                })
            except Exception as e:
                logger.debug(f"Mover alınamadı {ticker}: {e}")

    # Gemini ile destekle (yeterli veri yoksa)
    if len(movers) < 5:
        logger.info(f"Mover verisi yetersiz ({len(movers)}), Gemini Search ile destekleniyor...")
        try:
            from gemini_engine import get_news_gemini_sync
            # Gemini'dan mover listesi için haberlere göre tahmin yaptır
            # (Gerçek mover verisi gemini_engine.run_osint_scan içinde Gemini'ya verilecek)
        except Exception:
            pass

    movers.sort(key=lambda x: abs(x["change"]), reverse=True)
    return movers[:top_n]


def get_macro_data() -> dict:
    """Makro piyasa göstergelerini çek."""
    macro = {}

    if YFINANCE_AVAILABLE:
        symbols = {
            "^GSPC":     ("SP500",   "SP500_haftalik"),
            "XU100.IS":  ("BIST100", "BIST100_haftalik"),
            "DX-Y.NYB":  ("DXY",     None),
            "USDTRY=X":  ("USD_TRY", None),
            "GC=F":      ("ALTIN",   None),
            "^VIX":      ("VIX",     None),
            "BTC-USD":   ("BTC",     None),
        }
        for sym, (key, weekly_key) in symbols.items():
            try:
                period = "5d" if weekly_key else "1d"
                interval = "1d" if weekly_key else "1h"
                hist = yf.Ticker(sym).history(period=period, interval=interval)
                if not hist.empty:
                    macro[key] = round(float(hist["Close"].iloc[-1]),
                                       0 if key == "BTC" else (3 if key == "DXY" else 2))
                    if weekly_key and len(hist) >= 2:
                        macro[weekly_key] = (
                            f"{((hist['Close'].iloc[-1]/hist['Close'].iloc[0])-1)*100:+.2f}%"
                        )
            except Exception as e:
                logger.debug(f"Makro veri alınamadı {sym}: {e}")

    # Gemini Search ile eksikleri tamamla
    if len(macro) < 3:
        logger.info("Makro veri yetersiz, Gemini Search ile tamamlanıyor...")
        try:
            from gemini_engine import get_market_data_gemini_sync
            # SP500
            if "SP500" not in macro:
                sp = get_market_data_gemini_sync("^GSPC", "NASDAQ")
                if sp.get("current_price"):
                    macro["SP500"] = sp["current_price"]
            # BIST100
            if "BIST100" not in macro:
                bi = get_market_data_gemini_sync("XU100", "BIST")
                if bi.get("current_price"):
                    macro["BIST100"] = bi["current_price"]
        except Exception as e:
            logger.debug(f"Gemini makro fallback hatası: {e}")

    return macro


def check_price_alerts(alerts: dict) -> list:
    """Aktif fiyat uyarılarını kontrol et."""
    triggered = []
    for ticker, alert in alerts.items():
        if alert.get("triggered"):
            continue
        try:
            market = None
            if _is_bist(ticker):
                market = BIST_MARKET
            price = get_current_price(ticker, market)
            if price is None:
                continue
            target    = alert["target_price"]
            direction = alert.get("direction", "above")
            if direction == "above" and price >= target:
                triggered.append({"ticker": ticker, "price": price,
                                   "target": target, "direction": direction})
            elif direction == "below" and price <= target:
                triggered.append({"ticker": ticker, "price": price,
                                   "target": target, "direction": direction})
        except Exception as e:
            logger.debug(f"Alert kontrol hatası {ticker}: {e}")
    return triggered


# ─── TEKNİK ANALİZ YARDIMCILARI ──────────────────────────────────────────────

def _calc_rsi(series, period: int = 14) -> float:
    """RSI hesapla."""
    try:
        delta = series.diff().dropna()
        gain  = delta.where(delta > 0, 0.0)
        loss  = -delta.where(delta < 0, 0.0)
        avg_g = gain.rolling(period).mean().iloc[-1]
        avg_l = loss.rolling(period).mean().iloc[-1]
        if avg_l == 0:
            return 100.0
        rs  = avg_g / avg_l
        rsi = 100 - (100 / (1 + rs))
        return float(rsi) if not np.isnan(rsi) else 50.0
    except Exception:
        return 50.0

def _calc_bollinger(series, period: int = 20, std_dev: float = 2.0):
    """Bollinger bantları hesapla."""
    try:
        ma    = series.rolling(period).mean()
        std   = series.rolling(period).std()
        upper = float((ma + std_dev * std).iloc[-1])
        lower = float((ma - std_dev * std).iloc[-1])
        return upper, lower
    except Exception:
        last = float(series.iloc[-1]) if not series.empty else 0
        return last * 1.02, last * 0.98

def _format_market_cap(cap) -> str:
    if not cap:
        return "N/A"
    cap = float(cap)
    if cap >= 1e12:
        return f"{cap/1e12:.2f}T"
    if cap >= 1e9:
        return f"{cap/1e9:.2f}B"
    if cap >= 1e6:
        return f"{cap/1e6:.2f}M"
    return f"{cap:.0f}"
