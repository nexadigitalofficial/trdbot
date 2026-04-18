"""
portfolio.py — JSON tabanlı portföy veritabanı yöneticisi
"""
import json
import os
from datetime import datetime
from typing import Optional
import logging

from config import DB_PATH

logger = logging.getLogger(__name__)

# ─── VERİTABANI ŞEMASI ───────────────────────────────────────────────────────
DEFAULT_DB = {
    "positions": {},      # ticker → position dict
    "watchlist": [],      # izleme listesi
    "alerts": {},         # ticker → alert dict
    "trade_history": [],  # işlem geçmişi
    "notes": {},          # ticker → not
    "metadata": {
        "created_at": "",
        "last_updated": "",
        "total_trades": 0
    }
}

# ─── YARDIMCI FONKSİYONLAR ───────────────────────────────────────────────────

def _load() -> dict:
    """DB'yi yükle, yoksa oluştur."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if not os.path.exists(DB_PATH):
        db = DEFAULT_DB.copy()
        db["metadata"]["created_at"] = datetime.now().isoformat()
        _save(db)
        return db
    with open(DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def _save(db: dict) -> None:
    """DB'yi kaydet."""
    db["metadata"]["last_updated"] = datetime.now().isoformat()
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def _detect_market(ticker: str) -> str:
    """Ticker'dan piyasayı tespit et."""
    from config import BIST_WATCHLIST, BIST_MARKET, NASDAQ_MARKET
    ticker_upper = ticker.upper().replace(".IS", "")
    if ticker_upper in BIST_WATCHLIST:
        return BIST_MARKET
    # Basit heuristik: Türkçe harfli veya bilinen BIST pattern
    if any(c in ticker_upper for c in "ÇĞİÖŞÜ"):
        return BIST_MARKET
    return NASDAQ_MARKET  # Varsayılan NASDAQ

# ─── POZİSYON YÖNETİMİ ───────────────────────────────────────────────────────

def buy(ticker: str, shares: float, price: float,
        market: Optional[str] = None, note: str = "") -> dict:
    """
    Portföye hisse ekle veya mevcut pozisyonu artır.
    Returns: güncellenmiş pozisyon dict
    """
    db   = _load()
    tick = ticker.upper().replace(".IS", "")
    mkt  = market or _detect_market(tick)

    if tick in db["positions"]:
        pos = db["positions"][tick]
        old_shares = pos["shares"]
        old_avg    = pos["avg_price"]
        new_shares = old_shares + shares
        # Ağırlıklı ortalama maliyet
        new_avg    = ((old_shares * old_avg) + (shares * price)) / new_shares
        pos["shares"]    = round(new_shares, 6)
        pos["avg_price"] = round(new_avg, 4)
        pos["last_buy"]  = price
        pos["last_updated"] = datetime.now().isoformat()
        pos["buy_count"]    = pos.get("buy_count", 1) + 1
    else:
        db["positions"][tick] = {
            "shares":       round(shares, 6),
            "avg_price":    round(price, 4),
            "last_buy":     price,
            "market":       mkt,
            "currency":     "TRY" if mkt == "BIST" else "USD",
            "added_date":   datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "buy_count":    1
        }

    # İşlem geçmişine ekle
    db["trade_history"].append({
        "action":    "BUY",
        "ticker":    tick,
        "shares":    shares,
        "price":     price,
        "market":    mkt,
        "timestamp": datetime.now().isoformat(),
        "note":      note
    })
    db["metadata"]["total_trades"] = db["metadata"].get("total_trades", 0) + 1

    if note:
        db["notes"][tick] = note

    _save(db)
    logger.info(f"BUY: {tick} x{shares} @ {price}")
    return db["positions"][tick]


def sell(ticker: str, shares: float, price: float, note: str = "") -> dict:
    """
    Portföyden hisse çıkar.
    Returns: {"sold": shares, "remaining": shares, "pnl": float, "pnl_pct": float}
    """
    db   = _load()
    tick = ticker.upper().replace(".IS", "")

    if tick not in db["positions"]:
        raise ValueError(f"{tick} portföyde bulunamadı.")

    pos     = db["positions"][tick]
    avg     = pos["avg_price"]
    current = pos["shares"]
    sell_s  = min(shares, current)

    pnl     = round((price - avg) * sell_s, 2)
    pnl_pct = round(((price - avg) / avg) * 100, 2) if avg > 0 else 0

    db["trade_history"].append({
        "action":    "SELL",
        "ticker":    tick,
        "shares":    sell_s,
        "price":     price,
        "pnl":       pnl,
        "pnl_pct":   pnl_pct,
        "avg_cost":  avg,
        "timestamp": datetime.now().isoformat(),
        "note":      note
    })
    db["metadata"]["total_trades"] = db["metadata"].get("total_trades", 0) + 1

    remaining = round(current - sell_s, 6)
    if remaining <= 0.0001:
        del db["positions"][tick]
        status = "closed"
    else:
        pos["shares"]       = remaining
        pos["last_updated"] = datetime.now().isoformat()
        status = "partial"

    _save(db)
    logger.info(f"SELL: {tick} x{sell_s} @ {price} | PnL: {pnl_pct}%")
    return {
        "sold":      sell_s,
        "remaining": remaining,
        "pnl":       pnl,
        "pnl_pct":   pnl_pct,
        "status":    status
    }


