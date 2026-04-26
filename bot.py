"""
MAC ANALIZ BOTU - FINAL STABLE
- Syntax hataları giderildi
- Puanlama sistemi optimize edildi
- AI entegrasyonu stabil hale getirildi
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime
import json

# Ortam Değişkenleri
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
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
# VERITABANI ISLEMLERI
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
                bildirim_zamani TIMESTAMP DEFAULT NOW()
            )
        """)
        logger.info("Veritabani hazir.")
    except Exception as e:
        logger.error(f"DB Hatasi: {e}")

async def sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa_yuzde):
    if not db_pool: return
    try:
        await db_pool.execute("""
            INSERT INTO sinyaller (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol, puan, strateji, tahmin, ai_yorum, kasa_yuzde)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """, mac['id'], mac['ev'], mac['dep'], mac['lig'], mac['dakika'], mac['ev_gol'], mac['dep_gol'], puan, strateji, tahmin, ai_yorum, kasa_yuzde)
    except Exception as e:
        logger.error(f"Kayit Hatasi: {e}")

# ================================================
# MANTIK VE ANALIZ
# ================================================
def kontrol_suresi_al():
    # Gun icinde calisma periyodu
    return 300 # Her 5 dakikada bir kontrol et

def zaman_bonusu_hesapla(dakika):
    if 54 <= dakika <= 60: return 3, "Altın Pencere"
    if 75 <= dakika <= 82: return 2, "Son Baskı"
    return 0, ""

def sinyal_hesapla(mac):
    puan = 0
    puan_detay = []
    stratejiler = []

    dk = mac['dakika']
    eg = mac['ev_gol']
    dg = mac['dep_gol']
    toplam_gol = eg + dg
    gol_fark = abs(eg - dg)
    esit_skor = (eg == dg)
    shots_toplam = mac['shots_on_target_ev'] + mac['shots_on_target_dep']

    # Puanlama
    if toplam_gol >= 3:
        puan += 2
        stratejiler.append("GOL_POTANSIYELI")
    
    if esit_skor:
        puan += 2
        puan_detay.append("Beraberlik Dengesi")
        stratejiler.append("BERABERLIK")

    if shots_toplam >= 10:
        puan += 2
        stratejiler.append("HUCUM_MACI")

    if mac['kirmizi_kart'] > 0:
        puan += 1
        stratejiler.append("KIRMIZI_KART")

    bonus, label = zaman_bonusu_hesapla(dk)
    if bonus > 0:
        puan += bonus
        puan_detay.append(label)

    strat_adi = stratejiler[0] if stratejiler else "GENEL_ANALIZ"
    return puan, puan_detay, strat_adi

def tavsiye_uret(mac, strateji):
    eg, dg = mac['ev_gol'], mac['dep_gol']
    if strateji == "BERABERLIK":
        return "GOL OLACAK (S)", "Beraberlik bozulma eğiliminde."
    if eg > dg:
        return "EV GOL ATACAK (S)", "Ev sahibi baskısını sürdürüyor."
    return "GOL OLACAK (S)", "Maç temposu yüksek."

# ================================================
# AI VE DIS SERVISLER
# ================================================
async def gemini_analiz(mac, puan, tahmin):
    if not GEMINI_KEY: return "AI Devre Dışı", 1.5
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    
    prompt = f"Maç: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']} (Dakika: {mac['dakika']}). Tahmin: {tahmin}. Bu maçı 1 cümleyle yorumla ve % kaç kasa girilmeli söyle. JSON: {{\"yorum\": \"...\", \"kasa\": 1.5}}"
    
    try:
        async with aiohttp.ClientSession() as session:
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    if "
