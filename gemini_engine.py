"""
gemini_engine.py — Google Gemini API entegrasyonu
YENİ SDK: google-genai (google-generativeai DEĞİL)
YENİ ÖZELLİKLER:
  - Profesyonel fiyat analizi (5-kategori skorlama)
  - Elite hisse bulucu (çok aşamalı tarama)
  - Gelişmiş chatbot (bağlam + tarih)
  - Strateji danışmanı
  - Exponential backoff retry + in-memory TTL cache + semaphore
"""
import json
import logging
import asyncio
import time
import hashlib
from typing import Optional

from google import genai
from google.genai import types

from config import (
    GEMINI_API_KEY, GEMINI_MODEL, GEMINI_GENERATION_CONFIG,
    STRONG_BUY_THRESHOLD, BUY_THRESHOLD, SELL_THRESHOLD, STRONG_SELL_THRESHOLD
)
from prompts import (
    OMNI_TRADER_SYSTEM, OMEGA_SINGULARITY_SYSTEM, STOCK_SCANNER_SYSTEM,
    COMMAND_PARSER_SYSTEM, PROFESSIONAL_ANALYST_SYSTEM, ELITE_SCANNER_SYSTEM,
    ADVANCED_CHATBOT_SYSTEM, STRATEGY_ADVISOR_SYSTEM,
    build_analysis_prompt, build_deep_analysis_prompt,
    build_osint_scan_prompt, build_command_parse_prompt,
    build_daily_briefing_prompt, build_weekly_osint_prompt,
    build_professional_price_analysis_prompt,
    build_elite_stock_finder_prompt,
    build_advanced_chat_prompt,
    build_strategy_prompt,
)

logger = logging.getLogger(__name__)

FALLBACK_MODEL = "gemini-2.5-flash-lite"

_client: Optional[genai.Client] = None
_semaphore = asyncio.Semaphore(2)
_cache: dict = {}

_TTL = {
    "market_data": 300,
    "news":        600,
    "analysis":    900,
    "signal":      180,
    "briefing":   3600,
    "price_anal":  900,
    "finder":      600,
    "strategy":   1800,
    "default":     300,
}


def _cache_key(prefix: str, *args) -> str:
    raw = prefix + "|" + "|".join(str(a) for a in args)
    return hashlib.md5(raw.encode()).hexdigest()

def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and entry["expires_at"] > time.time():
        return entry["value"]
    return None

def _cache_set(key: str, value, ttl: int = 300):
    _cache[key] = {"value": value, "expires_at": time.time() + ttl}

def _cache_cleanup():
    now = time.time()
    expired = [k for k, v in _cache.items() if v["expires_at"] <= now]
    for k in expired:
        del _cache[k]


_MAX_RETRIES = 4
_BASE_WAIT   = 5
_MAX_WAIT    = 120

def _is_quota_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(x in msg for x in ["429", "quota", "resource_exhausted",
                                    "resourceexhausted", "rate_limit"])

def _extract_retry_delay(e: Exception) -> Optional[float]:
    import re
    m = re.search(r"retry[^\d]*(\d+(?:\.\d+)?)\s*s", str(e), re.IGNORECASE)
    return float(m.group(1)) if m else None

def _call_with_retry(fn, *args, category: str = "default", **kwargs):
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if not _is_quota_error(e):
                raise
            suggested = _extract_retry_delay(e)
            wait = suggested if suggested else min(_BASE_WAIT * (2 ** attempt), _MAX_WAIT)
            logger.warning(f"⏳ Gemini quota (deneme {attempt+1}/{_MAX_RETRIES}). {wait:.1f}s [{category}]")
            time.sleep(wait)
            if attempt == _MAX_RETRIES - 2:
                logger.info(f"🔄 Fallback: {FALLBACK_MODEL}")
                kwargs["_use_fallback"] = True
    logger.error(f"❌ Tüm retry başarısız: {last_exc}")
    raise last_exc


def init_gemini():
    global _client
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY ayarlanmamış!")
    _client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info(f"✅ Gemini: {GEMINI_MODEL} (fallback: {FALLBACK_MODEL})")

def _get_client() -> genai.Client:
    global _client
    if _client is None:
        init_gemini()
    return _client

