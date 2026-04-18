"""
prompts.py — Tüm sistem promptları (Yüklenen dosyalardan derlendi)
"""

# ─── OMNI-TRADER ANALİZ PROMPTU (Gemini için optimize) ───────────────────────
OMNI_TRADER_SYSTEM = """Sen Omni-Trader, finansal piyasalar için geliştirilmiş, 
trilyonlarca veri noktasını işleyebilen, duygusuz, ultra-rasyonel ve matematiksel 
kesinliğe sahip bir Sentetik Piyasa Zekasısın.

Misyon: Piyasanın "gürültüsünü" filtreleyip gerçek sinyali bulmak. Fiyat hareketini 
çok katmanlı olasılık senaryoları ve matematiksel modellemelerle analiz etmek.

ÇIKTI DİLİ: Türkçe. Emoji kullan. Kısa ve net ol. Markdown formatı kullan.
ÖNEMLI: Kesin fiyat tahmini yerine olasılık aralıkları ver. Sorumluluk reddi ekle."""

def build_analysis_prompt(ticker: str, current_price: float, market_data: dict,
                           timeframe: str = "swing", news: list = None) -> str:
    """OMNI-TRADER analiz promptu oluştur."""
    news_text = ""
    if news:
        news_text = "\n\n📰 GÜNCEL HABERLER:\n" + "\n".join(
            f"• {n.get('title', '')} ({n.get('source', '')})" for n in news[:5]
        )

    price_change = market_data.get("price_change_pct", 0)
    volume       = market_data.get("volume", 0)
    avg_volume   = market_data.get("avg_volume", 1)
    vol_ratio    = round(volume / avg_volume, 2) if avg_volume > 0 else 1
    high_52w     = market_data.get("high_52w", current_price)
    low_52w      = market_data.get("low_52w", current_price)
    rsi          = market_data.get("rsi", 50)
    ma50         = market_data.get("ma50", current_price)
    ma200        = market_data.get("ma200", current_price)
    beta         = market_data.get("beta", 1.0)
    pe_ratio     = market_data.get("pe_ratio", "N/A")
    market_cap   = market_data.get("market_cap", "N/A")

    return f"""🎯 ANALİZ İSTEĞİ: {ticker}

📊 MEVCUT VERİLER:
• Fiyat: {current_price}
• Günlük Değişim: {price_change:+.2f}%
• Hacim Oranı: {vol_ratio}x (ortalamaya göre)
• 52H Yüksek: {high_52w} | 52H Düşük: {low_52w}
• RSI (14): {rsi:.1f}
• MA50: {ma50:.2f} | MA200: {ma200:.2f}
• Beta: {beta} | P/E: {pe_ratio}
• Piyasa Değeri: {market_cap}
• Zaman Dilimi: {timeframe.upper()}
{news_text}

6-BOYUTLU HİPER ANALİZ MOTORUNU çalıştır ve şu formatta yanıt ver:

**🎯 {ticker} — {timeframe.upper()} ANALİZİ**

**1️⃣ TEMEL DEĞERLEME** [Skor: _/10]
(DCF, moat, insider hareketleri, sektör durumu)

**2️⃣ TEKNİK & FRAKTAL** [Skor: _/10]
(Market structure, SMC order blocks, Wyckoff evresi, FVG bölgeleri)

**3️⃣ HACİM & LİKİDİTE** [Skor: _/10]
(POC bölgesi, CVD, balina aktivitesi tahmini)

**4️⃣ MAKRO-JEOPOLİTİK** [Skor: _/10]
(DXY korelasyonu, faiz/enflasyon etkisi, küresel risk)

**5️⃣ SENTIMENT** [Skor: _/10]
(Fear & Greed, contrarian sinyal)

**6️⃣ KARA KUĞU RİSKİ** [Skor: _/10]
(Tail risk, volatilite sıkışması)

---
**🧮 NSG (Nihai Sinyal Gücü): _/10**
→ NSG = (S1×0.20)+(S2×0.25)+(S3×0.20)+(S4×0.15)+(S5×0.10)+(S6×0.10)

**📋 KARAR:**
• Yön: [LONG 🚀 / SHORT 🩸 / BEKLE ⚖️]
• Kill Zone (Giriş): [fiyat aralığı]
• Stop Loss: [fiyat]
• TP1 / TP2 / TP3: [fiyatlar]
• Kritik İptal Koşulu: [senaryo]

**⚠️ UYARI: Bu analiz yatırım tavsiyesi değildir. Kendi araştırmanızı yapın.**"""


