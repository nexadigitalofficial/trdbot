"""
config.py — Merkezi Konfigürasyon
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── TELEGRAM ────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID      = int(os.getenv("ADMIN_CHAT_ID", "0"))

# ─── GEMINI ──────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = "gemini-2.5-flash"   # Ana model (2.0 serisi Haziran 2026'da kapatılıyor)

# ─── NEWS ────────────────────────────────────────────────────────────────────
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

# ─── ZAMANLAMA ───────────────────────────────────────────────────────────────
DAILY_REPORT_TIME      = os.getenv("DAILY_REPORT_TIME", "08:00")
WEEKLY_SCAN_DAY        = os.getenv("WEEKLY_SCAN_DAY", "monday")
WEEKLY_SCAN_TIME       = os.getenv("WEEKLY_SCAN_TIME", "07:00")
SIGNAL_CHECK_INTERVAL  = int(os.getenv("SIGNAL_CHECK_INTERVAL", "30"))  # dakika

# ─── VERİTABANI ──────────────────────────────────────────────────────────────
DB_PATH          = os.path.join(os.path.dirname(__file__), "data", "portfolio.json")
SIGNALS_LOG_PATH = os.path.join(os.path.dirname(__file__), "data", "signals_log.json")

# ─── PİYASA ──────────────────────────────────────────────────────────────────
# BIST hisseleri için yfinance suffix
BIST_SUFFIX  = ".IS"
BIST_MARKET  = "BIST"
NASDAQ_MARKET = "NASDAQ"
NYSE_MARKET   = "NYSE"

# Desteklenen piyasalar
SUPPORTED_MARKETS = [BIST_MARKET, NASDAQ_MARKET, NYSE_MARKET]

# BIST büyük cap listesi (haftalık tarama için)
BIST_WATCHLIST = [
    "THYAO", "ASELS", "BIMAS", "EREGL", "GARAN", "ISCTR", "KCHOL",
    "KOZAL", "PETKM", "SAHOL", "SASA", "TCELL", "TOASO", "TUPRS",
    "AKBNK", "ARCLK", "ENKAI", "FROTO", "HEKTS", "MGROS", "OYAKC",
    "PGSUS", "SELEC", "VAKBN", "YKBNK", "CCOLA", "DOAS", "EKGYO",
    "GUBRF", "KONTR", "OTKAR", "REEDR", "SOKM", "TAVHL", "TTKOM"
]

# NASDAQ izleme listesi
NASDAQ_WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    "AMD", "INTC", "QCOM", "AVGO", "MU", "AMAT", "LRCX", "ASML",
    "NFLX", "UBER", "ABNB", "SNOW", "PLTR", "SOFI", "COIN",
    "SMCI", "ARM", "MRVL", "CRWD", "ZS", "PANW", "DDOG", "NET"
]

# ─── SİNYAL EŞİKLERİ ─────────────────────────────────────────────────────────
STRONG_BUY_THRESHOLD  =  7.0   # NSG > 7 → GÜÇLÜ AL
BUY_THRESHOLD         =  4.0   # NSG > 4 → AL
SELL_THRESHOLD        = -3.0   # NSG < -3 → SAT
STRONG_SELL_THRESHOLD = -7.0   # NSG < -7 → GÜÇLÜ SAT

# Fiyat değişim uyarı eşiği (%)
PRICE_ALERT_THRESHOLD = 3.0

# ─── MESAJ ───────────────────────────────────────────────────────────────────
MAX_MESSAGE_LENGTH = 4000   # Telegram mesaj limiti

# ─── GEMİNİ GÜVENLİK AYARLARI ────────────────────────────────────────────────
GEMINI_GENERATION_CONFIG = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 4096,
}