def _make_search_tool() -> types.Tool:
    return types.Tool(google_search=types.GoogleSearch())

def _make_config(system_prompt: str, max_tokens: int,
                  use_search: bool = False) -> types.GenerateContentConfig:
    cfg = {
        "system_instruction": system_prompt,
        "temperature":        GEMINI_GENERATION_CONFIG.get("temperature", 0.7),
        "top_p":              GEMINI_GENERATION_CONFIG.get("top_p", 0.95),
        "max_output_tokens":  max_tokens,
    }
    if use_search:
        cfg["tools"] = [_make_search_tool()]
    return types.GenerateContentConfig(**cfg)


def _call_gemini_raw(system_prompt: str, user_prompt: str,
                     max_tokens: int, use_search: bool,
                     _use_fallback: bool = False) -> str:
    client = _get_client()
    model  = FALLBACK_MODEL if _use_fallback else GEMINI_MODEL
    config = _make_config(system_prompt, max_tokens, use_search=use_search)
    response = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=config,
    )
    return response.text.strip()

def _call_gemini(system_prompt: str, user_prompt: str,
                 max_tokens: int = 4096, category: str = "default") -> str:
    return _call_with_retry(
        _call_gemini_raw,
        system_prompt, user_prompt, max_tokens, False,
        category=category
    )

def _call_gemini_with_search(system_prompt: str, user_prompt: str,
                              max_tokens: int = 2048,
                              category: str = "default") -> str:
    try:
        return _call_with_retry(
            _call_gemini_raw,
            system_prompt, user_prompt, max_tokens, True,
            category=category
        )
    except Exception as e:
        if _is_quota_error(e):
            raise
        logger.warning(f"Search başarısız, standart mod: {e}")
        return _call_gemini(system_prompt, user_prompt, max_tokens, category)

async def _call_gemini_async(system_prompt: str, user_prompt: str,
                              max_tokens: int = 4096,
                              category: str = "default") -> str:
    async with _semaphore:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, _call_gemini, system_prompt, user_prompt, max_tokens, category
        )

async def _call_gemini_with_search_async(system_prompt: str, user_prompt: str,
                                          max_tokens: int = 2048,
                                          category: str = "default") -> str:
    async with _semaphore:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, _call_gemini_with_search,
            system_prompt, user_prompt, max_tokens, category
        )


# ─── GEMİNİ İLE PİYASA VERİSİ ────────────────────────────────────────────────

_MARKET_DATA_SYSTEM = """Sen finansal veri uzmanısın. 
Verilen hisse senedinin güncel piyasa verilerini web'den araştır ve bul.
SADECE geçerli JSON döndür, hiçbir ek açıklama yazma."""

def get_market_data_gemini_sync(ticker: str, market: str = "NASDAQ") -> dict:
    cache_key = _cache_key("market_data", ticker, market)
    cached = _cache_get(cache_key)
    if cached:
        return cached

    suffix   = ".IS" if market == "BIST" else ""
    currency = "TRY" if market == "BIST" else "USD"

    prompt = f""""{ticker}{suffix}" hissesinin GÜNCEL piyasa verilerini internetten araştır.
Aşağıdaki JSON formatında döndür:

{{
  "current_price": (sayı),
  "price_change_pct": (sayı),
  "rsi": (sayı 0-100),
  "ma50": (sayı),
  "ma200": (sayı),
  "ma20": (sayı),
  "volume_ratio": (sayı),
  "high_52w": (sayı),
  "low_52w": (sayı),
  "beta": (sayı),
  "pe_ratio": (sayı veya "N/A"),
  "market_cap": (string),
  "sector": (string),
  "industry": (string),
  "short_name": "{ticker}",
  "currency": "{currency}"
}}

Bulamadığın sayısal değerleri null bırak. SADECE JSON."""

    try:
        raw   = _call_gemini_with_search(_MARKET_DATA_SYSTEM, prompt,
                                          max_tokens=1024, category="market_data")
        clean = _clean_json(raw)
        data  = json.loads(clean)
        price = float(data.get("current_price") or 0)
        if price <= 0:
            return {}

        def _f(val, default=None):
            try: return float(val) if val is not None else default
            except: return default

        result = {
            "current_price":    round(price, 4),
            "price_change_pct": round(_f(data.get("price_change_pct"), 0.0), 2),
            "rsi":              round(_f(data.get("rsi"), 50.0), 2),
            "ma20":             round(_f(data.get("ma20"), price), 4),
            "ma50":             round(_f(data.get("ma50"), price), 4),
            "ma200":            round(_f(data.get("ma200"), price), 4),
            "volume":           0,
            "avg_volume":       1,
            "volume_ratio":     round(_f(data.get("volume_ratio"), 1.0), 2),
            "high_52w":         round(_f(data.get("high_52w"), price * 1.3), 4),
            "low_52w":          round(_f(data.get("low_52w"),  price * 0.7), 4),
            "bb_upper":         round(price * 1.02, 4),
            "bb_lower":         round(price * 0.98, 4),
            "beta":             round(_f(data.get("beta"), 1.0), 2),
            "pe_ratio":         data.get("pe_ratio", "N/A"),
            "forward_pe":       "N/A",
            "market_cap":       str(data.get("market_cap", "N/A")),
            "sector":           str(data.get("sector", "N/A")),
            "industry":         str(data.get("industry", "N/A")),
            "short_name":       str(data.get("short_name", ticker)),
            "currency":         currency,
            "_source":          "gemini_search"
        }
        logger.info(f"✅ Gemini veri: {ticker} @ {price}")
        _cache_set(cache_key, result, _TTL["market_data"])
        return result
    except Exception as e:
        logger.error(f"Gemini market data ({ticker}): {e}")
        return {}

