"""
osint_scanner.py — OSINT & haber tarayıcısı
Kaynak: yfinance news, RSS, NewsAPI, Google News RSS
RSS/API başarısız olursa → Gemini + Google Search devreye girer
"""
import logging
import feedparser
import requests
from datetime import datetime, timedelta
from typing import Optional

from config import NEWS_API_KEY

logger = logging.getLogger(__name__)

# ─── RSS FEED KAYNAKLARI ──────────────────────────────────────────────────────

BIST_RSS_FEEDS = [
    "https://feeds.bbci.co.uk/turkce/rss.xml",
    "https://www.hurriyet.com.tr/rss/ekonomi",
    "https://www.bloomberght.com/rss",
    "https://feeds.feedburner.com/Doviz-Com",
    "https://www.borsagundem.com/rss/haberler",
    "https://www.ntv.com.tr/ekonomi.rss",
]

NASDAQ_RSS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^IXIC&region=US&lang=en-US",
    "https://www.marketwatch.com/rss/topstories",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL,NVDA,MSFT,TSLA&region=US&lang=en-US",
]

CRYPTO_RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
]


def _fetch_rss(url: str, limit: int = 10) -> list:
    """RSS feed'den haber çek."""
    try:
        feed    = feedparser.parse(url)
        entries = feed.entries[:limit]
        news    = []
        for e in entries:
            published = ""
            if hasattr(e, "published_parsed") and e.published_parsed:
                from time import mktime
                published = datetime.fromtimestamp(
                    mktime(e.published_parsed)
                ).strftime("%Y-%m-%d %H:%M")
            news.append({
                "title":     getattr(e, "title", ""),
                "summary":   getattr(e, "summary", "")[:200],
                "link":      getattr(e, "link", ""),
                "published": published,
                "source":    feed.feed.get("title", url)
            })
        return news
    except Exception as e:
        logger.debug(f"RSS fetch hatası {url}: {e}")
        return []


def _fetch_yfinance_news(ticker: str, market: str = None) -> list:
    """yfinance ile hisse haberlerini çek."""
    try:
        import yfinance as yf
        from config import BIST_SUFFIX, BIST_MARKET
        yt = ticker.upper()
        if market == BIST_MARKET or ticker.upper() in _get_bist_tickers():
            yt += BIST_SUFFIX
        stk  = yf.Ticker(yt)
        news = stk.news or []
        result = []
        for n in news[:10]:
            result.append({
                "title":     n.get("title", ""),
                "summary":   n.get("summary", "")[:200],
                "link":      n.get("link", ""),
                "published": datetime.fromtimestamp(
                    n.get("providerPublishTime", 0)
                ).strftime("%Y-%m-%d %H:%M") if n.get("providerPublishTime") else "",
                "source":    n.get("publisher", "Yahoo Finance")
            })
        return result
    except Exception as e:
        logger.debug(f"yfinance news hatası {ticker}: {e}")
        return []


