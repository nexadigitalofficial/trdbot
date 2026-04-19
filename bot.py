"""
bot.py — Ana Telegram Botu
YENİ ÖZELLİKLER:
  - /price: 5-kategori profesyonel fiyat analizi ve tahmin
  - /find: Elite hisse bulucu (çok aşamalı tarama)
  - /strategy: Trading strateji danışmanı
  - /clear: Sohbet geçmişini sıfırla
  - Gelişmiş doğal dil — "LUMN ne olur?", "bu hafta ne alayım" vb.
  - Bağlam destekli chatbot (konuşma geçmişi)
"""
import re
import logging
import asyncio
from datetime import datetime
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

import config
from portfolio import (
    buy, sell, get_all_positions, get_portfolio_summary,
    get_trade_history, get_closed_pnl, get_watchlist,
    add_watchlist, remove_watchlist, set_alert, get_alerts
)
from market_data import (
    get_market_data, get_portfolio_prices, get_macro_data,
    get_movers, get_current_price
)
from gemini_engine import (
    init_gemini, parse_command, analyze_stock,
    generate_signal, scan_portfolio_signals,
    run_osint_scan, generate_daily_briefing,
    generate_weekly_scan, chat, advanced_chat,
    fetch_market_data_via_gemini, fetch_news_via_gemini,
    analyze_stock_gemini_only,
    generate_professional_price_analysis,
    generate_professional_price_analysis_only,
    generate_stock_finder,
    generate_strategy_advice,
    clear_chat_history, get_cache_stats
)
from osint_scanner import (
    get_stock_news, get_market_news
)
from scheduler import TradingScheduler
from utils import (
    format_portfolio_message, format_signal_message,
    format_buy_confirmation, format_sell_confirmation,
    format_history_message, format_macro_message,
    split_long_message, HELP_TEXT
)

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

_scheduler: Optional[TradingScheduler] = None


# ─── GÜVENLİK ─────────────────────────────────────────────────────────────────

def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != config.ADMIN_CHAT_ID:
            await update.message.reply_text("❌ Bu bot kişisel kullanım içindir.")
            return
        return await func(update, ctx)
    return wrapper


# ─── YARDIMCI ─────────────────────────────────────────────────────────────────

async def _send(update: Update, text: str, parse_mode: str = ParseMode.MARKDOWN):
    parts = split_long_message(text)
    for part in parts:
        try:
            await update.message.reply_text(part, parse_mode=parse_mode)
        except Exception as e:
            logger.warning(f"Mesaj gönderme: {e}")
            try:
                await update.message.reply_text(part, parse_mode=None)
            except Exception:
                pass

async def _notify(chat_id: int, text: str):
    global _bot_app
    parts = split_long_message(text)
    for part in parts:
        try:
            await _bot_app.bot.send_message(
                chat_id=chat_id, text=part, parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.warning(f"Bildirim: {e}")
            try:
                await _bot_app.bot.send_message(chat_id=chat_id, text=part)
            except Exception:
                pass

def _parse_buy_sell(text: str) -> Optional[dict]:
    text = text.strip().lower()
    m = re.match(
        r"(buy|sell|al|sat)\s+([A-Za-z]{2,10})\s*([0-9.]+)?\s*([0-9.]+)?",
        text, re.IGNORECASE
    )
    if not m:
        return None
    action = "buy" if m.group(1).lower() in ("buy", "al") else "sell"
    ticker = m.group(2).upper()
    shares = float(m.group(3)) if m.group(3) else None
    price  = float(m.group(4)) if m.group(4) else None
    return {"action": action, "ticker": ticker, "shares": shares, "price": price}

async def _get_analysis_data(ticker: str, mkt: str, status_msg) -> tuple:
    data = get_market_data(ticker, mkt)
    if not data:
        try:
            await status_msg.edit_text(
                f"⚠️ yfinance verisi alınamadı. Gemini Search ile araştırılıyor...",
                parse_mode=None
            )
        except Exception:
            pass
        data = await fetch_market_data_via_gemini(ticker, mkt)

    news = get_stock_news(ticker, mkt, limit=8)
    if not news:
        try:
            suffix = " hisse" if mkt == "BIST" else " stock"
            news = await fetch_news_via_gemini(f"{ticker}{suffix} son haberler", limit=6)
        except Exception:
            news = []
    return data, news

def _detect_market(ticker: str) -> str:
    return "BIST" if ticker.upper() in config.BIST_WATCHLIST else "NASDAQ"


# ─── MEVCUT KOMUTLAR ──────────────────────────────────────────────────────────

@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "🤖 *ALGO TRADE BOT Aktif!*\n\n"
        "Kişisel algoritmik trading asistanın.\n"
        "• BIST & NASDAQ takibi\n"
        "• Gemini AI + Google Search ile derin analiz\n"
        "• 5-kategori profesyonel fiyat tahmini 🆕\n"
        "• Elite hisse bulucu 🆕\n"
        "• Strateji danışmanı 🆕\n"
        "• Bağlam destekli chatbot 🆕\n\n"
        "/help → Tüm komutlar\n"
        "_Örnek: 'LUMN ne olur?' veya 'bu hafta ne alayım?'_"
    )
    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)