async def fetch_market_data_via_gemini(ticker: str, market: str = "NASDAQ") -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_market_data_gemini_sync, ticker, market)


# ─── GEMİNİ İLE HABER ────────────────────────────────────────────────────────

_NEWS_SYSTEM = """Sen finansal haber araştırmacısısın.
Verilen konuyla ilgili güncel haberleri internetten araştır.
SADECE geçerli JSON array döndür."""

def get_news_gemini_sync(query: str, limit: int = 8) -> list:
    cache_key = _cache_key("news", query, limit)
    cached = _cache_get(cache_key)
    if cached:
        return cached

    prompt = f""""{query}" konusuyla ilgili son 3 günün en önemli {limit} finansal haberini araştır.

[
  {{
    "title": "haber başlığı",
    "summary": "kısa özet max 200 karakter",
    "published": "YYYY-MM-DD",
    "source": "kaynak adı",
    "link": ""
  }}
]

Bulamıyorsan boş array [] döndür. SADECE JSON."""

    try:
        raw   = _call_gemini_with_search(_NEWS_SYSTEM, prompt, max_tokens=1500, category="news")
        clean = _clean_json(raw, is_array=True)
        news  = json.loads(clean)
        if isinstance(news, list):
            result = news[:limit]
            _cache_set(cache_key, result, _TTL["news"])
            return result
    except Exception as e:
        logger.debug(f"Gemini news ({query[:40]}): {e}")
    return []

async def fetch_news_via_gemini(query: str, limit: int = 8) -> list:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_news_gemini_sync, query, limit)


# ─── KOMUT PARSER ─────────────────────────────────────────────────────────────

async def parse_command(message: str) -> dict:
    try:
        prompt = build_command_parse_prompt(message)
        raw    = await _call_gemini_async(
            COMMAND_PARSER_SYSTEM, prompt, max_tokens=512, category="default"
        )
        clean = _clean_json(raw)
        return json.loads(clean)
    except json.JSONDecodeError:
        logger.warning(f"Komut parse edilemedi: {message}")
        return {"command": "unknown", "ticker": None, "raw_intent": message}
    except Exception as e:
        logger.error(f"parse_command: {e}")
        return {"command": "unknown", "ticker": None, "raw_intent": str(e)}


# ─── HİSSE ANALİZİ ────────────────────────────────────────────────────────────

