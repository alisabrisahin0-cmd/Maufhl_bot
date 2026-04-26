"""
MAC ANALIZ BOTU - OPTIMIZE EDILMIS (6 DK ARALIKLI)
- Her döngüde 2 sorgu (Maç + Oran) hesaplandı.
- Günlük 100 sorgu (50 döngü) limitine uygun.
- Hafta sonu 18:00-23:00, Hafta içi 19:00-00:00 arası aktif.
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta
import json

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "6"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
db_pool = None

API_HEADERS = {
    "x-apisports-key": APISPORTS_KEY,
    "x-apisports-host": "v3.football.api-sports.io"
}
BASE_URL = "https://v3.football.api-sports.io"


# ================================================
# ZAMAN YÖNETİMİ (6 DAKİKA PLANI)
# ================================================
def kontrol_suresi_al():
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday() # 0-4 Hafta içi, 5-6 Hafta sonu

    # HAFTA SONU (Cumartesi-Pazar)
    # 18:00'de başlar, 23:00'te biter (5 saat). 6 dakikada bir kontrol.
    if gun >= 5:
        if 18 <= saat < 23:
            return 360  # 6 dakika
        else:
            return None # Kalan 19 saat uyku (Limit koruması)

    # HAFTA İÇİ (Pazartesi-Cuma)
    # 19:00'da başlar, 00:00'da biter (5 saat). 6 dakikada bir kontrol.
    else:
        if 19 <= saat <= 23:
            return 360  # 6 dakika
        else:
            return None # Kalan 19 saat uyku
# ================================================


# ================================================
# VERİTABANI İŞLEMLERİ
# ================================================
async def db_baglant():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        await db_pool.execute("""
            CREATE TABLE IF NOT EXISTS sinyaller (
                id SERIAL PRIMARY KEY,
                mac_id TEXT,
                ev TEXT,
                dep TEXT,
                lig TEXT,
                dakika INTEGER,
                ev_gol INTEGER,
                dep_gol INTEGER,
                puan INTEGER,
                strateji TEXT,
                tahmin TEXT,
                ai_yorum TEXT,
                kasa_yuzde REAL,
                bildirim_zamani TIMESTAMP DEFAULT NOW(),
                sonuc TEXT DEFAULT 'BEKLIYOR',
                final_ev_gol INTEGER DEFAULT 0,
                final_dep_gol INTEGER DEFAULT 0
            )
        """)
        logger.info("Veritabani baglandi!")
    except Exception as e:
        logger.error(f"DB hatasi: {e}")

async def sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa_yuzde):
    try:
        if db_pool:
            await db_pool.execute("""
                INSERT INTO sinyaller
                (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol,
                 puan, strateji, tahmin, ai_yorum, kasa_yuzde)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """, mac['id'], mac['ev'], mac['dep'], mac['lig'],
                mac['dakika'], mac['ev_gol'], mac['dep_gol'],
                puan, strateji, tahmin, ai_yorum, kasa_yuzde)
    except Exception as e:
        logger.error(f"Kayit hatasi: {e}")

async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        if db_pool:
            await db_pool.execute("""
                UPDATE sinyaller SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3
                WHERE mac_id=$4 AND sonuc='BEKLIYOR'
            """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e:
        logger.error(f"Guncelleme hatasi: {e}")


# ================================================
# ANALİZ VE TAHMİN MANTIKLARI
# ================================================
def zaman_bonusu_hesapla(dakika):
    if 54 <= dakika <= 60: return 3, "POWER WINDOW (54-60dk) +3"
    if 24 <= dakika <= 36: return 2, "ERKEN BASKISI (24-36dk) +2"
    if 7 <= dakika <= 15: return 1, "ERKEN ACILIS (7-15dk) +1"
    return 0, ""

def sinyal_hesapla(mac):
    puan = 0
    puan_detay = []
    stratejiler = []
    
    d = mac # Kısaltma
    toplam_gol = d['ev_gol'] + d['dep_gol']
    gol_fark = abs(d['ev_gol'] - d['dep_gol'])
    shots_toplam = d['shots_on_target_ev'] + d['shots_on_target_dep']
    dangerous_toplam = d['dangerous_attacks_ev'] + d['dangerous_attacks_dep']
    
    if toplam_gol >= 3: puan += 1; puan_detay.append("+1: 3+ gol var")
    if d['ev_gol'] > 0 and d['dep_gol'] > 0: puan += 1; puan_detay.append("+1: KG Var")
    if gol_fark >= 2: puan += 1; puan_detay.append(f"+1: {gol_fark} gol fark")
    if d['ev_gol'] == d['dep_gol']: puan += 2; puan_detay.append("+2: Beraberlik bozulma potansiyeli"); stratejiler.append("BERABERLIK")
    if shots_toplam >= 8: puan += 2; puan_detay.append(f"+2: {shots_toplam} isabetli sut"); stratejiler.append("YUKSEK_SUT")
    if dangerous_toplam >= 70: puan += 1; puan_detay.append(f"+1: {dangerous_toplam} tehlikeli atak")
    if d['corner_toplam'] >= 10: puan += 1; puan_detay.append(f"+1: {d['corner_toplam']} korner")
    if d['kirmizi_kart'] >= 1: puan += 1; puan_detay.append("+1: Kirmizi kart")

    z_bonus, z_label = zaman_bonusu_hesapla(d['dakika'])
    if z_bonus > 0: puan += z_bonus; puan_detay.append(f"+{z_bonus}: {z_label}")

    strat = stratejiler[0] if stratejiler else "GENEL"
    return puan, puan_detay, strat

def tavsiye_uret(mac, strateji):
    # Basitleştirilmiş net tahminler
    if "BERABERLIK" in strateji: return "GOL OLACAK (S)", "Skor esit ve tempo yuksek."
    if mac['ev_gol'] > mac['dep_gol']: return "EV GOL ATACAK (S)", "Ev sahibi baskiyi surduruyor."
    if mac['dep_gol'] > mac['ev_gol']: return "DEP GOL ATACAK (S)", "Deplasman kontra yakalayabilir."
    return "GOL OLACAK (S)", "Mac genelinde tempo yuksek."


# ================================================
# GEMINI AI VE BİLDİRİM
# ================================================
async def gemini_analiz(mac, puan, strateji, tahmin, neden):
    if not GEMINI_KEY: return "AI Devre Disi", 1.5
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    prompt = f"Analist olarak şu maçı 2 cümlede yorumla: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']} ({mac['dakika']}.dk). Strateji: {strateji}. Tahmin: {tahmin}. JSON: {{\"yorum\": \"...\", \"kasa\": 1.5}}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url