@admin_only
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    help_text = HELP_TEXT + """

━━━━━━━━━━━━━━━━━━━━━ 🆕 YENİ ÖZELLİKLER

💰 *FİYAT TAHMİNİ (5-Kategori Skorlama)*
`/price THYAO` — Profesyonel fiyat analizi ve tahmin
`/price LUMN 1ay` — 1 aylık tahmin
`/price AAPL 1hafta` — 1 haftalık tahmin

🔎 *HİSSE BULUCU (Elite Scanner)*
`/find bist` — BIST'te en iyi fırsatlar
`/find nasdaq teknoloji` — NASDAQ teknoloji hisseleri
`/find bist 1hafta enerji yüksek` — Kritere göre tara

🧠 *STRATEJİ DANIŞMANI*
`/strategy swing trading nedir` — Strateji açıkla
`/strategy DCA nasıl yapılır` — Yatırım stratejisi
`/strategy stop loss nasıl belirlenir` — Risk yönetimi

💬 *CHATBOT*
`/clear` — Sohbet geçmişini sıfırla
Doğal dil: _"LUMN ne olur?"_, _"bu hafta ne alayım?"_
_"swing mi position mu?"_, _"BIST'te ne izleyeyim?"_"""
    await _send(update, help_text)


@admin_only
async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💼 Portföy yükleniyor...", parse_mode=None)
    positions = get_all_positions()
    if not positions:
        await _send(update, format_portfolio_message({}))
        return
    port_data = get_portfolio_prices(positions)
    await _send(update, format_portfolio_message(port_data))


@admin_only
async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    positions = get_all_positions()
    if not positions:
        await update.message.reply_text("📭 Portföy boş. Önce hisse ekle.")
        return
    await update.message.reply_text("📡 Sinyaller taranıyor... (Gemini AI)", parse_mode=None)
    port_data = get_portfolio_prices(positions)
    signals   = await scan_portfolio_signals(port_data)
    await _send(update, format_signal_message(signals))


@admin_only
async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "Kullanım: /analyze TICKER [scalp|swing|position]\n"
            "Örnek: /analyze THYAO swing"
        )
        return
    ticker    = args[0].upper()
    timeframe = args[1].lower() if len(args) > 1 else "swing"
    mkt       = _detect_market(ticker)

    status_msg = await update.message.reply_text(
        f"🔍 *{ticker}* analiz ediliyor... (OMNI-TRADER 6D)",
        parse_mode=ParseMode.MARKDOWN
    )
    data, news = await _get_analysis_data(ticker, mkt, status_msg)
    try:
        await status_msg.delete()
    except Exception:
        pass

    if data:
        current_price = data.get("current_price", 0)
        if data.get("_source") == "gemini_search":
            await update.message.reply_text(
                "📡 _Veri kaynağı: Gemini Search_", parse_mode=ParseMode.MARKDOWN
            )
        result = await analyze_stock(ticker, current_price, data, timeframe, news, deep=False)
    else:
        await update.message.reply_text(
            "⚠️ _Harici veri alınamadı. Gemini kendi bilgisiyle analiz yapıyor..._",
            parse_mode=ParseMode.MARKDOWN
        )
        result = await analyze_stock_gemini_only(ticker, mkt, timeframe, deep=False)
    await _send(update, result)


