"""
bot.py — Ana Telegram Botu
Tüm komut handler'larını ve mesaj işleyicilerini içerir.
Veri alınamadığında Gemini + Google Search ile analiz yapılır.
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
    generate_weekly_scan, chat,
    fetch_market_data_via_gemini, fetch_news_via_gemini,
    analyze_stock_gemini_only
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


# ─── GÜVENLİK KONTROLÜ ───────────────────────────────────────────────────────

def admin_only(func):
    """Sadece admin kullanabilir."""
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != config.ADMIN_CHAT_ID:
            await update.message.reply_text("❌ Bu bot kişisel kullanım içindir.")
            return
        return await func(update, ctx)
    return wrapper


# ─── YARDIMCI FONKSİYONLAR ───────────────────────────────────────────────────

async def _send(update: Update, text: str, parse_mode: str = ParseMode.MARKDOWN):
    """Uzun mesajı bölerek gönder."""
    parts = split_long_message(text)
    for part in parts:
        try:
            await update.message.reply_text(part, parse_mode=parse_mode)
        except Exception as e:
            logger.warning(f"Mesaj gönderme hatası: {e}")
            try:
                await update.message.reply_text(part, parse_mode=None)
            except Exception:
                pass


async def _notify(chat_id: int, text: str):
    """Proaktif bildirim gönder (scheduler callback)."""
    global _bot_app
    parts = split_long_message(text)
    for part in parts:
        try:
            await _bot_app.bot.send_message(
                chat_id=chat_id, text=part, parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.warning(f"Bildirim hatası: {e}")
            try:
                await _bot_app.bot.send_message(chat_id=chat_id, text=part)
            except Exception:
                pass


def _parse_buy_sell(text: str) -> Optional[dict]:
    """Doğrudan regex ile buy/sell parse et."""
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


async def _get_analysis_data(ticker: str, mkt: str, status_msg) -> tuple[dict, list]:
    """
    Analiz için veri topla.
    yfinance başarısız → Gemini Search fallback.
    Returns: (market_data, news)
    """
    # 1. yfinance ile dene
    data = get_market_data(ticker, mkt)

    if not data:
        # 2. Gemini Search fallback
        try:
            await status_msg.edit_text(
                f"⚠️ yfinance verisi alınamadı. Gemini Search ile araştırılıyor... 🔍",
                parse_mode=None
            )
        except Exception:
            pass
        data = await fetch_market_data_via_gemini(ticker, mkt)

    # Haber çek
    news = get_stock_news(ticker, mkt, limit=8)

    # Haber de boşsa Gemini Search ile dene
    if not news:
        try:
            suffix = " hisse" if mkt == "BIST" else " stock"
            news = await fetch_news_via_gemini(f"{ticker}{suffix} son haberler", limit=6)
        except Exception:
            news = []

    return data, news


# ─── KOMUT HANDLER'LARI ───────────────────────────────────────────────────────

@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "🤖 *ALGO TRADE BOT Aktif!*\n\n"
        "Ben senin kişisel algoritmik trading asistanınım.\n"
        "• BIST & NASDAQ takibi\n"
        "• Gemini AI + Google Search ile derin analiz\n"
        "• Otomatik sinyal & bildirimler\n"
        "• Veri alınamazsa Gemini araştırır!\n\n"
        "Başlamak için /help yaz veya doğal dille konuş.\n"
        "_Örnek: 'THYAO analiz et' veya 'buy THYAO 100 45'_"
    )
    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)


@admin_only
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send(update, HELP_TEXT)


@admin_only
async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Canlı portföy görüntüle."""
    await update.message.reply_text("💼 Portföy yükleniyor...", parse_mode=None)
    positions = get_all_positions()
    if not positions:
        await _send(update, format_portfolio_message({}))
        return
    port_data = get_portfolio_prices(positions)
    msg = format_portfolio_message(port_data)
    await _send(update, msg)


@admin_only
async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Portföy sinyal taraması."""
    positions = get_all_positions()
    if not positions:
        await update.message.reply_text("📭 Portföy boş. Önce hisse ekle.")
        return
    await update.message.reply_text("📡 Sinyaller taranıyor... (Gemini AI)", parse_mode=None)
    port_data = get_portfolio_prices(positions)
    signals   = await scan_portfolio_signals(port_data)
    msg = format_signal_message(signals)
    await _send(update, msg)


@admin_only
async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Hisse analizi: /analyze THYAO [timeframe]
    6-boyutlu OMNI-TRADER analizi.
    Veri alınamazsa Gemini kendi araştırır.
    """
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "Kullanım: /analyze TICKER [scalp|swing|position]\n"
            "Örnek: /analyze THYAO swing"
        )
        return

    ticker    = args[0].upper()
    timeframe = args[1].lower() if len(args) > 1 else "swing"
    mkt       = "BIST" if ticker in config.BIST_WATCHLIST else "NASDAQ"

    status_msg = await update.message.reply_text(
        f"🔍 *{ticker}* analiz ediliyor... (OMNI-TRADER 6D + Gemini Search)",
        parse_mode=ParseMode.MARKDOWN
    )

    data, news = await _get_analysis_data(ticker, mkt, status_msg)

    try:
        await status_msg.delete()
    except Exception:
        pass

    if data:
        # Normal analiz — veri var
        current_price = data.get("current_price", 0)
        source = data.get("_source", "yfinance")
        if source == "gemini_search":
            await update.message.reply_text(
                f"📡 _Veri kaynağı: Gemini Search (yfinance kullanılamadı)_",
                parse_mode=ParseMode.MARKDOWN
            )
        result = await analyze_stock(
            ticker, current_price, data, timeframe, news, deep=False
        )
    else:
        # Gemini-only mod — hiç veri alınamadı
        await update.message.reply_text(
            f"⚠️ _Harici veri alınamadı. Gemini kendi bilgisiyle analiz yapıyor..._",
            parse_mode=ParseMode.MARKDOWN
        )
        result = await analyze_stock_gemini_only(ticker, mkt, timeframe, deep=False)

    await _send(update, result)