def get_position(ticker: str) -> Optional[dict]:
    """Tekli pozisyon bilgisi döndür."""
    db   = _load()
    tick = ticker.upper().replace(".IS", "")
    return db["positions"].get(tick)


def get_all_positions() -> dict:
    """Tüm pozisyonları döndür."""
    return _load()["positions"]


def get_portfolio_summary() -> dict:
    """Portföy özeti (sayısal, fiyat olmadan)."""
    db  = _load()
    pos = db["positions"]
    return {
        "total_positions": len(pos),
        "tickers":         list(pos.keys()),
        "markets": {
            "BIST":   [t for t, v in pos.items() if v.get("market") == "BIST"],
            "NASDAQ": [t for t, v in pos.items() if v.get("market") in ("NASDAQ", "NYSE")],
        },
        "total_trades":    db["metadata"].get("total_trades", 0),
        "last_updated":    db["metadata"].get("last_updated", "")
    }


# ─── İZLEME LİSTESİ ──────────────────────────────────────────────────────────

def add_watchlist(ticker: str) -> list:
    db   = _load()
    tick = ticker.upper().replace(".IS", "")
    if tick not in db["watchlist"]:
        db["watchlist"].append(tick)
        _save(db)
    return db["watchlist"]

def remove_watchlist(ticker: str) -> list:
    db   = _load()
    tick = ticker.upper().replace(".IS", "")
    if tick in db["watchlist"]:
        db["watchlist"].remove(tick)
        _save(db)
    return db["watchlist"]

def get_watchlist() -> list:
    return _load()["watchlist"]

# ─── UYARILAR ─────────────────────────────────────────────────────────────────

def set_alert(ticker: str, target_price: float, direction: str = "above") -> dict:
    """
    Fiyat uyarısı ayarla.
    direction: 'above' | 'below'
    """
    db   = _load()
    tick = ticker.upper().replace(".IS", "")
    db["alerts"][tick] = {
        "target_price": target_price,
        "direction":    direction,
        "created_at":   datetime.now().isoformat(),
        "triggered":    False
    }
    _save(db)
    return db["alerts"][tick]

def get_alerts() -> dict:
    return _load()["alerts"]

def mark_alert_triggered(ticker: str) -> None:
    db   = _load()
    tick = ticker.upper().replace(".IS", "")
    if tick in db["alerts"]:
        db["alerts"][tick]["triggered"]    = True
        db["alerts"][tick]["triggered_at"] = datetime.now().isoformat()
        _save(db)

# ─── GEÇMİŞ ──────────────────────────────────────────────────────────────────

def get_trade_history(limit: int = 20) -> list:
    db = _load()
    return db["trade_history"][-limit:]

def get_closed_pnl() -> dict:
    """Kapatılmış pozisyonların toplam PnL'i."""
    db     = _load()
    sells  = [t for t in db["trade_history"] if t["action"] == "SELL"]
    total  = sum(t.get("pnl", 0) for t in sells)
    wins   = sum(1 for t in sells if t.get("pnl", 0) > 0)
    losses = sum(1 for t in sells if t.get("pnl", 0) < 0)
    return {
        "total_pnl":   round(total, 2),
        "total_sells": len(sells),
        "wins":        wins,
        "losses":      losses,
        "win_rate":    round((wins / len(sells) * 100) if sells else 0, 1)
    }

# ─── NOT ──────────────────────────────────────────────────────────────────────

def set_note(ticker: str, note: str) -> None:
    db   = _load()
    tick = ticker.upper().replace(".IS", "")
    db["notes"][tick] = note
    _save(db)

def get_note(ticker: str) -> str:
    db   = _load()
    tick = ticker.upper().replace(".IS", "")
    return db["notes"].get(tick, "")
