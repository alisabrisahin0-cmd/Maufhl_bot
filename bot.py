"""
MAC ANALIZ BOTU - WINNING CODE & DEEP ANALYSIS EDITION
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta, timezone
import json

# Yapılandırma - Karakter temizliği dahil
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
GEMINI_KEY = os.getenv("GEMINI_KEY", "").strip()
MIN_PUAN = int(os.getenv("MIN_PUAN", "6"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
db_pool = None

API_HEADERS = {
    "x-apisports-key": APISPORTS_KEY,
    "x-apisports-host": "v3.football.api-sports.io"
}
BASE_URL = "https://v3.football.api-sports.io"

# ================================================
# ZAMAN VE AKTİFLİK YÖNETİMİ
# ================================================
def aktif_mi():
    tr_saati = datetime.now(timezone(timedelta(hours=3)))
    saat = tr_saati.hour
    gun = tr_saati.weekday() 
    if gun <= 4: return 19 <= saat <= 23
    else: return 19 <= saat <= 22

# ================================================
# WINNING CODE — SERT FİLTRELEME SİSTEMİ
# ================================================
def winning_code_kontrol(mac):
    """
    KATI KURAL: VU=1, TÜM=1, MA=0, DİYİ=0 olmazsa SİNYAL ÜRETİLMEZ.
    """
    shots_ev = mac.get('shots_on_target_ev', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    son_gol_dk = mac.get('son_gol', 0)
    dakika = mac.get('dakika', 0)
    corner_toplam = mac.get('corner_toplam', 0)

    # VU (Ev Baskısı): 1 olmalı
    VU = 1 if (shots_ev >= 2 and possession_ev >= 42 and dangerous_ev >= 15) else 0

    # TÜM (Genel Aktiflik): 1 olmalı
    TÜM = 1 if (dangerous_ev + dangerous_dep) >= 25 else 0

    # MA (Momentum Kaybı): 0 olmalı (1 ise sinyal iptal)
    son_golden_beri = dakika - son_gol_dk if son_gol_dk > 0 else dakika
    MA = 1 if (son_golden_beri > 8 and (dangerous_ev + dangerous_dep) < 20) else 0

    # DİYİ (Deplasman Direnci/Dengesi): 0 olmalı (1 ise sinyal iptal)
    # Deplasman çok aktifse veya ev sahibi baskıyı kuramadıysa 1 olur.
    DİYİ = 1 if (dangerous_dep > dangerous_ev * 0.65) else 0

    # SERT FİLTRE KONTROLÜ
    gecti = (VU == 1 and TÜM == 1 and MA == 0 and DİYİ == 0)
    
    return {
        'gecti': gecti,
        'VU': VU, 'TÜM': TÜM, 'MA': MA, 'DİYİ': DİYİ,
        'detay': f"VU:{VU} TÜM:{TÜM} MA:{MA} DİYİ:{DİYİ}"
    }

# ================================================
# SİNYAL HESAPLAMA (BONUSLAR DAHİL)
# ================================================
def sinyal_hesapla(mac):
    wc = winning_code_kontrol(mac)
    if not wc['gecti']:
        return 0, [], "Filtreye Takıldı", wc

    puan = 4.0  # Temel puan (Filtreyi geçen her maç 4 ile başlar)
    puan_detay = [f"✅ Winning Code Onayı ({wc['detay']})"]
    strateji = "GENEL_GOL"

    dakika = mac.get('dakika', 0)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    ah = mac.get('ah_deger', 0.0)
    corner = mac.get('corner_toplam', 0)

    # 1. Altın Pencere Bonusları
    if 54 <= dakika <= 60:
        puan += 3.0
        puan_detay.append("🔥 Power Window (54-60') +3")
        strateji = "POWER_WINDOW"
    elif 24 <= dakika <= 36:
        puan += 2.0
        puan_detay.append("⚡ Erken Baskı (24-36') +2")
    elif 45 <= dakika <= 49:
        puan += 2.0
        puan_detay.append("🕒 Uzatma Volatilitesi (45-49') +2")

    # 2. Beraberlik & Skor Bonusu
    if ev_gol == dep_gol:
        puan += 1.5
        puan_detay.append("🤝 Beraberlik Bonusu +1.5")
    
    # 3. Asya Handikap (AH) Entegrasyonu
    if -1.5 <= ah <= -0.75:
        puan += 1.0
        puan_detay.append(f"📊 AH Favori Çizgisi ({ah}) +1")
        if ev_gol < dep_gol: strateji = "VALUE_FAVORI_GERIDE"

    # 4. Korner Eşikleri
    if corner >= 11.5:
        puan += 1.5
        puan_detay.append(f"🚩 Elite Tempo (Korner: {corner}) +1.5")
    elif corner <= 8.0 and (mac['shots_on_target_ev'] + mac['shots_on_target_dep']) < 6:
        puan -= 2.0 # Düşük tempo cezası
        puan_detay.append("⚠️ Düşük Korner/Şut Aktivitesi -2")

    return puan, puan_detay, strateji, wc

# ================================================
# GEMINI AI — DEEP THINKING & ANALİZ
# ================================================
async def gemini_analiz(mac, puan, strateji, wc):
    if not GEMINI_KEY: return "AI Analizi Aktif Değil.", 1.5
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    
    # AI'ya derin düşünme talimatı veriyoruz
    prompt = f"""
    Bir profesyonel bahis stratejistisin. Şu maçı 'Deep Thinking' metodolojisiyle analiz et:
    MAÇ: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}
    DAKİKA: {mac['dakika']} | PUAN: {puan} | STRATEJİ: {strateji}
    DETAY: Şut:{mac['shots_on_target_ev']}/{mac['shots_on_target_dep']}, Korner:{mac['corner_toplam']}, AH:{mac['ah_deger']}
    WINNING CODE: {wc['detay']}

    Sadece veriyi okuma, şunları yorumla:
    1. Momentum taze mi? 
    2. Favori baskısı gole dönüşmek üzere mi?
    3. 'Winning Code' değerleri (VU, TUM, MA, DIYI) maçı nasıl özetliyor?
    
    Kısa, keskin ve aksiyon odaklı bir yorum yaz.
    JSON formatında dön: {{"yorum": "Derin analiz buraya", "kasa": 2.5}}
    """
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=12) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    if "