@admin_only
async def cmd_deep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    12-boyutlu derin analiz: /deep THYAO
    OMEGA-SINGULARITY protokolü + Gemini Search.
    Veri alınamazsa Gemini kendi araştırır.
    """
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "Kullanım: /deep TICKER\nÖrnek: /deep THYAO"
        )
        return

    ticker    = args[0].upper()
    timeframe = args[1].lower() if len(args) > 1 else "position"
    mkt       = "BIST" if ticker in config.BIST_WATCHLIST else "NASDAQ"

    status_msg = await update.message.reply_text(
        f"🌌 *{ticker}* için OMEGA-SINGULARITY 12D analiz başlatılıyor...\n"
        f"_(Gemini Search ile zenginleştiriliyor)_",
        parse_mode=ParseMode.MARKDOWN
    )

    data, news = await _get_analysis_data(ticker, mkt, status_msg)

    try:
        await status_msg.delete()
    except Exception:
        pass

    if data:
        current_price = data.get("current_price", 0)
        source = data.get("_source", "yfinance")
        if source == "gemini_search":
            await update.message.reply_text(
                f"📡 _Veri kaynağı: Gemini Search (yfinance kullanılamadı)_",
                parse_mode=ParseMode.MARKDOWN
            )
        result = await analyze_stock(
            ticker, current_price, data, timeframe, news, deep=True
        )
    else:
        # Gemini-only mod
        await update.message.reply_text(
            f"⚠️ _Harici veri alınamadı. OMEGA-SINGULARITY kendi araştırmasıyla analiz yapıyor..._",
            parse_mode=ParseMode.MARKDOWN
        )
        result = await analyze_stock_gemini_only(ticker, mkt, timeframe, deep=True)

    await _send(update, result)


@admin_only
async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    OSINT tarama: /scan bist|nasdaq [swing|position]
    Mover verisi alınamazsa Gemini Search ile tarar.
    """
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

    # Candidates az ise Gemini daha kapsamlı araştırır
    if not candidates_data:
        await update.message.reply_text(
            f"⚠️ yfinance mover verisi alınamadı. Gemini Search tüm piyasayı tarayacak...",
            parse_mode=None
        )

    result = await run_osint_scan(market, timeframe, candidates_data)
    header = f"🔭 *{market} OSINT TARAMA — {datetime.now().strftime('%d.%m.%Y')}*\n\n"
    await _send(update, header + result)


@admin_only
async def cmd_briefing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manuel sabah bülteni."""
    await update.message.reply_text("🌅 Sabah bülteni hazırlanıyor... (Gemini Search)")
    if _scheduler:
        await _scheduler.trigger_daily_briefing()
    else:
        await update.message.reply_text("❌ Scheduler başlatılmamış.")


@admin_only
async def cmd_weekly(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manuel haftalık tarama."""
    await update.message.reply_text("🔭 Haftalık tarama başlıyor... (~2-3 dakika)")
    if _scheduler:
        await _scheduler.trigger_weekly_scan()
    else:
        await update.message.reply_text("❌ Scheduler başlatılmamış.")