def _fetch_newsapi(query: str, limit: int = 10) -> list:
    """NewsAPI.org'dan haber çek."""
    if not NEWS_API_KEY:
        return []
    try:
        url    = "https://newsapi.org/v2/everything"
        params = {
            "q":        query,
            "apiKey":   NEWS_API_KEY,
            "language": "tr",
            "sortBy":   "publishedAt",
            "pageSize": limit,
            "from":     (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            articles = resp.json().get("articles", [])
            return [{
                "title":     a.get("title", ""),
                "summary":   (a.get("description") or "")[:200],
                "link":      a.get("url", ""),
                "published": a.get("publishedAt", "")[:16],
                "source":    a.get("source", {}).get("name", "NewsAPI")
            } for a in articles]
    except Exception as e:
        logger.debug(f"NewsAPI hatası: {e}")
    return []


def _fetch_gemini_news(query: str, limit: int = 8) -> list:
    """
    Gemini + Google Search ile haber çek.
    RSS ve NewsAPI başarısız olunca kullanılır.
    """
    try:
        from gemini_engine import get_news_gemini_sync
        return get_news_gemini_sync(query, limit=limit)
    except Exception as e:
        logger.debug(f"Gemini news fallback hatası ({query}): {e}")
        return []


def _get_bist_tickers() -> set:
    from config import BIST_WATCHLIST
    return set(BIST_WATCHLIST)

# ─── ANA FONKSİYONLAR ────────────────────────────────────────────────────────

def get_stock_news(ticker: str, market: str = None, limit: int = 8) -> list:
    """
    Belirli bir hisse için haber topla.
    Kaynak: yfinance → NewsAPI → Gemini Search
    """
    news = []

    # 1. yfinance
    news.extend(_fetch_yfinance_news(ticker, market))

    # 2. NewsAPI
    if NEWS_API_KEY and len(news) < 5:
        news.extend(_fetch_newsapi(ticker, limit=5))

    # 3. Gemini Search fallback
    if len(news) < 3:
        logger.info(f"Haber yetersiz ({len(news)}), Gemini Search ile aranıyor: {ticker}")
        suffix = " hisse" if market == "BIST" else " stock"
        news.extend(_fetch_gemini_news(f"{ticker}{suffix} son haberler", limit=limit))

    # Dedupe
    seen  = set()
    dedup = []
    for n in news:
        title = n.get("title", "")
        if title and title not in seen:
            seen.add(title)
            dedup.append(n)

    return dedup[:limit]


def get_market_news(market: str = "BIST", limit: int = 15) -> list:
    """
    Piyasa geneli haber topla.
    Kaynak: RSS → NewsAPI → Gemini Search
    """
    news = []

    if market == "BIST":
        for url in BIST_RSS_FEEDS[:3]:
            news.extend(_fetch_rss(url, limit=5))
        if NEWS_API_KEY:
            news.extend(_fetch_newsapi("borsa istanbul hisse", limit=5))
    elif market in ("NASDAQ", "NYSE"):
        for url in NASDAQ_RSS_FEEDS[:3]:
            news.extend(_fetch_rss(url, limit=5))
        if NEWS_API_KEY:
            news.extend(_fetch_newsapi("nasdaq stock market", limit=5))
    else:
        for url in BIST_RSS_FEEDS[:2] + NASDAQ_RSS_FEEDS[:2]:
            news.extend(_fetch_rss(url, limit=4))

    # Gemini Search fallback
    if len(news) < 5:
        logger.info(f"Piyasa haberi yetersiz ({len(news)}), Gemini Search devreye giriyor: {market}")
        if market == "BIST":
            news.extend(_fetch_gemini_news("borsa istanbul bugün hisse haberleri", limit=8))
        elif market in ("NASDAQ", "NYSE"):
            news.extend(_fetch_gemini_news("nasdaq stock market news today", limit=8))
        else:
            news.extend(_fetch_gemini_news("global stock market news today", limit=8))

    # Dedupe
    seen  = set()
    dedup = []
    for n in news:
        title = n.get("title", "")
        if title and title not in seen:
            seen.add(title)
            dedup.append(n)

    return dedup[:limit]


def get_all_news_for_briefing() -> dict:
    """
    Sabah bülteni için tüm haberleri topla.
    Her kategori için Gemini Search fallback uygulanır.
    Returns: {bist: [...], nasdaq: [...], macro: [...], crypto: [...]}
    """
    bist_news   = get_market_news("BIST", limit=8)
    nasdaq_news = get_market_news("NASDAQ", limit=8)

    # Makro haberler
    macro_news = _fetch_rss("https://feeds.bbci.co.uk/news/business/rss.xml", limit=5)
    if not macro_news:
        macro_news = _fetch_gemini_news("global economy macro news today", limit=5)

    # Kripto haberler
    crypto_news = _fetch_rss(CRYPTO_RSS_FEEDS[0], limit=3)
    if not crypto_news:
        crypto_news = _fetch_gemini_news("bitcoin crypto news today", limit=3)

    return {
        "bist":   bist_news,
        "nasdaq": nasdaq_news,
        "macro":  macro_news,
        "crypto": crypto_news
    }


def get_osint_signals(tickers: list, market: str) -> dict:
    """
    Ticker listesi için OSINT haberleri topla.
    Returns: {ticker: [news_list]}
    """
    result = {}
    for ticker in tickers[:20]:
        news = get_stock_news(ticker, market, limit=5)
        if news:
            result[ticker] = news
    return result


def build_news_context(news_list: list) -> str:
    """Haber listesini metin haline getir."""
    if not news_list:
        return "Güncel haber bulunamadı."
    lines = []
    for n in news_list[:8]:
        title  = n.get("title", "")
        source = n.get("source", "")
        pub    = n.get("published", "")
        if title:
            lines.append(f"• [{pub[:10]}] {title} ({source})")
    return "\n".join(lines)


def search_ticker_osint(ticker: str) -> dict:
    """
    Belirli ticker için kapsamlı OSINT bilgi topla.
    """
    news = get_stock_news(ticker, limit=10)

    # Google News RSS (Türkçe)
    tr_news = _fetch_rss(
        f"https://news.google.com/rss/search?q={ticker}+hisse&hl=tr&gl=TR&ceid=TR:tr",
        limit=5
    )

    # Google News RSS (İngilizce)
    en_news = _fetch_rss(
        f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en",
        limit=5
    )

    all_news = news + tr_news + en_news

    # Gemini fallback (hala yetersizse)
    if len(all_news) < 5:
        all_news.extend(_fetch_gemini_news(f"{ticker} hisse haberleri analiz", limit=6))

    # Dedupe
    seen  = set()
    dedup = []
    for n in all_news:
        title = n.get("title", "")
        if title and title not in seen:
            seen.add(title)
            dedup.append(n)

    return {
        "ticker":     ticker,
        "news_count": len(dedup),
        "news":       dedup[:12],
        "fetched_at": datetime.now().isoformat()
    }
