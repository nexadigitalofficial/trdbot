"""
gemini_engine.py — Google Gemini API entegrasyonu
YENİ SDK: google-genai (google-generativeai DEĞİL)
Kullanım: from google import genai + client.models.generate_content()

DEĞİŞİKLİKLER:
  - Exponential backoff retry (429 / quota aşımı için)
  - In-memory TTL cache (aynı isteği tekrar atma)
  - Asyncio semaphore ile eş zamanlı istek sınırı
  - Fallback model: gemini-2.5-flash-lite (quota bitince)
"""
import json
import logging
import asyncio
import time
import hashlib
from typing import Optional

# YENİ SDK — pip install google-genai
from google import genai
from google.genai import types

from config import (
    GEMINI_API_KEY, GEMINI_MODEL, GEMINI_GENERATION_CONFIG,
    STRONG_BUY_THRESHOLD, BUY_THRESHOLD, SELL_THRESHOLD, STRONG_SELL_THRESHOLD
)
from prompts import (
    OMNI_TRADER_SYSTEM, OMEGA_SINGULARITY_SYSTEM, STOCK_SCANNER_SYSTEM,
    COMMAND_PARSER_SYSTEM,
    build_analysis_prompt, build_deep_analysis_prompt,
    build_osint_scan_prompt, build_command_parse_prompt,
    build_daily_briefing_prompt, build_weekly_osint_prompt
)

logger = logging.getLogger(__name__)

# ─── FALLBACK MODEL ───────────────────────────────────────────────────────────
# Ücretsiz kota bitince veya 429 alınınca bu modele geç
FALLBACK_MODEL = "gemini-2.5-flash-lite"   # Ana model quota bitince devreye girer

# ─── GLOBAL CLIENT ────────────────────────────────────────────────────────────
_client: Optional[genai.Client] = None

# ─── EŞ ZAMANLI İSTEK SINIRI ─────────────────────────────────────────────────
# Aynı anda en fazla 2 istek (ücretsiz kota için güvenli)
_semaphore = asyncio.Semaphore(2)

# ─── IN-MEMORY CACHE ─────────────────────────────────────────────────────────
# {cache_key: {"value": ..., "expires_at": unix_ts}}
_cache: dict = {}