@admin_only
async def cmd_deep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text("Kullanım: /deep TICKER\nÖrnek: /deep THYAO")
        return
    ticker    = args[0].upper()
    timeframe = args[1].lower() if len(args) > 1 else "position"
    mkt       = _detect_market(ticker)

    status_msg = await update.message.reply_text(
        f"🌌 *{ticker}* OMEGA-SINGULARITY 12D analiz başlatılıyor...",
        parse_mode=ParseMode.MARKDOWN
    )
    data, news = await _get_analysis_data(ticker, mkt, status_msg)
    try:
        await status_msg.delete()
    except Exception:
        pass

    if data:
        current_price = data.get("current_price", 0)
        result = await analyze_stock(ticker, current_price, data, timeframe, news, deep=True)
    else:
        result = await analyze_stock_gemini_only(ticker, mkt, timeframe, deep=True)
    await _send(update, result)


# ─── YENİ: PROFESYONEİL FİYAT ANALİZİ ───────────────────────────────────────

@admin_only
async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /price TICKER [timeframe]
    5-kategori skorlama sistemi ile profesyonel fiyat analizi ve tahmin.
    Örnek: /price THYAO | /price LUMN 1ay | /price AAPL 1hafta
    """
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "💰 *Fiyat Analizi Kullanımı:*\n"
            "`/price TICKER [zaman]`\n\n"
            "Örnekler:\n"
            "`/price THYAO` — 1 aylık tahmin\n"
            "`/price LUMN 1hafta` — 1 haftalık tahmin\n"
            "`/price AAPL 3ay` — 3 aylık tahmin\n\n"
            "5 kategoride puanlama:\n"
            "Temel Veriler + Büyüme + Teknik + Makro + Psikoloji",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    ticker    = args[0].upper()
    timeframe = args[1] if len(args) > 1 else "1ay"
    mkt       = _detect_market(ticker)

    status_msg = await update.message.reply_text(
        f"💰 *{ticker}* profesyonel fiyat analizi yapılıyor...\n"
        f"_(5-kategori skorlama + Gemini Search)_",
        parse_mode=ParseMode.MARKDOWN
    )

    data, news = await _get_analysis_data(ticker, mkt, status_msg)

    try:
        await status_msg.delete()
    except Exception:
        pass

    if data and data.get("current_price"):
        current_price = data.get("current_price", 0)
        result = await generate_professional_price_analysis(
            ticker, current_price, data, timeframe, news, mkt
        )
    else:
        await update.message.reply_text(
            f"⚠️ _Piyasa verisi alınamadı. Gemini araştırıyor..._",
            parse_mode=ParseMode.MARKDOWN
        )
        result = await generate_professional_price_analysis_only(ticker, mkt, timeframe)

    await _send(update, result)


# ─── YENİ: ELİTE HİSSE BULUCU ────────────────────────────────────────────────

@admin_only
async def cmd_find(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /find [market] [timeframe] [sector] [risk]
    Elite hisse bulucu — çok aşamalı tarama.
    Örnek: /find bist | /find nasdaq teknoloji | /find bist 1hafta enerji yüksek
    """
    args = ctx.args or []

    # Argüman parsing
    market    = "BIST"
    timeframe = "1 hafta"
    sector    = "Tümü"
    risk      = "Orta"
    count     = 5
    extra     = ""

    arg_text = " ".join(args).lower()

    # Market tespiti
    if any(x in arg_text for x in ["nasdaq", "nyse", "us", "amerikan"]):
        market = "NASDAQ"
    elif any(x in arg_text for x in ["bist", "borsa", "türk", "tr"]):
        market = "BIST"

    # Timeframe tespiti
    if "gün" in arg_text or "daily" in arg_text:
        timeframe = "1 gün"
    elif "hafta" in arg_text or "weekly" in arg_text:
        timeframe = "1 hafta"
    elif "ay" in arg_text and "3" in arg_text:
        timeframe = "3 ay"
    elif "ay" in arg_text:
        timeframe = "1 ay"

    # Risk tespiti
    if any(x in arg_text for x in ["yüksek", "agresif", "high"]):
        risk = "Yüksek"
    elif any(x in arg_text for x in ["düşük", "konservatif", "low"]):
        risk = "Düşük"

    # Sektör tespiti
    sektörler = {
        "teknoloji": "Teknoloji", "technology": "Teknoloji",
        "enerji": "Enerji", "energy": "Enerji",
        "madenci": "Madencilik", "mining": "Madencilik",
        "finans": "Finans", "finance": "Finans", "banka": "Finans",
        "sağlık": "Sağlık", "health": "Sağlık",
        "sanayi": "Sanayi", "industrial": "Sanayi",
        "perakende": "Perakende", "retail": "Perakende",
        "gıda": "Gıda", "food": "Gıda",
    }
    for key, val in sektörler.items():
        if key in arg_text:
            sector = val
            break

    # Sayı tespiti
    for arg in args:
        if arg.isdigit() and 3 <= int(arg) <= 20:
            count = int(arg)

    await update.message.reply_text(
        f"🔎 *ELİTE HİSSE BULUCU*\n\n"
        f"Piyasa: `{market}` | Vade: `{timeframe}`\n"
        f"Sektör: `{sector}` | Risk: `{risk}` | Sayı: `{count}`\n\n"
        f"_Çok aşamalı tarama başlıyor... (~2-3 dakika)_",
        parse_mode=ParseMode.MARKDOWN
    )

    result = await generate_stock_finder(market, timeframe, sector, risk, count, extra)
    header = (
        f"🔎 *ELİTE HİSSE BULUCU — {market}*\n"
        f"_{datetime.now().strftime('%d.%m.%Y %H:%M')}_\n\n"
    )
    await _send(update, header + result)