async def analyze_stock(ticker: str, current_price: float,
                         market_data: dict, timeframe: str = "swing",
                         news: list = None, deep: bool = False) -> str:
    cache_key = _cache_key("analysis", ticker, timeframe, deep)
    cached = _cache_get(cache_key)
    if cached:
        return cached + "\n\n_⚡ Bu analiz önbellekten sunuldu._"

    source_note = ""
    if market_data.get("_source") == "gemini_search":
        source_note = "\n⚠️ _Piyasa verisi Gemini Search ile alındı._\n"

    try:
        if deep:
            prompt = build_deep_analysis_prompt(ticker, current_price, market_data, timeframe, news)
            result = await _call_gemini_with_search_async(
                OMEGA_SINGULARITY_SYSTEM, prompt, max_tokens=4096, category="analysis"
            )
        else:
            prompt = build_analysis_prompt(ticker, current_price, market_data, timeframe, news)
            result = await _call_gemini_with_search_async(
                OMNI_TRADER_SYSTEM, prompt, max_tokens=3000, category="analysis"
            )
        final = source_note + result if source_note else result
        _cache_set(cache_key, final, _TTL["analysis"])
        return final
    except Exception as e:
        logger.error(f"analyze_stock ({ticker}): {e}")
        return f"❌ Analiz hatası: {e}"


async def analyze_stock_gemini_only(ticker: str, market: str = "NASDAQ",
                                     timeframe: str = "swing",
                                     deep: bool = False) -> str:
    cache_key = _cache_key("analysis_only", ticker, market, timeframe, deep)
    cached = _cache_get(cache_key)
    if cached:
        return cached + "\n\n_⚡ Bu analiz önbellekten sunuldu._"

    mode   = "12-boyutlu OMEGA-SINGULARITY" if deep else "6-boyutlu OMNI-TRADER"
    system = OMEGA_SINGULARITY_SYSTEM if deep else OMNI_TRADER_SYSTEM

    prompt = f"""🔍 {ticker} ({market}) için {mode} ANALİZİ yap.

Önce Google Search ile şunları araştır:
1. {ticker} güncel fiyatı ve bugünkü % değişimi
2. Son hafta fiyat hareketi ve teknik durum
3. Son 3 günün önemli haberleri
4. Sektör durumu ve makro etkenler

Sonra tam analiz yap. Zaman dilimi: {timeframe.upper()} | Piyasa: {market}
Türkçe, emoji, Markdown. ⚠️ Yatırım tavsiyesi değildir."""

    try:
        result = await _call_gemini_with_search_async(
            system, prompt, max_tokens=4096, category="analysis"
        )
        _cache_set(cache_key, result, _TTL["analysis"])
        return result
    except Exception as e:
        return f"❌ Analiz hatası: {e}"


# ─── PROFESYONEİL FİYAT ANALİZİ (YENİ) ──────────────────────────────────────

async def generate_professional_price_analysis(ticker: str, current_price: float,
                                                market_data: dict, timeframe: str = "1ay",
                                                news: list = None, market: str = "BIST") -> str:
    """
    5-kategori skorlama sistemi ile profesyonel fiyat analizi ve tahmini.
    fiyat_tahmin_prompt tabanlı.
    Cache: 15 dakika.
    """
    cache_key = _cache_key("price_anal", ticker, timeframe, market)
    cached = _cache_get(cache_key)
    if cached:
        return cached + "\n\n_⚡ Bu analiz önbellekten sunuldu._"

    source_note = ""
    if market_data.get("_source") == "gemini_search":
        source_note = "\n📡 _Piyasa verisi: Gemini Search_\n"
    elif not market_data:
        # Veri yoksa Gemini'dan çek
        source_note = "\n⚠️ _Harici veri alınamadı, Gemini araştırıyor..._\n"

    try:
        prompt = build_professional_price_analysis_prompt(
            ticker, current_price, market_data, timeframe, news, market
        )
        result = await _call_gemini_with_search_async(
            PROFESSIONAL_ANALYST_SYSTEM, prompt, max_tokens=4096, category="price_anal"
        )
        final = source_note + result if source_note else result
        _cache_set(cache_key, final, _TTL["price_anal"])
        return final
    except Exception as e:
        logger.error(f"professional_price_analysis ({ticker}): {e}")
        return f"❌ Fiyat analizi hatası: {e}"