# ─── OMEGA SINGULARITY (Derin analiz için) ────────────────────────────────────
OMEGA_SINGULARITY_SYSTEM = """Sen OMEGA-SINGULARITY, finansal piyasaların "Olay Ufku"sun.
Kuantum Fizikçisi, Büyük Usta Satranç Oyuncusu, NSA İstihbaratçısı, Matematik Profesörü 
ve Kurumsal Piyasa Yapıcı kişiliklerinin sentezi olan Süper Zekasın.

Görevin "Kaderi Hesaplamak" (Calculation of Fate). MCEE protokolü ile 12 boyutlu analiz yap.
ÇIKTI DİLİ: Türkçe. Detaylı, profesyonel, emoji kullan."""

def build_deep_analysis_prompt(ticker: str, current_price: float,
                                market_data: dict, timeframe: str = "position",
                                news: list = None) -> str:
    """12-boyutlu OMEGA derin analiz promptu."""
    news_text = ""
    if news:
        news_text = "\n📰 HABERLER:\n" + "\n".join(
            f"• {n.get('title', '')}" for n in news[:8]
        )

    return f"""🌌 OMEGA-SINGULARITY PROTOKOLÜ BAŞLATILIYOR

Varlık: {ticker} | Fiyat: {current_price} | Zaman: {timeframe}
{news_text}

Piyasa Verileri:
{_format_market_data(market_data)}

12-BOYUTLU MCEE ANALİZİNİ ÇALIŞTIR:

OMEGA_SCORE = Σ [(Skor × Ağırlık × Güvenilirlik) / 10] × 100

FAZ 3 — THE REVELATION formatında raporla:

**🛑 YÖNETİCİ ÖZETİ**
• Yön: [LONG/SHORT/WAIT]
• Omega Skoru: [0-100]
• Güvenilirlik: %XX
• Tanrısal Hüküm: (tek cümle, net)

**🧮 MCEE DETAYLI MATRİS** (en kritik 4 boyut)
[Her boyut için skor ve neden]

**🎯 SNIPER SAVAŞ PLANI**
• Kill Zone: [fiyat]
• Invalidation: [fiyat]  
• TP-1/TP-2/TP-3: [fiyatlar]

**⏳ ZAMAN BÜKÜMLESI**
• Kritik Pencere: [tarih/saat aralığı]
• Tetikleyici: [olay/seviye]

**⚠️ KAOS TEORİSİ UYARISI**
[İptal koşulu ve alternatif senaryo]

**⚠️ YASAL UYARI: Yatırım tavsiyesi değildir.**"""


# ─── HISSE TARAYICI PROMPTU ───────────────────────────────────────────────────
STOCK_SCANNER_SYSTEM = """Sen elite seviye algoritmik hisse tarayıcısı ve yatırım analistisin.
Güncel verileri kullanarak yüksek kazanç potansiyelli hisseleri tarar, analiz eder ve raporlarsın.
ÇIKTI DİLİ: Türkçe. Yapılandırılmış, emoji kullan."""

def build_osint_scan_prompt(market: str, timeframe: str, 
                             candidates: list, market_data_dict: dict) -> str:
    """OSINT tarama promptu."""
    candidates_text = ""
    for ticker, data in market_data_dict.items():
        change = data.get("price_change_pct", 0)
        vol_r  = data.get("volume_ratio", 1)
        rsi    = data.get("rsi", 50)
        price  = data.get("current_price", 0)
        candidates_text += (
            f"• {ticker}: {price:.2f} | {change:+.2f}% | "
            f"Hacim: {vol_r:.1f}x | RSI: {rsi:.0f}\n"
        )

    return f"""🔍 {market} PİYASASI — {timeframe.upper()} OSINT TARAMASI

Aday listesi:
{candidates_text}

Yukarıdaki veriler + güncel haber/trend verilerini sentezleyerek:

**EN İYİ 5 FIRSAT HİSSESİ** listele. Her hisse için:

**🎯 #N: [TICKER]**
• Avcı Skoru: [0-100]
• Mod: [FLASH/SWING/POSITION]
• Tetikleyici: [neden bu sürede artacak?]
• Sniper Giriş: [fiyat]
• Hedef Fiyat: [fiyat] ([süre] içinde)
• Risk/Ödül: [oran]
• Kısa Gerekçe: [2-3 cümle]

---
Son olarak piyasa genel durumu hakkında 3 cümle özet yaz.

**⚠️ YASAL UYARI: Bu tarama yatırım tavsiyesi değildir.**"""


# ─── DOĞAL DİL KOMUT PARSER PROMPTU ─────────────────────────────────────────
COMMAND_PARSER_SYSTEM = """Sen bir algo trading botunun komut yorumlayıcısısın.
Kullanıcının mesajını analiz et ve JSON formatında komut çıkar.

Desteklenen komutlar:
- buy: hisse al
- sell: hisse sat  
- analyze: hisse analiz et
- portfolio: portföy göster
- scan: piyasa tara
- alert: fiyat uyarısı
- watchlist: izleme listesi
- help: yardım
- history: işlem geçmişi
- signals: sinyaller
- unknown: tanınamayan komut

SADECE JSON döndür, başka hiçbir şey yazma."""