# ─── YENİ: STRATEJİ DANIŞMANI ────────────────────────────────────────────────

@admin_only
async def cmd_strategy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /strategy [soru]
    Trading stratejisi danışmanlığı.
    Örnek: /strategy swing trading nedir | /strategy DCA nasıl yapılır
    """
    if not ctx.args:
        await update.message.reply_text(
            "🧠 *Strateji Danışmanı Kullanımı:*\n"
            "`/strategy [sorunuz]`\n\n"
            "Örnekler:\n"
            "`/strategy swing trading nedir`\n"
            "`/strategy DCA nasıl yapılır`\n"
            "`/strategy stop loss nasıl belirlenir`\n"
            "`/strategy portföy çeşitlendirmesi`\n"
            "`/strategy RSI ile nasıl işlem yapılır`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    question = " ".join(ctx.args)
    await update.message.reply_text(
        f"🧠 Strateji analizi yapılıyor: _{question}_",
        parse_mode=ParseMode.MARKDOWN
    )

    # Portföy bağlamı ekle
    try:
        summary = get_portfolio_summary()
    except Exception:
        summary = None

    result = await generate_strategy_advice(question, summary)
    await _send(update, result)


# ─── YENİ: SOHBET GEÇMİŞİ SIFIRLA ───────────────────────────────────────────

@admin_only
async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Sohbet geçmişini sıfırla."""
    user_id = update.effective_user.id
    clear_chat_history(user_id)
    await update.message.reply_text(
        "🗑 *Sohbet geçmişi temizlendi.*\n"
        "Yeni bir konuşma başlatabilirsin.",
        parse_mode=ParseMode.MARKDOWN
    )


# ─── MEVCUT DİĞER KOMUTLAR ────────────────────────────────────────────────────

@admin_only
async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args      = ctx.args
    market    = "BIST"
    timeframe = "swing"

    if args:
        arg0 = args[0].lower()
        if arg0 in ("nasdaq", "nyse", "us"):
            market = "NASDAQ"
        elif arg0 in ("bist", "tr", "borsa"):
            market = "BIST"
        if len(args) > 1:
            timeframe = args[1].lower()

    await update.message.reply_text(
        f"🔭 *{market}* OSINT taraması başlıyor... (~1-2 dakika)\n"
        f"_(Gemini Search ile zenginleştirilecek)_",
        parse_mode=ParseMode.MARKDOWN
    )

    ticker_list = config.BIST_WATCHLIST[:20] if market == "BIST" else config.NASDAQ_WATCHLIST[:20]
    movers      = get_movers(ticker_list, market, top_n=15)

    candidates_data = {}
    for m in movers[:12]:
        tick = m["ticker"]
        candidates_data[tick] = {
            "current_price":    m.get("price", 0),
            "price_change_pct": m.get("daily_change", 0),
            "volume_ratio":     m.get("volume_ratio", 1),
            "rsi":              m.get("rsi", 50)
        }

    result = await run_osint_scan(market, timeframe, candidates_data)
    header = f"🔭 *{market} OSINT TARAMA — {datetime.now().strftime('%d.%m.%Y')}*\n\n"
    await _send(update, header + result)