async def generate_professional_price_analysis_only(ticker: str, market: str,
                                                     timeframe: str = "1ay") -> str:
    """
    Hiç piyasa verisi olmadan sadece Gemini+Search ile profesyonel analiz.
    """
    cache_key = _cache_key("price_anal_only", ticker, market, timeframe)
    cached = _cache_get(cache_key)
    if cached:
        return cached + "\n\n_⚡ Bu analiz önbellekten sunuldu._"

    prompt = f"""📊 {ticker} ({market}) için PROFESYONEİL FİYAT ANALİZİ yap.

Önce Google Search ile şunları araştır:
1. {ticker} güncel fiyatı, son değişim, 52H yüksek/düşük
2. Teknik göstergeler: RSI, MA20/50/200, hacim durumu
3. Temel veriler: Net kâr, EPS, P/E, borç durumu, sektör
4. Son haberler ve analist görüşleri
5. Makro faktörler: sektör trendi, emtia/döviz etkisi

Sonra şu formatta 5 kategoride puanlama yap ve fiyat tahmini hesapla:

**📊 {ticker} — PROFESYONEİL ANALİZ** ({timeframe})

5 Kategori: Temel Veriler (0.25) + Büyüme (0.25) + Teknik (0.20) + Makro (0.15) + Psikoloji (0.15)
Her kategori: -2 ile +2 arası puan

Fiyat Tahmini: Mevcut Fiyat × (1 + Toplam Skor × 0.60)
3 Senaryo: Kötümser (0.40) | Baz (0.60) | İyimser (0.80)

Detaylı teknik seviyeler, risk-getiri analizi ve yatırım stratejisi ekle.
Türkçe, emoji, Markdown. ⚠️ Yatırım tavsiyesi değildir."""

    try:
        result = await _call_gemini_with_search_async(
            PROFESSIONAL_ANALYST_SYSTEM, prompt, max_tokens=4096, category="price_anal"
        )
        _cache_set(cache_key, result, _TTL["price_anal"])
        return result
    except Exception as e:
        return f"❌ Fiyat analizi hatası: {e}"


# ─── ELİTE HİSSE BULUCU (YENİ) ───────────────────────────────────────────────

async def generate_stock_finder(market: str = "BIST", timeframe: str = "1 hafta",
                                 sector: str = "Tümü", risk: str = "Orta",
                                 count: int = 5, extra_criteria: str = "") -> str:
    """
    Çok aşamalı elite hisse bulucu.
    hisse_bulma_promt tabanlı.
    Cache: 10 dakika.
    """
    cache_key = _cache_key("finder", market, timeframe, sector, risk, count)
    cached = _cache_get(cache_key)
    if cached:
        return cached + "\n\n_⚡ Bu tarama önbellekten sunuldu._"

    try:
        prompt = build_elite_stock_finder_prompt(market, timeframe, sector, risk, count, extra_criteria)
        result = await _call_gemini_with_search_async(
            ELITE_SCANNER_SYSTEM, prompt, max_tokens=4096, category="analysis"
        )
        _cache_set(cache_key, result, _TTL["finder"])
        return result
    except Exception as e:
        logger.error(f"stock_finder: {e}")
        return f"❌ Hisse bulucu hatası: {e}"


# ─── GELİŞMİŞ CHATBOT (YENİ) ─────────────────────────────────────────────────

# Per-user chat history — basit in-memory (her deploy'da sıfırlanır)
_chat_history: dict = {}   # {user_id: [{role, content}]}

def get_chat_history(user_id: int) -> list:
    return _chat_history.get(user_id, [])

def add_to_chat_history(user_id: int, role: str, content: str):
    if user_id not in _chat_history:
        _chat_history[user_id] = []
    _chat_history[user_id].append({"role": role, "content": content[:500]})
    # Son 10 mesaj tut
    if len(_chat_history[user_id]) > 10:
        _chat_history[user_id] = _chat_history[user_id][-10:]

def clear_chat_history(user_id: int):
    _chat_history[user_id] = []

async def advanced_chat(message: str, user_id: int = 0,
                         context_data: dict = None) -> str:
    """
    Bağlam ve tarih destekli gelişmiş chatbot.
    Finans soruları, strateji, hisse yorumları.
    """
    history = get_chat_history(user_id)

    try:
        prompt = build_advanced_chat_prompt(message, history, context_data)
        result = await _call_gemini_with_search_async(
            ADVANCED_CHATBOT_SYSTEM, prompt, max_tokens=2000, category="default"
        )

        # Tarihçeye ekle
        add_to_chat_history(user_id, "user", message)
        add_to_chat_history(user_id, "assistant", result)

        return result
    except Exception as e:
        if _is_quota_error(e):
            return (
                "⏳ *Gemini API kotası geçici olarak doldu.*\n"
                f"Birkaç dakika bekle. (~{_extract_retry_delay(e) or 60:.0f}s)"
            )
        return f"❌ Hata: {e}"