# Farklı içerik türleri için TTL (saniye)
_TTL = {
    "market_data": 300,    # 5 dakika — fiyatlar değişir
    "news":        600,    # 10 dakika — haberler yavaş değişir
    "analysis":    900,    # 15 dakika — analizler pahalı, cache'le
    "signal":      180,    # 3 dakika — sinyaller kısa ömürlü
    "briefing":   3600,    # 1 saat — sabah bülteni
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
    """Süresi dolmuş cache girişlerini temizle."""
    now = time.time()
    expired = [k for k, v in _cache.items() if v["expires_at"] <= now]
    for k in expired:
        del _cache[k]


# ─── RETRY MANTIĞI ────────────────────────────────────────────────────────────
# 429 / quota aşımı için exponential backoff
_MAX_RETRIES     = 4
_BASE_WAIT       = 5    # ilk bekleme (saniye)
_MAX_WAIT        = 120  # maksimum bekleme (saniye)


def _is_quota_error(e: Exception) -> bool:
    """429 veya quota hatası mı?"""
    msg = str(e).lower()
    return any(x in msg for x in ["429", "quota", "resource_exhausted",
                                    "resourceexhausted", "rate_limit"])


def _extract_retry_delay(e: Exception) -> Optional[float]:
    """Hata mesajından 'retry in Xs' süresini çıkar."""
    msg = str(e)
    import re
    m = re.search(r"retry[^\d]*(\d+(?:\.\d+)?)\s*s", msg, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _call_with_retry(fn, *args, category: str = "default", **kwargs):
    """
    Verilen callable'ı retry + backoff ile çalıştır.
    429 alınca bekler ve fallback model ile tekrar dener.
    """
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if not _is_quota_error(e):
                # Quota dışı hata — tekrar deneme
                raise

            suggested = _extract_retry_delay(e)
            wait = suggested if suggested else min(_BASE_WAIT * (2 ** attempt), _MAX_WAIT)

            logger.warning(
                f"⏳ Gemini quota aşıldı (deneme {attempt+1}/{_MAX_RETRIES}). "
                f"{wait:.1f}s bekleniyor... [{category}]"
            )
            time.sleep(wait)

            # Son denemede fallback model'e geç
            if attempt == _MAX_RETRIES - 2:
                logger.info(f"🔄 Fallback model'e geçiliyor: {FALLBACK_MODEL}")
                # kwargs'a fallback model'i ekle (fn içinde kullanılacak)
                kwargs["_use_fallback"] = True

    logger.error(f"❌ Tüm retry denemeleri başarısız: {last_exc}")
    raise last_exc


# ─── CLIENT ───────────────────────────────────────────────────────────────────

def init_gemini():
    """Gemini API client'ını başlat (yeni SDK)."""
    global _client
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY ayarlanmamış! .env dosyasını kontrol et.")
    _client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info(f"✅ Gemini client başlatıldı: {GEMINI_MODEL} (fallback: {FALLBACK_MODEL})")


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        init_gemini()
    return _client


# ─── GOOGLE SEARCH TOOL ───────────────────────────────────────────────────────

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


# ─── TEMEL API ÇAĞRILARI ──────────────────────────────────────────────────────

def _call_gemini_raw(system_prompt: str, user_prompt: str,
                     max_tokens: int, use_search: bool,
                     _use_fallback: bool = False) -> str:
    """
    Tek bir Gemini API isteği. retry mantığı yoktur — _call_with_retry içinden çağrılır.
    """
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
    """Gemini'ya senkron API çağrısı (retry + backoff dahil)."""
    return _call_with_retry(
        _call_gemini_raw,
        system_prompt, user_prompt, max_tokens, False,
        category=category
    )


def _call_gemini_with_search(system_prompt: str, user_prompt: str,
                              max_tokens: int = 2048,
                              category: str = "default") -> str:
    """
    Gemini + Google Search grounding ile senkron API çağrısı (retry + backoff dahil).
    Search başarısız olursa search olmadan dener.
    """
    try:
        return _call_with_retry(
            _call_gemini_raw,
            system_prompt, user_prompt, max_tokens, True,
            category=category
        )
    except Exception as e:
        if _is_quota_error(e):
            raise  # quota hataları yukarıya yayılsın
        logger.warning(f"Gemini search başarısız, standart moda geçiliyor: {e}")
        return _call_gemini(system_prompt, user_prompt, max_tokens, category)


async def _call_gemini_async(system_prompt: str, user_prompt: str,
                              max_tokens: int = 4096,
                              category: str = "default") -> str:
    """Async Gemini çağrısı — semaphore ile eş zamanlı sınır uygulanır."""
    async with _semaphore:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, _call_gemini, system_prompt, user_prompt, max_tokens, category
        )


async def _call_gemini_with_search_async(system_prompt: str, user_prompt: str,
                                          max_tokens: int = 2048,
                                          category: str = "default") -> str:
    """Async Gemini + Google Search çağrısı — semaphore ile eş zamanlı sınır."""
    async with _semaphore:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, _call_gemini_with_search,
            system_prompt, user_prompt, max_tokens, category
        )


# ─── GEMİNİ İLE PİYASA VERİSİ ÇEKME ─────────────────────────────────────────

_MARKET_DATA_SYSTEM = """Sen finansal veri uzmanısın. 
Verilen hisse senedinin güncel piyasa verilerini web'den araştır ve bul.
SADECE geçerli JSON döndür, hiçbir ek açıklama yazma."""


