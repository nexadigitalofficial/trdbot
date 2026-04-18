"""
scheduler.py — APScheduler ile zamanlanmış görevler
Günlük sabah bülteni, haftalık OSINT tarama, periyodik sinyal tarama
"""
import logging
import asyncio
from datetime import datetime
from typing import Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import (
    DAILY_REPORT_TIME, WEEKLY_SCAN_DAY, WEEKLY_SCAN_TIME,
    SIGNAL_CHECK_INTERVAL, BIST_WATCHLIST, NASDAQ_WATCHLIST,
    ADMIN_CHAT_ID, BIST_MARKET, NASDAQ_MARKET
)

logger = logging.getLogger(__name__)


class TradingScheduler:
    """
    Zamanlanmış görevleri yöneten sınıf.
    notify_fn: Telegram mesajı göndermek için callback
    """

    def __init__(self, notify_fn: Callable):
        self.scheduler  = AsyncIOScheduler(timezone="Europe/Istanbul")
        self.notify_fn  = notify_fn   # async fn(chat_id, text)
        self._running   = False

    def start(self):
        """Scheduler'ı başlat ve görevleri ekle."""
        self._add_daily_briefing()
        self._add_weekly_scan()
        self._add_signal_checker()
        self._add_portfolio_monitor()
        self.scheduler.start()
        self._running = True
        logger.info("✅ Scheduler başlatıldı")

    def stop(self):
        """Scheduler'ı durdur."""
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False

    # ─── GÖREV EKLEYİCİLER ───────────────────────────────────────────────────

    def _add_daily_briefing(self):
        """Günlük sabah bülteni (haftanın her günü)."""
        hour, minute = DAILY_REPORT_TIME.split(":")
        self.scheduler.add_job(
            self._daily_briefing_job,
            CronTrigger(
                day_of_week="mon-fri",
                hour=int(hour),
                minute=int(minute),
                timezone="Europe/Istanbul"
            ),
            id="daily_briefing",
            name="Günlük Sabah Bülteni",
            replace_existing=True
        )
        logger.info(f"Günlük bülten: hafta içi {DAILY_REPORT_TIME}")

    def _add_weekly_scan(self):
        """Haftalık OSINT hisse tarama."""
        hour, minute = WEEKLY_SCAN_TIME.split(":")
        day_map = {
            "monday": "mon", "tuesday": "tue", "wednesday": "wed",
            "thursday": "thu", "friday": "fri",
            "pazartesi": "mon", "sali": "tue", "carsamba": "wed",
            "persembe": "thu", "cuma": "fri"
        }
        day = day_map.get(WEEKLY_SCAN_DAY.lower(), "mon")
        self.scheduler.add_job(
            self._weekly_scan_job,
            CronTrigger(
                day_of_week=day,
                hour=int(hour),
                minute=int(minute),
                timezone="Europe/Istanbul"
            ),
            id="weekly_scan",
            name="Haftalık OSINT Tarama",
            replace_existing=True
        )
        logger.info(f"Haftalık tarama: {WEEKLY_SCAN_DAY} {WEEKLY_SCAN_TIME}")

    def _add_signal_checker(self):
        """Periyodik sinyal kontrol."""
        self.scheduler.add_job(
            self._signal_check_job,
            "interval",
            minutes=SIGNAL_CHECK_INTERVAL,
            id="signal_checker",
            name="Sinyal Kontrolü",
            replace_existing=True
        )
        logger.info(f"Sinyal kontrolü: her {SIGNAL_CHECK_INTERVAL} dakikada")

    def _add_portfolio_monitor(self):
        """Portföy fiyat uyarısı kontrolü (her 15 dk, piyasa saatlerinde)."""
        self.scheduler.add_job(
            self._alert_check_job,
            CronTrigger(
                day_of_week="mon-fri",
                hour="9-18",
                minute="*/15",
                timezone="Europe/Istanbul"
            ),
            id="alert_checker",
            name="Fiyat Uyarısı",
            replace_existing=True
        )

    # ─── GÖREV FONKSİYONLARI ─────────────────────────────────────────────────

    async def _daily_briefing_job(self):
        """Sabah bülteni görevi."""
        logger.info("🌅 Günlük bülten oluşturuluyor...")
        try:
            from portfolio import get_all_positions
            from market_data import get_portfolio_prices, get_macro_data
            from osint_scanner import get_market_news
            from gemini_engine import generate_daily_briefing

            positions  = get_all_positions()
            port_data  = get_portfolio_prices(positions) if positions else {}
            macro      = get_macro_data()
            news       = get_market_news("BIST", limit=8) + get_market_news("NASDAQ", limit=5)

            briefing = await generate_daily_briefing(port_data, macro, news)

            msg = f"🌅 *SABAH MARKET BÜLTENİ*\n_{datetime.now().strftime('%d.%m.%Y %H:%M')}_\n\n{briefing}"
            await self.notify_fn(ADMIN_CHAT_ID, msg)
            logger.info("✅ Günlük bülten gönderildi")
        except Exception as e:
            logger.error(f"Günlük bülten hatası: {e}")
            await self.notify_fn(ADMIN_CHAT_ID, f"❌ Günlük bülten hatası: {e}")

    async def _weekly_scan_job(self):
        """Haftalık OSINT tarama görevi."""
        logger.info("🔭 Haftalık OSINT tarama başlıyor...")
        try:
            from market_data import get_movers, get_macro_data
            from osint_scanner import get_market_news
            from gemini_engine import generate_weekly_scan

            await self.notify_fn(ADMIN_CHAT_ID,
                "🔭 *Haftalık OSINT tarama başladı...* Bu ~2-3 dakika sürebilir.")

            bist_movers   = get_movers(BIST_WATCHLIST[:25], "BIST", top_n=15)
            nasdaq_movers = get_movers(NASDAQ_WATCHLIST[:25], "NASDAQ", top_n=15)
            macro         = get_macro_data()
            news          = get_market_news("BIST", 6) + get_market_news("NASDAQ", 6)

            report = await generate_weekly_scan(
                bist_movers, nasdaq_movers, macro, news
            )

            header = (
                f"🔭 *HAFTALIK OSINT TARAMA RAPORU*\n"
                f"_{datetime.now().strftime('%d.%m.%Y')}_\n\n"
            )
            await self.notify_fn(ADMIN_CHAT_ID, header + report)
            logger.info("✅ Haftalık tarama gönderildi")
        except Exception as e:
            logger.error(f"Haftalık tarama hatası: {e}")
            await self.notify_fn(ADMIN_CHAT_ID, f"❌ Haftalık tarama hatası: {e}")

    async def _signal_check_job(self):
        """Portföy sinyal kontrol görevi."""
        try:
            from portfolio import get_all_positions
            from market_data import get_portfolio_prices
            from osint_scanner import get_stock_news
            from gemini_engine import scan_portfolio_signals

            positions = get_all_positions()
            if not positions:
                return

            port_data = get_portfolio_prices(positions)
            signals   = await scan_portfolio_signals(port_data)

            # Sadece güçlü sinyalleri bildir
            strong = [
                s for s in signals
                if abs(s.get("nsg", 0)) >= 6 or s.get("signal") in ("BUY", "SELL")
            ]

            for sig in strong:
                ticker = sig["ticker"]
                price  = sig.get("price", 0)
                signal = sig.get("signal", "HOLD")
                nsg    = sig.get("nsg", 0)
                reason = sig.get("reason", "")
                sc     = sig.get("signal_class", "")
                pnl    = sig.get("pnl_pct", 0)

                emoji = "🚀" if signal == "BUY" else "🩸" if signal == "SELL" else "⚖️"
                msg   = (
                    f"{emoji} *SİNYAL: {ticker}*\n"
                    f"Fiyat: `{price:.2f}` | NSG: `{nsg:+.1f}`\n"
                    f"Durum: {sc}\n"
                    f"PnL: `{pnl:+.1f}%`\n"
                    f"📝 {reason}"
                )
                await self.notify_fn(ADMIN_CHAT_ID, msg)

        except Exception as e:
            logger.debug(f"Sinyal kontrol hatası: {e}")

    async def _alert_check_job(self):
        """Fiyat uyarısı kontrol görevi."""
        try:
            from portfolio import get_alerts, mark_alert_triggered
            from market_data import check_price_alerts

            alerts    = get_alerts()
            triggered = check_price_alerts(alerts)

            for t in triggered:
                ticker    = t["ticker"]
                price     = t["price"]
                target    = t["target"]
                direction = t["direction"]

                arrow = "📈" if direction == "above" else "📉"
                msg   = (
                    f"{arrow} *FİYAT UYARISI: {ticker}*\n"
                    f"Hedef: `{target:.2f}` | Mevcut: `{price:.2f}`\n"
                    f"Yön: {'Üstüne çıktı' if direction == 'above' else 'Altına düştü'}"
                )
                mark_alert_triggered(ticker)
                await self.notify_fn(ADMIN_CHAT_ID, msg)

        except Exception as e:
            logger.debug(f"Alert kontrol hatası: {e}")

    # ─── MANUEL TETIKLEME ─────────────────────────────────────────────────────

    async def trigger_daily_briefing(self):
        """Manuel sabah bülteni tetikle."""
        await self._daily_briefing_job()

    async def trigger_weekly_scan(self):
        """Manuel haftalık tarama tetikle."""
        await self._weekly_scan_job()

    async def trigger_signal_check(self):
        """Manuel sinyal kontrolü tetikle."""
        await self._signal_check_job()