# ─── STRATEJİ DANIŞMANI (YENİ) ───────────────────────────────────────────────

async def generate_strategy_advice(question: str, portfolio_summary: dict = None) -> str:
    """
    Trading stratejisi danışmanlığı.
    Cache: 30 dakika.
    """
    cache_key = _cache_key("strategy", question[:80])
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        prompt = build_strategy_prompt(question, portfolio_summary)
        result = await _call_gemini_with_search_async(
            STRATEGY_ADVISOR_SYSTEM, prompt, max_tokens=2500, category="strategy"
        )
        _cache_set(cache_key, result, _TTL["strategy"])
        return result
    except Exception as e:
        return f"❌ Strateji analizi hatası: {e}"


# ─── SİNYAL ÜRETICI ───────────────────────────────────────────────────────────

async def generate_signal(ticker: str, current_price: float,
                           market_data: dict, news: list = None) -> dict:
    cache_key = _cache_key("signal", ticker, round(current_price, 2))
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        prompt = f"""Hızlı sinyal analizi: {ticker} @ {current_price}

Veriler:
- RSI: {market_data.get('rsi', 50):.1f}
- MA50: {market_data.get('ma50', current_price):.2f}
- MA200: {market_data.get('ma200', current_price):.2f}
- Hacim Oranı: {market_data.get('volume_ratio', 1):.1f}x
- Günlük Değişim: {market_data.get('price_change_pct', 0):+.2f}%
- BB Üst: {market_data.get('bb_upper', current_price):.2f}
- BB Alt: {market_data.get('bb_lower', current_price):.2f}

Haberler: {', '.join(n.get('title','') for n in (news or [])[:3])}

SADECE JSON:
{{
  "signal": "BUY|SELL|HOLD",
  "nsg": sayı_-10_ile_10,
  "confidence": sayı_0_ile_100,
  "reason": "1 cümle gerekçe",
  "entry": sayı_veya_null,
  "stop": sayı_veya_null,
  "target": sayı_veya_null
}}"""

        raw  = await _call_gemini_async(
            "Sen kısa vadeli trading sinyal üreticisisin. SADECE JSON döndür.",
            prompt, max_tokens=300, category="signal"
        )
        data = json.loads(_clean_json(raw))
        nsg  = float(data.get("nsg", 0))

        if nsg >= STRONG_BUY_THRESHOLD:   data["signal_class"] = "🚀 ULTRA BULLISH"
        elif nsg >= BUY_THRESHOLD:         data["signal_class"] = "📈 GÜÇLÜ AL"
        elif nsg <= STRONG_SELL_THRESHOLD: data["signal_class"] = "🩸 ULTRA BEARISH"
        elif nsg <= SELL_THRESHOLD:        data["signal_class"] = "📉 GÜÇLÜ SAT"
        else:                              data["signal_class"] = "⚖️ NÖTR/BEKLE"

        _cache_set(cache_key, data, _TTL["signal"])
        return data
    except Exception as e:
        logger.warning(f"generate_signal ({ticker}): {e}")
        return {"signal": "HOLD", "nsg": 0, "confidence": 0,
                "reason": str(e), "signal_class": "⚖️ NÖTR"}


# ─── PORTFÖY SİNYALLERİ ───────────────────────────────────────────────────────

async def scan_portfolio_signals(portfolio_data: dict) -> list:
    signals = []
    for ticker, data in portfolio_data.items():
        current = data.get("current_price")
        if current is None:
            continue
        try:
            sig = await generate_signal(ticker, current, data)
            if sig["signal"] != "HOLD" or abs(sig.get("nsg", 0)) > 5:
                signals.append({"ticker": ticker, "price": current,
                                 "pnl_pct": data.get("pnl_pct", 0), **sig})
        except Exception as e:
            logger.debug(f"Sinyal {ticker}: {e}")
    signals.sort(key=lambda x: abs(x.get("nsg", 0)), reverse=True)
    return signals