def get_market_data_gemini_sync(ticker: str, market: str = "NASDAQ") -> dict:
    """
    Gemini + Google Search ile piyasa verisi çek.
    Cache: 5 dakika.
    """
    cache_key = _cache_key("market_data", ticker, market)
    cached = _cache_get(cache_key)
    if cached:
        logger.info(f"✅ Market data cache hit: {ticker}")
        return cached

    suffix   = ".IS" if market == "BIST" else ""
    currency = "TRY" if market == "BIST" else "USD"
    full_tk  = f"{ticker}{suffix}"

    prompt = f""""{full_tk}" hissesinin GÜNCEL piyasa verilerini internetten araştır.
Bugünün tarihi dikkate al. Aşağıdaki JSON formatında döndür:

{{
  "current_price": (sayı - güncel fiyat),
  "price_change_pct": (sayı - bugünkü % değişim),
  "rsi": (sayı 0-100 arası - bilmiyorsan 50 yaz),
  "ma50": (sayı - 50 günlük hareketli ortalama),
  "ma200": (sayı - 200 günlük hareketli ortalama),
  "ma20": (sayı - 20 günlük hareketli ortalama),
  "volume_ratio": (sayı - bugünkü hacim/ortalama hacim oranı),
  "high_52w": (sayı - 52 hafta yüksek),
  "low_52w": (sayı - 52 hafta düşük),
  "beta": (sayı - bilmiyorsan 1.0 yaz),
  "pe_ratio": (sayı veya "N/A"),
  "market_cap": (string - örn: "5.2B" veya "N/A"),
  "sector": (string - sektör),
  "industry": (string - endüstri),
  "short_name": "{ticker}",
  "currency": "{currency}"
}}

Bulamadığın sayısal değerleri null olarak bırak. SADECE JSON."""

    try:
        raw   = _call_gemini_with_search(_MARKET_DATA_SYSTEM, prompt,
                                          max_tokens=1024, category="market_data")
        clean = _clean_json(raw)
        data  = json.loads(clean)

        price = float(data.get("current_price") or 0)
        if price <= 0:
            return {}

        def _f(val, default=None):
            try:
                return float(val) if val is not None else default
            except Exception:
                return default

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
        logger.info(f"✅ Gemini search ile veri alındı: {ticker} @ {price}")
        _cache_set(cache_key, result, _TTL["market_data"])
        return result
    except Exception as e:
        logger.error(f"Gemini market data hatası ({ticker}): {e}")
        return {}


async def fetch_market_data_via_gemini(ticker: str, market: str = "NASDAQ") -> dict:
    """Async wrapper — bot.py tarafından kullanılır."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, get_market_data_gemini_sync, ticker, market
    )


# ─── GEMİNİ İLE HABER ÇEKME ──────────────────────────────────────────────────

_NEWS_SYSTEM = """Sen finansal haber araştırmacısısın.
Verilen konuyla ilgili güncel haberleri internetten araştır.
SADECE geçerli JSON array döndür."""


def get_news_gemini_sync(query: str, limit: int = 8) -> list:
    """
    Gemini + Google Search ile haber çek.
    Cache: 10 dakika.
    """
    cache_key = _cache_key("news", query, limit)
    cached = _cache_get(cache_key)
    if cached:
        logger.info(f"✅ News cache hit: {query[:40]}")
        return cached

    prompt = f""""{query}" konusuyla ilgili son 3 günün en önemli {limit} finansal haberini araştır.

Aşağıdaki JSON array formatında döndür:
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
        raw   = _call_gemini_with_search(_NEWS_SYSTEM, prompt,
                                          max_tokens=1500, category="news")
        clean = _clean_json(raw, is_array=True)
        news  = json.loads(clean)
        if isinstance(news, list):
            logger.info(f"✅ Gemini search ile {len(news)} haber alındı: {query[:40]}")
            result = news[:limit]
            _cache_set(cache_key, result, _TTL["news"])
            return result
    except Exception as e:
        logger.debug(f"Gemini news hatası ({query[:40]}): {e}")
    return []


