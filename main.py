"""
main.py — Algo Trade Bot Ana Giriş Noktası

Başlatmak için:
1. cp .env.example .env
2. .env dosyasını düzenle (API anahtarları)
3. pip install -r requirements.txt
4. python main.py
"""
import asyncio
import logging
import sys
import os

# Proje dizinini path'e ekle
sys.path.insert(0, os.path.dirname(__file__))

# ─── VERİ DİZİNİNİ ÖNCE OLUŞTUR ─────────────────────────────────────────────
# FileHandler'dan ÖNCE yapılmalı, aksi halde FileNotFoundError alınır
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(_DATA_DIR, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(_DATA_DIR, "bot.log"),
            encoding="utf-8"
        )
    ]
)
logger = logging.getLogger(__name__)

from telegram.ext import Application
from bot import build_app, post_init
import config


def validate_config():
    """Kritik konfigürasyonları kontrol et."""
    errors = []
    if not config.TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN ayarlanmamış")
    if not config.GEMINI_API_KEY:
        errors.append("GEMINI_API_KEY ayarlanmamış")
    if not config.ADMIN_CHAT_ID:
        errors.append("ADMIN_CHAT_ID ayarlanmamış")
    if errors:
        for e in errors:
            logger.error(f"❌ {e}")
        logger.error("💡 .env.example dosyasını .env olarak kopyalayıp doldurun!")
        return False
    return True


def main():
    """Ana giriş noktası."""
    logger.info("=" * 60)
    logger.info("🚀 ALGO TRADE BOT başlatılıyor...")
    logger.info("=" * 60)

    # Config kontrolü
    if not validate_config():
        sys.exit(1)

    # Uygulamayı oluştur
    app = build_app()
    app.post_init = post_init

    logger.info("✅ Telegram botu başlatılıyor...")
    logger.info(f"Admin Chat ID: {config.ADMIN_CHAT_ID}")
    logger.info(f"Gemini Modeli: {config.GEMINI_MODEL}")
    logger.info("Çıkmak için Ctrl+C")

    # Polling başlat
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()