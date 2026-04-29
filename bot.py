"""
MAC ANALIZ BOTU - GÜNCELLENMİŞ SİSTEM
- Asian Handicap Entegrasyonu İyileştirildi
- Nesine Bildirim Notu Eklendi
- API Verimliliği Artırıldı
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
# ZAMAN YÖNETİMİ
# ================================================
def aktif_mi():
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday()
    if gun <= 4:
        return 19 <= saat <= 23
    else:
        return 19 <= saat <= 22
    return False

def sonraki_aktif():
    gun = datetime.now().weekday()
    return "19:00 (Hafta ici)" if gun <= 4 else "19:00 (Hafta sonu)"


# ================================================
# VERİTABANI
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
                puan REAL,
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
        logger.error(f"DB: {e}")

async def sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa):
    try:
        if db_pool:
            await db_pool.execute("""
                INSERT INTO sinyaller
                (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol,
                 puan, strateji, tahmin, ai_yorum, kasa_yuzde)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """, mac['id'], mac['ev'], mac['dep'], mac['lig'],
                mac['dakika'], mac['ev_gol'], mac['dep_gol'],
                puan, strateji, tahmin, ai_yorum, kasa)
    except Exception as e:
        logger.error(f"Kayit: {e}")

async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        if db_pool:
            await db_pool.execute("""
                UPDATE sinyaller SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3
                WHERE mac_id=$4 AND sonuc='BEKLIYOR'
            """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e:
        logger.error(f"Guncelleme: {e}")


# ================================================
# WINNING CODE
# ================================================
def winning_code_kontrol(mac):
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    son_gol = mac.get('son_gol', 0)
    dakika = mac.get('dakika', 0)

    VU = shots_ev >= 2 and possession_ev >= 42 and dangerous_ev >= 15
    TUM = (dangerous_ev + dangerous_dep) >= 25
    if son_gol > 0:
        gecen = dakika - son_gol
        MA = not (gecen > 8 and (dangerous_ev + dangerous_dep) < 20)
    else:
        MA = not (dakika > 15 and dangerous_ev < 8)
    DIYI = dangerous_dep <= dangerous_ev * 0.65 and shots_dep <= shots_ev + 3

    return {
        'VU': VU, 'TUM': TUM, 'MA': MA, 'DIYI': DIYI,
        'gecti': VU and TUM and MA and DIYI,
        'VU_val': 1 if VU else 0, 'TUM_val': 1 if TUM else 0