@admin_only
async def cmd_briefing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🌅 Sabah bülteni hazırlanıyor... (Gemini Search)")
    if _scheduler:
        await _scheduler.trigger_daily_briefing()
    else:
        await update.message.reply_text("❌ Scheduler başlatılmamış.")


@admin_only
async def cmd_weekly(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔭 Haftalık tarama başlıyor... (~2-3 dakika)")
    if _scheduler:
        await _scheduler.trigger_weekly_scan()
    else:
        await update.message.reply_text("❌ Scheduler başlatılmamış.")


@admin_only
async def cmd_macro(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Makro veriler yükleniyor...", parse_mode=None)
    macro = get_macro_data()
    await _send(update, format_macro_message(macro))


@admin_only
async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    history = get_trade_history(limit=15)
    await _send(update, format_history_message(history))


@admin_only
async def cmd_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pnl = get_closed_pnl()
    msg = (
        f"📊 *GERÇEKLEŞMİŞ KAR/ZARAR*\n\n"
        f"Toplam PnL: `{pnl['total_pnl']:+.2f}`\n"
        f"Toplam Satış: `{pnl['total_sells']}`\n"
        f"✅ Kazanan: `{pnl['wins']}`\n"
        f"🔴 Kaybeden: `{pnl['losses']}`\n"
        f"🎯 Kazanma Oranı: `{pnl['win_rate']}%`"
    )
    await _send(update, msg)


@admin_only
async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text(
            "Kullanım: /alert TICKER FIYAT [above|below]\n"
            "Örnek: /alert THYAO 50 above"
        )
        return
    ticker    = args[0].upper()
    price     = float(args[1])
    direction = args[2].lower() if len(args) > 2 else "above"
    set_alert(ticker, price, direction)
    dir_txt = "üstüne çıkınca" if direction == "above" else "altına düşünce"
    await update.message.reply_text(
        f"🔔 Uyarı oluşturuldu!\n*{ticker}* `{price}` {dir_txt} bildirim alacaksın.",
        parse_mode=ParseMode.MARKDOWN
    )


@admin_only
async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    alerts = get_alerts()
    active = {k: v for k, v in alerts.items() if not v.get("triggered")}
    if not active:
        await update.message.reply_text("🔔 Aktif uyarı yok.")
        return
    lines = ["🔔 *AKTİF UYARILAR*\n"]
    for ticker, a in active.items():
        direction = "📈 >" if a["direction"] == "above" else "📉 <"
        lines.append(f"• *{ticker}*: {direction} `{a['target_price']}`")
    await _send(update, "\n".join(lines))


@admin_only
async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Kullanım: /watch TICKER")
        return
    ticker = ctx.args[0].upper()
    wl     = add_watchlist(ticker)
    await update.message.reply_text(
        f"👁 *{ticker}* izleme listesine eklendi.\nListe: {', '.join(wl)}",
        parse_mode=ParseMode.MARKDOWN
    )


@admin_only
async def cmd_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Kullanım: /unwatch TICKER")
        return
    ticker = ctx.args[0].upper()
    remove_watchlist(ticker)
    await update.message.reply_text(f"❌ *{ticker}* izleme listesinden çıkarıldı.", parse_mode=ParseMode.MARKDOWN)


@admin_only
async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    wl = get_watchlist()
    if not wl:
        await update.message.reply_text("👁 İzleme listesi boş.\n/watch TICKER ile ekle.")
        return
    lines = ["👁 *İZLEME LİSTESİ*\n"]
    for ticker in wl:
        mkt   = _detect_market(ticker)
        price = get_current_price(ticker, mkt)
        p_txt = f"`{price:.2f}`" if price else "_fiyat yok_"
        lines.append(f"• *{ticker}*: {p_txt}")
    await _send(update, "\n".join(lines))


@admin_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    summary    = get_portfolio_summary()
    cache_info = get_cache_stats()
    msg = (
        f"⚙️ *BOT DURUMU*\n\n"
        f"🟢 Çalışıyor\n"
        f"🤖 Gemini: `{config.GEMINI_MODEL}` + Google Search\n"
        f"💼 Pozisyon: `{summary['total_positions']}`\n"
        f"📊 BIST: `{len(summary['markets']['BIST'])}`\n"
        f"📊 NASDAQ: `{len(summary['markets']['NASDAQ'])}`\n"
        f"🔄 Toplam İşlem: `{summary['total_trades']}`\n"
        f"💾 Cache: `{cache_info['alive']}` aktif\n"
        f"🕐 Son Güncelleme: _{summary.get('last_updated','N/A')[:16]}_\n\n"
        f"⏰ Günlük Bülten: `{config.DAILY_REPORT_TIME}`\n"
        f"🔭 Haftalık Tarama: `{config.WEEKLY_SCAN_DAY} {config.WEEKLY_SCAN_TIME}`\n"
        f"📡 Sinyal Kontrolü: `{config.SIGNAL_CHECK_INTERVAL} dk`\n\n"
        f"🆕 *Yeni Özellikler:* /price /find /strategy /clear"
    )
    await _send(update, msg)


# ─── GELİŞMİŞ DOĞAL DİL HANDLER ─────────────────────────────────────────────

# Fiyat tahmini kalıpları: "THYAO ne olur", "LUMN tahmini", "AAPL fiyatı ne olur"
_PRICE_PATTERNS = [
    r"^([A-Z]{2,8})\s*(ne\s*olur|ne\s*olacak|tahmini?|fiyatı?\s*ne\s*olur|analiz)",
    r"(fiyat\s*tahmini?|tahmin)\s+([A-Z]{2,8})",
    r"([A-Z]{2,8})\s+(kaça\s*gelir|kaç\s*olur|nasıl\s*gider|ne\s*zaman\s*yüks)",
]

# Hisse bulma kalıpları
_FIND_PATTERNS = [
    r"(bu\s*hafta|bugün|şu\s*an)\s*(ne\s*alayım|hangi\s*hiss|fırsat|öneri)",
    r"(al|almak)\s*(için\s*)?(en\s*iyi|iyi|güzel)\s*(hiss|hisse|şirket)",
    r"(hangi\s*hiss|ne\s*hiss|bana\s*hiss|hiss\s*öner|tavsiye)",
    r"(bist|nasdaq|borsa).*(fırsat|al|öneri|tarama)",
]

# Strateji kalıpları
_STRATEGY_PATTERNS = [
    r"(swing|scalp|position|momentum|value|growth|dca)\s*(trading|stratej|nedir|nasıl)",
    r"(nasıl\s*trade|trading\s*stratej|yatırım\s*stratej)",
    r"(stop\s*loss|risk\s*yönetim|portföy\s*çeşit|pozisyon\s*büyüklüğü)",
    r"(teknik\s*analiz|temel\s*analiz|rsi|macd|bollinger|fibonacci)\s*(nedir|nasıl|kullanım)",
]

def _detect_ticker_from_text(text: str) -> Optional[str]:
    """Metinden hisse kodu tespit et."""
    # 2-8 büyük harf kalıbı
    tokens = text.upper().split()
    for token in tokens:
        clean = re.sub(r'[^A-Z]', '', token)
        if 2 <= len(clean) <= 8:
            # Bilinen BIST veya NASDAQ listesinde mi?
            if clean in config.BIST_WATCHLIST or clean in config.NASDAQ_WATCHLIST:
                return clean
    # Yaygın hisse formatı — büyük harfli kısa kelime
    m = re.search(r'\b([A-Z]{2,6})\b', text.upper())
    if m and not m.group(1) in ("BU", "NE", "VE", "DE", "DA", "MI", "MU", "AL", "SAT"):
        return m.group(1)
    return None

def _is_price_question(text: str) -> Optional[str]:
    """Fiyat tahmini sorusu mu? Ticker döndür veya None."""
    text_lower = text.lower()
    text_upper = text.upper()
    for pattern in _PRICE_PATTERNS:
        m = re.search(pattern, text_upper, re.IGNORECASE)
        if m:
            ticker = m.group(1) if m.lastindex >= 1 else m.group(2)
            if len(ticker) >= 2:
                return ticker
    return None

def _is_find_request(text: str) -> bool:
    """Hisse bulma isteği mi?"""
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in _FIND_PATTERNS)

def _is_strategy_question(text: str) -> bool:
    """Strateji sorusu mu?"""
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in _STRATEGY_PATTERNS)


