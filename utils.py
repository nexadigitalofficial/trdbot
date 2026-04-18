"""
utils.py — Telegram mesaj biçimlendirme ve yardımcı araçlar
"""
from datetime import datetime
from typing import Optional
from config import MAX_MESSAGE_LENGTH


def format_portfolio_message(portfolio_data: dict) -> str:
    """Portföy durumunu güzel formatlı mesaj olarak döndür."""
    if not portfolio_data:
        return "📭 *Portföy boş.*\n\nHisse eklemek için:\n`buy THYAO 100 45.50`"

    bist_pos   = {k: v for k, v in portfolio_data.items() if v.get("market") == "BIST"}
    nasdaq_pos = {k: v for k, v in portfolio_data.items() if v.get("market") in ("NASDAQ", "NYSE")}

    lines = ["💼 *PORTFÖY DURUMU*\n"]

    total_pnl_try = 0
    total_pnl_usd = 0

    if bist_pos:
        lines.append("🇹🇷 *BIST*")
        for ticker, data in bist_pos.items():
            lines.append(_format_position_line(ticker, data))
            pnl = data.get("pnl_abs", 0) or 0
            total_pnl_try += pnl
        lines.append("")

    if nasdaq_pos:
        lines.append("🇺🇸 *NASDAQ/NYSE*")
        for ticker, data in nasdaq_pos.items():
            lines.append(_format_position_line(ticker, data))
            pnl = data.get("pnl_abs", 0) or 0
            total_pnl_usd += pnl
        lines.append("")

    # Toplam
    lines.append("─────────────────")
    if total_pnl_try != 0:
        emoji = "✅" if total_pnl_try >= 0 else "🔴"
        lines.append(f"{emoji} BIST Toplam PnL: `{total_pnl_try:+.2f} ₺`")
    if total_pnl_usd != 0:
        emoji = "✅" if total_pnl_usd >= 0 else "🔴"
        lines.append(f"{emoji} NASDAQ Toplam PnL: `{total_pnl_usd:+.2f} $`")

    lines.append(f"\n🕐 _{datetime.now().strftime('%d.%m.%Y %H:%M')}_")
    return "\n".join(lines)


def _format_position_line(ticker: str, data: dict) -> str:
    """Tek pozisyon satırını formatla."""
    current = data.get("current_price")
    avg     = data.get("avg_price", 0)
    shares  = data.get("shares", 0)
    pnl_pct = data.get("pnl_pct")
    pnl_abs = data.get("pnl_abs")
    change  = data.get("price_change_pct", 0)
    rsi     = data.get("rsi")

    if current is None:
        return f"  • *{ticker}* — fiyat alınamadı"

    pnl_emoji = ""
    if pnl_pct is not None:
        pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
    
    day_emoji = "↗️" if change > 0 else ("↘️" if change < 0 else "➡️")

    line = (
        f"  • *{ticker}*: `{current:.2f}` {day_emoji}`{change:+.1f}%`\n"
        f"    Maliyet: `{avg:.2f}` | Adet: `{shares:.0f}`\n"
        f"    {pnl_emoji} PnL: `{pnl_pct:+.1f}%` (`{pnl_abs:+.2f}`)"
    )
    if rsi:
        line += f" | RSI: `{rsi:.0f}`"
    return line


def format_signal_message(signals: list) -> str:
    """Sinyal listesini formatla."""
    if not signals:
        return "📊 *Portföyünüzde güçlü sinyal yok.*\nSonraki kontrol otomatik yapılacak."

    lines = ["📡 *PORTFÖY SİNYALLERİ*\n"]
    for sig in signals:
        ticker  = sig.get("ticker", "")
        signal  = sig.get("signal", "HOLD")
        nsg     = sig.get("nsg", 0)
        sc      = sig.get("signal_class", "")
        reason  = sig.get("reason", "")
        price   = sig.get("price", 0)
        pnl     = sig.get("pnl_pct", 0)
        conf    = sig.get("confidence", 0)
        entry   = sig.get("entry")
        stop    = sig.get("stop")
        target  = sig.get("target")

        emoji = "🚀" if signal == "BUY" else ("🩸" if signal == "SELL" else "⚖️")
        lines.append(
            f"{emoji} *{ticker}* | `{price:.2f}` | NSG: `{nsg:+.1f}`\n"
            f"   {sc} | Güven: `{conf}%` | PnL: `{pnl:+.1f}%`\n"
            f"   📝 _{reason}_"
        )
        if entry and stop and target:
            lines.append(
                f"   Giriş: `{entry}` | Stop: `{stop}` | Hedef: `{target}`"
            )
        lines.append("")

    lines.append(f"🕐 _{datetime.now().strftime('%d.%m.%Y %H:%M')}_")
    return "\n".join(lines)


def format_buy_confirmation(ticker: str, shares: float, price: float,
                             position: dict) -> str:
    """Alım onayı mesajı."""
    total_cost = round(shares * price, 2)
    currency   = position.get("currency", "USD")
    market     = position.get("market", "")
    avg        = position.get("avg_price", price)
    total_s    = position.get("shares", shares)
    sym        = "₺" if currency == "TRY" else "$"
    flag       = "🇹🇷" if market == "BIST" else "🇺🇸"

    return (
        f"✅ {flag} *ALIM GERÇEKLEŞTİ*\n\n"
        f"📌 *{ticker}*\n"
        f"Alınan: `{shares:.0f}` adet @ `{price:.4f}` {sym}\n"
        f"İşlem Tutarı: `{total_cost:.2f}` {sym}\n"
        f"─────────────\n"
        f"Toplam Pozisyon: `{total_s:.0f}` adet\n"
        f"Ortalama Maliyet: `{avg:.4f}` {sym}\n"
        f"Toplam Değer: `{round(total_s * avg, 2):.2f}` {sym}\n"
        f"\n🕐 _{datetime.now().strftime('%d.%m.%Y %H:%M')}_"
    )