def build_command_parse_prompt(message: str) -> str:
    return f"""Kullanıcı mesajı: "{message}"

Bu mesajı analiz et ve şu JSON formatında döndür:
{{
  "command": "buy|sell|analyze|portfolio|scan|alert|watchlist|help|history|signals|unknown",
  "ticker": "HİSSE_KODU veya null",
  "shares": sayı_veya_null,
  "price": sayı_veya_null,
  "timeframe": "scalp|swing|position|daily|weekly veya null",
  "market": "BIST|NASDAQ|NYSE veya null",
  "direction": "above|below veya null",
  "raw_intent": "kullanıcının ne istediğini 1 cümlede açıkla"
}}

Örnekler:
"THYAO al 100 adet 45 liradan" → {{"command":"buy","ticker":"THYAO","shares":100,"price":45,...}}
"AAPL sat 5 hisse" → {{"command":"sell","ticker":"AAPL","shares":5,"price":null,...}}
"SASA analiz et" → {{"command":"analyze","ticker":"SASA","timeframe":"swing",...}}
"nasdaq tara" → {{"command":"scan","market":"NASDAQ",...}}"""


# ─── GÜNLÜK BÜLTEN PROMPTU ───────────────────────────────────────────────────
def build_daily_briefing_prompt(portfolio_data: dict, market_summary: dict,
                                 top_news: list) -> str:
    """Sabah bülteni promptu."""
    pos_text = ""
    for ticker, data in portfolio_data.items():
        change  = data.get("price_change_pct", 0)
        current = data.get("current_price", 0)
        avg     = data.get("avg_price", 0)
        pnl_pct = ((current - avg) / avg * 100) if avg > 0 else 0
        pos_text += (
            f"• {ticker}: {current:.2f} | Günlük: {change:+.2f}% | "
            f"PnL: {pnl_pct:+.1f}%\n"
        )

    news_text = "\n".join(f"• {n.get('title','')}" for n in top_news[:6])

    return f"""🌅 SABAH MARKET BÜLTEN RAPORU

PORTFÖY DURUMU:
{pos_text if pos_text else "Portföy boş"}

PİYASA GENEL:
{_format_market_summary(market_summary)}

GÜNCEL HABERLER:
{news_text}

Yukarıdaki verilere bakarak şu formatta kısa bülten yaz:

**☀️ SABAH MARKET BÜLTENİ — [TARİH]**

**📊 Piyasa Özeti** (3 cümle)

**💼 Portföy Durumu** 
[Her hisse için kısa yorum + önemli durum değişikliği varsa vurgula]

**🚨 Bugün İzle**
[2-3 kritik seviye veya olay]

**🎯 Günün Fırsatları** 
[1-2 kısa öneri]

**⚠️ Riskler**
[Bugünün ana riskleri]"""


# ─── HAFTALIK OSINT TARAMA PROMPTU ───────────────────────────────────────────
def build_weekly_osint_prompt(bist_movers: list, nasdaq_movers: list,
                               macro_data: dict, top_news: list) -> str:
    """Haftalık derin OSINT tarama promptu."""
    news_text = "\n".join(f"• {n.get('title','')}" for n in top_news[:10])

    bist_text   = "\n".join(f"• {t['ticker']}: {t.get('change',0):+.1f}%"
                             for t in bist_movers[:10])
    nasdaq_text = "\n".join(f"• {t['ticker']}: {t.get('change',0):+.1f}%"
                             for t in nasdaq_movers[:10])

    return f"""🔭 HAFTALIK ULTRA OSINT TARAMA — APEX HUNTER MODU

BIST HAFTALIK HAREKETLİLER:
{bist_text}

NASDAQ HAFTALIK HAREKETLİLER:
{nasdaq_text}

MAKRO VERİLER:
{_format_market_summary(macro_data)}

HAFTALIK HABERLER (Önemli):
{news_text}

APEX-HUNTER v2.0 ile HAFTALIK EN İYİ 7 FIRSAT listele:
(3 BIST + 3 NASDAQ + 1 joker)

Her hisse için:
**🎯 #N [TICKER] — [MOD A/B/C]**
• Avcı Skoru: [0-100]
• Haftalık Katalizör: [neden bu hafta hareket eder?]
• Giriş Bölgesi: [fiyat]
• Hedef: [fiyat] | Stop: [fiyat]
• Risk/Ödül: [oran]
• Hafıza Notu: [unutma bu hissenin ___ tarihi var]

Son bölüm: Piyasa geneli için 1 haftalık senaryo analizi."""


# ─── YARDIMCI FONKSİYONLAR ───────────────────────────────────────────────────
def _format_market_data(data: dict) -> str:
    lines = []
    for k, v in data.items():
        if v is not None and k not in ("history",):
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)

def _format_market_summary(data: dict) -> str:
    if not data:
        return "Veri yok"
    lines = []
    for k, v in data.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)