@admin_only
async def cmd_macro(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Makro piyasa verileri."""
    await update.message.reply_text("📊 Makro veriler yükleniyor...", parse_mode=None)
    macro = get_macro_data()
    msg   = format_macro_message(macro)
    await _send(update, msg)


@admin_only
async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """İşlem geçmişi."""
    history = get_trade_history(limit=15)
    msg     = format_history_message(history)
    await _send(update, msg)


@admin_only
async def cmd_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Gerçekleşen kar/zarar özeti."""
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
    """Fiyat uyarısı: /alert THYAO 50 above"""
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
    """Aktif uyarıları listele."""
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
    """İzleme listesine ekle: /watch THYAO"""
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
    """İzleme listesinden çıkar: /unwatch THYAO"""
    if not ctx.args:
        await update.message.reply_text("Kullanım: /unwatch TICKER")
        return
    ticker = ctx.args[0].upper()
    wl     = remove_watchlist(ticker)
    await update.message.reply_text(
        f"❌ *{ticker}* izleme listesinden çıkarıldı.",
        parse_mode=ParseMode.MARKDOWN
    )


@admin_only
async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """İzleme listesini göster."""
    wl = get_watchlist()
    if not wl:
        await update.message.reply_text("👁 İzleme listesi boş.\n/watch TICKER ile ekle.")
        return

    lines = ["👁 *İZLEME LİSTESİ*\n"]
    for ticker in wl:
        mkt   = "BIST" if ticker in config.BIST_WATCHLIST else "NASDAQ"
        price = get_current_price(ticker, mkt)
        p_txt = f"`{price:.2f}`" if price else "_fiyat yok_"
        lines.append(f"• *{ticker}*: {p_txt}")
    await _send(update, "\n".join(lines))


@admin_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Bot durumu."""
    summary = get_portfolio_summary()
    msg     = (
        f"⚙️ *BOT DURUMU*\n\n"
        f"🟢 Çalışıyor\n"
        f"🤖 Gemini: `{config.GEMINI_MODEL}` + Google Search\n"
        f"💼 Pozisyon: `{summary['total_positions']}`\n"
        f"📊 BIST: `{len(summary['markets']['BIST'])}`\n"
        f"📊 NASDAQ: `{len(summary['markets']['NASDAQ'])}`\n"
        f"🔄 Toplam İşlem: `{summary['total_trades']}`\n"
        f"🕐 Son Güncelleme: _{summary.get('last_updated','N/A')[:16]}_\n\n"
        f"⏰ Günlük Bülten: `{config.DAILY_REPORT_TIME}`\n"
        f"🔭 Haftalık Tarama: `{config.WEEKLY_SCAN_DAY} {config.WEEKLY_SCAN_TIME}`\n"
        f"📡 Sinyal Kontrolü: `{config.SIGNAL_CHECK_INTERVAL} dk`\n\n"
        f"🔍 _Veri alınamazsa Gemini Search devreye girer._"
    )
    await _send(update, msg)


# ─── MESAJ HANDLER (DOĞAL DİL) ────────────────────────────────────────────────

@admin_only
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Doğal dil mesajlarını işle."""
    text = update.message.text.strip()

    # 1. Hızlı regex parse (buy/sell)
    direct = _parse_buy_sell(text)
    if direct:
        await _handle_trade(update, direct)
        return

    # 2. Gemini ile komut parse
    try:
        parsed = await parse_command(text)
    except Exception:
        parsed = {"command": "unknown"}

    cmd    = parsed.get("command", "unknown")
    ticker = parsed.get("ticker")
    shares = parsed.get("shares")
    price  = parsed.get("price")
    tf     = parsed.get("timeframe", "swing") or "swing"

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

    elif cmd == "analyze" and ticker:
        ctx.args = [ticker, tf]
        await cmd_analyze(update, ctx)

    elif cmd == "portfolio":
        await cmd_portfolio(update, ctx)

    elif cmd == "scan":
        market = parsed.get("market", "BIST") or "BIST"
        ctx.args = [market.lower(), tf]
        await cmd_scan(update, ctx)

    elif cmd == "signals":
        await cmd_signals(update, ctx)

    elif cmd == "history":
        await cmd_history(update, ctx)

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
        # Serbest sohbet modu — Gemini + Google Search
        typing_msg = await update.message.reply_text("💭 Gemini araştırıyor...", parse_mode=None)
        response   = await chat(text)
        try:
            await typing_msg.delete()
        except Exception:
            pass
        await _send(update, response)


async def _handle_trade(update: Update, parsed: dict):
    """Alım/satım işlemini gerçekleştir."""
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

    # Fiyat belirtilmemişse canlı fiyat çek (Gemini fallback ile)
    if price is None:
        mkt   = "BIST" if ticker in config.BIST_WATCHLIST else "NASDAQ"
        price = get_current_price(ticker, mkt)
        if price is None:
            await update.message.reply_text(
                f"❌ {ticker} için fiyat alınamadı. Fiyatı manuel gir:\n"
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

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    _bot_app = app
    return app


async def post_init(app: Application):
    """Bot başlatıldığında çalışır."""
    global _scheduler
    try:
        init_gemini()
        logger.info("✅ Gemini başlatıldı")
    except Exception as e:
        logger.error(f"Gemini başlatma hatası: {e}")

    _scheduler = TradingScheduler(notify_fn=_notify)
    _scheduler.start()

    try:
        await app.bot.send_message(
            chat_id=config.ADMIN_CHAT_ID,
            text=(
                "🚀 *ALGO TRADE BOT BAŞLATILDI*\n\n"
                f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                f"🤖 Gemini: `{config.GEMINI_MODEL}` + Google Search\n"
                f"⏰ Günlük Bülten: `{config.DAILY_REPORT_TIME}`\n"
                f"🔭 Haftalık Tarama: `{config.WEEKLY_SCAN_DAY}`\n\n"
                "✅ Veri alınamazsa Gemini Search devreye girer.\n"
                "Hazırım! /help ile komutları gör."
            ),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.warning(f"Başlangıç mesajı gönderilemedi: {e}")