def format_sell_confirmation(ticker: str, shares: float, price: float,
                              result: dict) -> str:
    """Satış onayı mesajı."""
    pnl     = result.get("pnl", 0)
    pnl_pct = result.get("pnl_pct", 0)
    rem     = result.get("remaining", 0)
    status  = result.get("status", "")

    pnl_emoji = "✅" if pnl >= 0 else "🔴"
    closed_txt = "🏁 *Pozisyon Kapatıldı*" if status == "closed" else f"Kalan: `{rem:.0f}` adet"

    return (
        f"💸 *SATIM GERÇEKLEŞTİ*\n\n"
        f"📌 *{ticker}*\n"
        f"Satılan: `{shares:.0f}` adet @ `{price:.4f}`\n"
        f"─────────────\n"
        f"{pnl_emoji} Gerçekleşen PnL: `{pnl:+.2f}` (`{pnl_pct:+.2f}%`)\n"
        f"{closed_txt}\n"
        f"\n🕐 _{datetime.now().strftime('%d.%m.%Y %H:%M')}_"
    )


def format_history_message(history: list) -> str:
    """İşlem geçmişini formatla."""
    if not history:
        return "📋 *İşlem geçmişi boş.*"

    lines = ["📋 *SON İŞLEMLER*\n"]
    for t in reversed(history[-15:]):
        action  = t.get("action", "")
        ticker  = t.get("ticker", "")
        shares  = t.get("shares", 0)
        price   = t.get("price", 0)
        ts      = t.get("timestamp", "")[:16].replace("T", " ")
        pnl_pct = t.get("pnl_pct")

        emoji = "🟢" if action == "BUY" else "🔴"
        line  = f"{emoji} `{ts}` | *{ticker}* | {action} | {shares:.0f}x @ {price:.2f}"
        if pnl_pct is not None:
            line += f" | PnL: `{pnl_pct:+.1f}%`"
        lines.append(line)

    return "\n".join(lines)


def format_macro_message(macro: dict) -> str:
    """Makro veri mesajı."""
    if not macro:
        return "📊 Makro veri alınamadı."

    lines = ["🌍 *MAKRO PİYASA GÖSTERGELERİ*\n"]
    icons = {
        "SP500": "🇺🇸", "BIST100": "🇹🇷", "DXY": "💵",
        "USD_TRY": "💱", "ALTIN": "🥇", "VIX": "😨", "BTC": "₿"
    }
    for k, v in macro.items():
        icon = icons.get(k, "•")
        lines.append(f"  {icon} *{k}*: `{v}`")
    lines.append(f"\n🕐 _{datetime.now().strftime('%d.%m.%Y %H:%M')}_")
    return "\n".join(lines)


def split_long_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list:
    """Uzun mesajı parçalara böl."""
    if len(text) <= limit:
        return [text]
    parts  = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        split = text.rfind("\n", 0, limit)
        if split == -1:
            split = limit
        parts.append(text[:split])
        text = text[split:].lstrip("\n")
    return parts


def escape_md(text: str) -> str:
    """Telegram MarkdownV2 için escape et."""
    chars = r"_*[]()~`>#+-=|{}.!"
    for c in chars:
        text = text.replace(c, f"\\{c}")
    return text


HELP_TEXT = """🤖 *ALGO TRADE BOT — KOMUT REHBERİ*

━━━━━━━━━━━━━━━━━━━━━

📥 *ALIM/SATIM*
`buy THYAO 100 45.50` — 100 adet THYAO al, 45.50₺'den
`buy AAPL 5 180` — 5 adet AAPL al, 180$'dan
`sell THYAO 50 50` — 50 adet THYAO sat, 50₺'den
`sell AAPL all` — Tüm AAPL pozisyonunu kapat

📊 *ANALİZ*
`/analyze THYAO` — Hızlı 6-boyutlu analiz
`/deep THYAO` — 12-boyutlu OMEGA derin analiz
`/analyze AAPL swing` — Swing analizi

💼 *PORTFÖY*
`/portfolio` — Portföy durumu (canlı fiyat)
`/signals` — Portföy sinyal taraması
`/history` — Son 15 işlem
`/pnl` — Gerçekleşen kar/zarar

📡 *TARAMA*
`/scan bist` — BIST OSINT taraması
`/scan nasdaq` — NASDAQ OSINT taraması
`/weekly` — Haftalık tarama (manuel)
`/briefing` — Sabah bülteni (manuel)

🔔 *UYARILAR*
`/alert THYAO 50 above` — THYAO 50₺'nin üstüne çıkınca uyar
`/alert AAPL 170 below` — AAPL 170$'ın altına düşünce uyar
`/alerts` — Aktif uyarıları listele

👁 *İZLEME LİSTESİ*
`/watch SASA` — İzleme listesine ekle
`/unwatch SASA` — Listeden çıkar
`/watchlist` — İzleme listesi

📈 *PİYASA*
`/macro` — Makro göstergeler (BIST, SP500, DXY, TL...)

━━━━━━━━━━━━━━━━━━━━━
ℹ️ Doğal dil de kullanabilirsin:
_"THYAO'yu analiz et"_
_"Portföyümde sinyal var mı?"_
_"Bu hafta BIST'te ne alayım?"_"""