@admin_only
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Gelişmiş doğal dil mesajlarını işle."""
    text    = update.message.text.strip()
    user_id = update.effective_user.id

    # 1. Hızlı buy/sell parse
    direct = _parse_buy_sell(text)
    if direct:
        await _handle_trade(update, direct)
        return

    # 2. Fiyat tahmini kalıbı tespiti (önce dene — sık kullanılan)
    price_ticker = _is_price_question(text)
    if price_ticker:
        ctx.args = [price_ticker, "1ay"]
        await cmd_price(update, ctx)
        return

    # 3. Hisse bulma isteği
    if _is_find_request(text):
        text_lower = text.lower()
        market = "NASDAQ" if any(x in text_lower for x in ["nasdaq", "amerikan", "us "]) else "BIST"
        ctx.args = [market.lower()]
        await cmd_find(update, ctx)
        return

    # 4. Strateji sorusu
    if _is_strategy_question(text):
        ctx.args = text.split()
        await cmd_strategy(update, ctx)
        return

    # 5. Gemini ile komut parse
    try:
        parsed = await parse_command(text)
    except Exception:
        parsed = {"command": "unknown"}

    cmd    = parsed.get("command", "unknown")
    ticker = parsed.get("ticker")
    shares = parsed.get("shares")
    price  = parsed.get("price")
    tf     = parsed.get("timeframe", "swing") or "swing"
    market = parsed.get("market", "BIST") or "BIST"
    sector = parsed.get("sector") or "Tümü"
    risk   = parsed.get("risk") or "Orta"
    count  = parsed.get("count") or 5

    if cmd == "buy" and ticker:
        if not shares:
            await update.message.reply_text(
                f"📥 *{ticker}* için kaç adet ve hangi fiyattan alıyorsun?\n"
                f"Örnek: `buy {ticker} 100 45.50`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        await _handle_trade(update, {"action": "buy", "ticker": ticker,
                                      "shares": shares, "price": price})

    elif cmd == "sell" and ticker:
        await _handle_trade(update, {"action": "sell", "ticker": ticker,
                                      "shares": shares, "price": price})

    elif cmd == "price" and ticker:
        ctx.args = [ticker, tf if tf not in ("swing","scalp","position") else "1ay"]
        await cmd_price(update, ctx)

    elif cmd == "analyze" and ticker:
        ctx.args = [ticker, tf]
        await cmd_analyze(update, ctx)

    elif cmd == "deep" and ticker:
        ctx.args = [ticker, tf]
        await cmd_deep(update, ctx)

    elif cmd == "find":
        ctx.args = [market.lower(), tf, sector, risk, str(count)]
        await cmd_find(update, ctx)

    elif cmd == "strategy":
        intent = parsed.get("raw_intent", text)
        ctx.args = intent.split()
        await cmd_strategy(update, ctx)

    elif cmd == "portfolio":
        await cmd_portfolio(update, ctx)

    elif cmd == "scan":
        ctx.args = [market.lower(), tf]
        await cmd_scan(update, ctx)

    elif cmd == "signals":
        await cmd_signals(update, ctx)

    elif cmd == "history":
        await cmd_history(update, ctx)

    elif cmd == "macro":
        await cmd_macro(update, ctx)

    elif cmd == "help":
        await cmd_help(update, ctx)

    elif cmd == "alert" and ticker:
        price_a = parsed.get("price")
        if price_a:
            dir_a = parsed.get("direction", "above") or "above"
            ctx.args = [ticker, str(price_a), dir_a]
            await cmd_alert(update, ctx)
        else:
            await update.message.reply_text(
                f"Uyarı için fiyat belirt:\n`/alert {ticker} FIYAT above`"
            )

    else:
        # Gelişmiş chatbot — bağlam destekli
        # Eğer hisse adı geçiyor soru içinde, bağlam ver
        context_data = None
        suspected_ticker = _detect_ticker_from_text(text)
        if suspected_ticker:
            mkt = _detect_market(suspected_ticker)
            price_val = get_current_price(suspected_ticker, mkt)
            if price_val:
                context_data = {"ticker": suspected_ticker, "price": price_val}

        typing_msg = await update.message.reply_text("💭 Düşünüyorum...", parse_mode=None)
        response   = await advanced_chat(text, user_id=user_id, context_data=context_data)
        try:
            await typing_msg.delete()
        except Exception:
            pass
        await _send(update, response)


async def _handle_trade(update: Update, parsed: dict):
    action = parsed.get("action", "").lower()
    ticker = parsed.get("ticker", "").upper()
    shares = parsed.get("shares")
    price  = parsed.get("price")

    if action == "sell" and str(shares).lower() == "all":
        from portfolio import get_position
        pos = get_position(ticker)
        if pos:
            shares = pos["shares"]
        else:
            await update.message.reply_text(f"❌ {ticker} portföyde bulunamadı.")
            return

    if price is None:
        mkt   = _detect_market(ticker)
        price = get_current_price(ticker, mkt)
        if price is None:
            await update.message.reply_text(
                f"❌ {ticker} için fiyat alınamadı. Manuel gir:\n"
                f"`{action} {ticker} {shares or 100} FIYAT`"
            )
            return

    if shares is None:
        await update.message.reply_text(
            f"Kaç adet {ticker} {action.replace('buy','alıyorsun').replace('sell','satıyorsun')}?\n"
            f"Örnek: `{action} {ticker} 100`"
        )
        return

    try:
        if action == "buy":
            pos = buy(ticker, float(shares), float(price))
            msg = format_buy_confirmation(ticker, float(shares), float(price), pos)
        else:
            result = sell(ticker, float(shares), float(price))
            msg    = format_sell_confirmation(ticker, float(shares), float(price), result)
        await _send(update, msg)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        logger.error(f"Trade hatası: {e}")
        await update.message.reply_text(f"❌ İşlem hatası: {e}")


# ─── UYGULAMA ─────────────────────────────────────────────────────────────────

_bot_app = None

def build_app() -> Application:
    global _bot_app

    if not config.TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN ayarlanmamış!")

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Mevcut komutlar
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("signals",   cmd_signals))
    app.add_handler(CommandHandler("analyze",   cmd_analyze))
    app.add_handler(CommandHandler("deep",      cmd_deep))
    app.add_handler(CommandHandler("scan",      cmd_scan))
    app.add_handler(CommandHandler("briefing",  cmd_briefing))
    app.add_handler(CommandHandler("weekly",    cmd_weekly))
    app.add_handler(CommandHandler("macro",     cmd_macro))
    app.add_handler(CommandHandler("history",   cmd_history))
    app.add_handler(CommandHandler("pnl",       cmd_pnl))
    app.add_handler(CommandHandler("alert",     cmd_alert))
    app.add_handler(CommandHandler("alerts",    cmd_alerts))
    app.add_handler(CommandHandler("watch",     cmd_watch))
    app.add_handler(CommandHandler("unwatch",   cmd_unwatch))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("status",    cmd_status))

    # Yeni komutlar
    app.add_handler(CommandHandler("price",    cmd_price))
    app.add_handler(CommandHandler("find",     cmd_find))
    app.add_handler(CommandHandler("strategy", cmd_strategy))
    app.add_handler(CommandHandler("clear",    cmd_clear))

    # Mesaj handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    _bot_app = app
    return app


async def post_init(app: Application):
    global _scheduler
    try:
        init_gemini()
        logger.info("✅ Gemini başlatıldı")
    except Exception as e:
        logger.error(f"Gemini başlatma: {e}")

    _scheduler = TradingScheduler(notify_fn=_notify)
    _scheduler.start()

    try:
        await app.bot.send_message(
            chat_id=config.ADMIN_CHAT_ID,
            text=(
                "🚀 *ALGO TRADE BOT BAŞLATILDI*\n\n"
                f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                f"🤖 Gemini: `{config.GEMINI_MODEL}` + Google Search\n\n"
                "🆕 *Yeni Özellikler:*\n"
                "💰 `/price THYAO` — Profesyonel fiyat tahmini\n"
                "🔎 `/find bist` — Elite hisse bulucu\n"
                "🧠 `/strategy` — Strateji danışmanı\n"
                "💬 Doğal dil: _'LUMN ne olur?'_\n\n"
                "✅ /help ile tüm komutları gör."
            ),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.warning(f"Başlangıç mesajı: {e}")