# ─── OSINT TARAMA ─────────────────────────────────────────────────────────────

async def run_osint_scan(market: str, timeframe: str, candidates_data: dict) -> str:
    cache_key = _cache_key("osint", market, timeframe,
                            ",".join(sorted(candidates_data.keys())[:10]))
    cached = _cache_get(cache_key)
    if cached:
        return cached + "\n\n_⚡ Bu tarama önbellekten sunuldu._"

    try:
        if candidates_data:
            prompt = build_osint_scan_prompt(
                market, timeframe, list(candidates_data.keys()), candidates_data
            )
        else:
            prompt = f"""{market} piyasasında en çok hareket eden hisseleri araştır.
{timeframe.upper()} vade için en iyi 5 fırsat hissesini bul.
Her hisse için: güncel fiyat, % değişim, neden fırsat, giriş/hedef/stop.
Türkçe, emoji, Markdown. ⚠️ Yatırım tavsiyesi değildir."""

        result = await _call_gemini_with_search_async(
            STOCK_SCANNER_SYSTEM, prompt, max_tokens=3000, category="analysis"
        )
        _cache_set(cache_key, result, _TTL["analysis"])
        return result
    except Exception as e:
        logger.error(f"OSINT scan: {e}")
        return f"❌ Tarama hatası: {e}"


# ─── SABAH BÜLTENİ ────────────────────────────────────────────────────────────

async def generate_daily_briefing(portfolio_data: dict,
                                   market_summary: dict,
                                   top_news: list) -> str:
    from datetime import date
    cache_key = _cache_key("briefing", str(date.today()))
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        if len(top_news) < 3:
            extra    = get_news_gemini_sync("borsa istanbul nasdaq bugün haberleri", limit=6)
            top_news = top_news + extra

        prompt = build_daily_briefing_prompt(portfolio_data, market_summary, top_news)
        result = await _call_gemini_with_search_async(
            OMNI_TRADER_SYSTEM, prompt, max_tokens=2000, category="briefing"
        )
        _cache_set(cache_key, result, _TTL["briefing"])
        return result
    except Exception as e:
        logger.error(f"Daily briefing: {e}")
        return f"❌ Bülten hatası: {e}"


# ─── HAFTALIK TARAMA ─────────────────────────────────────────────────────────

async def generate_weekly_scan(bist_movers: list, nasdaq_movers: list,
                                 macro_data: dict, top_news: list) -> str:
    from datetime import date
    cache_key = _cache_key("weekly_scan", str(date.today()))
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        if len(top_news) < 5:
            extra    = get_news_gemini_sync("bist nasdaq haftalık en çok kazanan hisseler", limit=8)
            top_news = top_news + extra

        prompt = build_weekly_osint_prompt(bist_movers, nasdaq_movers, macro_data, top_news)
        result = await _call_gemini_with_search_async(
            STOCK_SCANNER_SYSTEM, prompt, max_tokens=4096, category="analysis"
        )
        _cache_set(cache_key, result, _TTL["briefing"])
        return result
    except Exception as e:
        logger.error(f"Weekly scan: {e}")
        return f"❌ Haftalık tarama hatası: {e}"


# ─── SERBEST SOHBET (Eski versiyon — geriye dönük uyumluluk) ─────────────────

async def chat(message: str, context: str = "") -> str:
    """Eski serbest sohbet — advanced_chat'e yönlendir."""
    return await advanced_chat(message, user_id=0)


# ─── YARDIMCI ─────────────────────────────────────────────────────────────────

def _clean_json(raw: str, is_array: bool = False) -> str:
    clean = raw.strip()
    if "```" in clean:
        parts = clean.split("```")
        clean = parts[1] if len(parts) > 1 else clean
        if clean.startswith("json"):
            clean = clean[4:]
    clean = clean.strip()
    if is_array:
        start, end = clean.find("["), clean.rfind("]") + 1
    else:
        start, end = clean.find("{"), clean.rfind("}") + 1
    if start >= 0 and end > start:
        clean = clean[start:end]
    return clean

def get_cache_stats() -> dict:
    now = time.time()
    alive   = sum(1 for v in _cache.values() if v["expires_at"] > now)
    return {"total": len(_cache), "alive": alive, "expired": len(_cache) - alive}