async def fetch_news_via_gemini(query: str, limit: int = 8) -> list:
    """Async wrapper — bot.py tarafından kullanılır."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, get_news_gemini_sync, query, limit
    )


# ─── KOMUT PARSER ─────────────────────────────────────────────────────────────

async def parse_command(message: str) -> dict:
    """Doğal dil mesajını komuta dönüştür. Cache yok (her mesaj farklı)."""
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
        logger.error(f"parse_command hatası: {e}")
        return {"command": "unknown", "ticker": None, "raw_intent": str(e)}


# ─── HİSSE ANALİZİ ────────────────────────────────────────────────────────────

async def analyze_stock(ticker: str, current_price: float,
                         market_data: dict, timeframe: str = "swing",
                         news: list = None, deep: bool = False) -> str:
    """
    Hisse analizi yap.
    Cache: 15 dakika (aynı ticker + timeframe + deep).
    deep=True  → 12-boyutlu OMEGA analizi
    deep=False → 6-boyutlu OMNI-TRADER analizi
    """
    cache_key = _cache_key("analysis", ticker, timeframe, deep)
    cached = _cache_get(cache_key)
    if cached:
        logger.info(f"✅ Analiz cache hit: {ticker}/{timeframe}")
        return cached + "\n\n_⚡ Bu analiz önbellekten sunuldu._"

    source_note = ""
    if market_data.get("_source") == "gemini_search":
        source_note = "\n⚠️ _Piyasa verisi Gemini Search ile alındı._\n"
    elif not market_data:
        source_note = "\n⚠️ _Piyasa verisi alınamadı, Gemini kendi bilgisiyle analiz etti._\n"

    try:
        if deep:
            prompt = build_deep_analysis_prompt(
                ticker, current_price, market_data, timeframe, news
            )
            result = await _call_gemini_with_search_async(
                OMEGA_SINGULARITY_SYSTEM, prompt, max_tokens=4096, category="analysis"
            )
        else:
            prompt = build_analysis_prompt(
                ticker, current_price, market_data, timeframe, news
            )
            result = await _call_gemini_with_search_async(
                OMNI_TRADER_SYSTEM, prompt, max_tokens=3000, category="analysis"
            )
        final = source_note + result if source_note else result
        _cache_set(cache_key, final, _TTL["analysis"])
        return final
    except Exception as e:
        logger.error(f"analyze_stock hatası ({ticker}): {e}")
        return f"❌ Analiz hatası: {e}"


async def analyze_stock_gemini_only(ticker: str, market: str = "NASDAQ",
                                     timeframe: str = "swing",
                                     deep: bool = False) -> str:
    """
    Hiç veri yokken Gemini + Google Search ile tam analiz yap.
    Cache: 15 dakika.
    """
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

Sonra tam analiz yap ve şu formatta döndür:

{"OMEGA-SINGULARITY PROTOKOLÜ ile 12-boyutlu analiz" if deep else "OMNI-TRADER ile 6-boyutlu analiz"}

Zaman dilimi: {timeframe.upper()}
Piyasa: {market}

Analizi Türkçe yaz, emoji kullan, Markdown formatı kullan.
⚠️ UYARI: Yatırım tavsiyesi değildir."""

    try:
        result = await _call_gemini_with_search_async(
            system, prompt, max_tokens=4096, category="analysis"
        )
        _cache_set(cache_key, result, _TTL["analysis"])
        return result
    except Exception as e:
        logger.error(f"Gemini-only analiz hatası ({ticker}): {e}")
        return f"❌ Analiz hatası: {e}"


# ─── SİNYAL ÜRETICI ───────────────────────────────────────────────────────────

async def generate_signal(ticker: str, current_price: float,
                           market_data: dict, news: list = None) -> dict:
    """
    Hızlı al/sat sinyali üret.
    Cache: 3 dakika.
    """
    cache_key = _cache_key("signal", ticker, round(current_price, 2))
    cached = _cache_get(cache_key)
    if cached:
        logger.debug(f"Signal cache hit: {ticker}")
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
- Veri Kaynağı: {market_data.get('_source', 'yfinance')}

Haberler: {', '.join(n.get('title','') for n in (news or [])[:3])}

SADECE JSON döndür:
{{
  "signal": "BUY|SELL|HOLD",
  "nsg": sayı_-10_ile_10_arası,
  "confidence": sayı_0_ile_100_arası,
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

        nsg = float(data.get("nsg", 0))
        if nsg >= STRONG_BUY_THRESHOLD:
            data["signal_class"] = "🚀 ULTRA BULLISH"
        elif nsg >= BUY_THRESHOLD:
            data["signal_class"] = "📈 GÜÇLÜ AL"
        elif nsg <= STRONG_SELL_THRESHOLD:
            data["signal_class"] = "🩸 ULTRA BEARISH"
        elif nsg <= SELL_THRESHOLD:
            data["signal_class"] = "📉 GÜÇLÜ SAT"
        else:
            data["signal_class"] = "⚖️ NÖTR/BEKLE"

        _cache_set(cache_key, data, _TTL["signal"])
        return data
    except Exception as e:
        logger.warning(f"generate_signal hatası ({ticker}): {e}")
        return {"signal": "HOLD", "nsg": 0, "confidence": 0,
                "reason": str(e), "signal_class": "⚖️ NÖTR"}


# ─── PORTFÖY SİNYALLERİ ───────────────────────────────────────────────────────

async def scan_portfolio_signals(portfolio_data: dict) -> list:
    """Tüm portföy için sinyal tara."""
    signals = []
    for ticker, data in portfolio_data.items():
        current = data.get("current_price")
        if current is None:
            continue
        try:
            sig = await generate_signal(ticker, current, data)
            if sig["signal"] != "HOLD" or abs(sig.get("nsg", 0)) > 5:
                signals.append({
                    "ticker":  ticker,
                    "price":   current,
                    "pnl_pct": data.get("pnl_pct", 0),
                    **sig
                })
        except Exception as e:
            logger.debug(f"Sinyal hatası {ticker}: {e}")

    signals.sort(key=lambda x: abs(x.get("nsg", 0)), reverse=True)
    return signals


# ─── OSINT TARAMA ─────────────────────────────────────────────────────────────

async def run_osint_scan(market: str, timeframe: str,
                          candidates_data: dict) -> str:
    """OSINT hisse tarama çalıştır. Cache: 15 dakika."""
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
            result = await _call_gemini_with_search_async(
                STOCK_SCANNER_SYSTEM, prompt, max_tokens=3000, category="analysis"
            )
        else:
            prompt = f"""{market} piyasasında şu an en çok hareket eden,
fırsat sunan hisseleri araştır. {timeframe.upper()} vade için en iyi 5 fırsat hissesini bul.

Her hisse için:
- Güncel fiyat ve % değişim
- Neden bu hafta fırsat sunuyor
- Giriş bölgesi, hedef fiyat, stop loss
- Risk/Ödül oranı

Türkçe, emoji kullan, Markdown formatı.
⚠️ YASAL UYARI: Yatırım tavsiyesi değildir."""
            result = await _call_gemini_with_search_async(
                STOCK_SCANNER_SYSTEM, prompt, max_tokens=3000, category="analysis"
            )
        _cache_set(cache_key, result, _TTL["analysis"])
        return result
    except Exception as e:
        logger.error(f"OSINT scan hatası: {e}")
        return f"❌ Tarama hatası: {e}"


# ─── SABAH BÜLTENİ ────────────────────────────────────────────────────────────

async def generate_daily_briefing(portfolio_data: dict,
                                   market_summary: dict,
                                   top_news: list) -> str:
    """Günlük sabah bülteni üret. Cache: 1 saat."""
    from datetime import date
    cache_key = _cache_key("briefing", str(date.today()))
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        if len(top_news) < 3:
            logger.info("Haber yetersiz, Gemini Search ile tamamlanıyor...")
            extra    = get_news_gemini_sync("borsa istanbul nasdaq bugün haberleri", limit=6)
            top_news = top_news + extra

        prompt = build_daily_briefing_prompt(portfolio_data, market_summary, top_news)
        result = await _call_gemini_with_search_async(
            OMNI_TRADER_SYSTEM, prompt, max_tokens=2000, category="briefing"
        )
        _cache_set(cache_key, result, _TTL["briefing"])
        return result
    except Exception as e:
        logger.error(f"Daily briefing hatası: {e}")
        return f"❌ Bülten üretme hatası: {e}"


# ─── HAFTALIK TARAMA ─────────────────────────────────────────────────────────

async def generate_weekly_scan(bist_movers: list, nasdaq_movers: list,
                                 macro_data: dict, top_news: list) -> str:
    """Haftalık OSINT tarama raporu üret. Cache: 1 saat."""
    from datetime import date
    cache_key = _cache_key("weekly_scan", str(date.today()))
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        if len(top_news) < 5:
            extra    = get_news_gemini_sync(
                "bist nasdaq haftalık en çok kazanan hisseler", limit=8
            )
            top_news = top_news + extra

        prompt = build_weekly_osint_prompt(
            bist_movers, nasdaq_movers, macro_data, top_news
        )
        result = await _call_gemini_with_search_async(
            STOCK_SCANNER_SYSTEM, prompt, max_tokens=4096, category="analysis"
        )
        _cache_set(cache_key, result, _TTL["briefing"])
        return result
    except Exception as e:
        logger.error(f"Weekly scan hatası: {e}")
        return f"❌ Haftalık tarama hatası: {e}"


# ─── SERBEST SOHBET ───────────────────────────────────────────────────────────

async def chat(message: str, context: str = "") -> str:
    """Serbest piyasa sorusu — güncel bilgi için Google Search kullanır."""
    system = """Sen profesyonel bir trading asistanısın.
Kullanıcının finans ve trading sorularını Türkçe yanıtla.
Güncel piyasa soruları için web araması yap.
Kısa, net ve pratik ol. Gerektiğinde uyarı ekle."""

    prompt = f"{context}\n\nKullanıcı: {message}" if context else message

    try:
        return await _call_gemini_with_search_async(
            system, prompt, max_tokens=1500, category="default"
        )
    except Exception as e:
        if _is_quota_error(e):
            return (
                "⏳ *Gemini API kotası geçici olarak doldu.*\n"
                "Birkaç dakika bekleyip tekrar dene.\n"
                f"Bekleme süresi: ~{_extract_retry_delay(e) or 60:.0f}s"
            )
        return f"❌ Hata: {e}"


# ─── YARDIMCI FONKSİYONLAR ───────────────────────────────────────────────────

def _clean_json(raw: str, is_array: bool = False) -> str:
    """Markdown kod bloklarını temizleyip JSON string'i döndür."""
    clean = raw.strip()
    if "```" in clean:
        parts = clean.split("```")
        clean = parts[1] if len(parts) > 1 else clean
        if clean.startswith("json"):
            clean = clean[4:]
    clean = clean.strip()
    if is_array:
        start = clean.find("[")
        end   = clean.rfind("]") + 1
    else:
        start = clean.find("{")
        end   = clean.rfind("}") + 1
    if start >= 0 and end > start:
        clean = clean[start:end]
    return clean


def get_cache_stats() -> dict:
    """Cache istatistiklerini döndür (debug için)."""
    now = time.time()
    alive   = sum(1 for v in _cache.values() if v["expires_at"] > now)
    expired = len(_cache) - alive
    return {"total": len(_cache), "alive": alive, "expired": expired}