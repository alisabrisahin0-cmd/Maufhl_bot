"""
MAC ANALIZ BOTU - DUZELTILMIS SISTEM
- Net tahmin ve aciklama
- Gercek AI analiz (deep thinking)
- Puan aciklamali
- WC filtresi esnek
- Corner + Odds + Gemini
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
def kontrol_suresi_al():
    saat = datetime.now().hour
    dk = datetime.now().minute
    if saat < 11 or (saat == 11 and dk < 30):
        return None
    elif (saat == 11 and dk >= 30) or (12 <= saat < 15):
        return 480
    elif 15 <= saat < 19:
        return 420
    elif 19 <= saat < 23:
        return 360
    else:
        return None


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
        for kolon, tip in [
            ("ai_yorum", "TEXT"), ("kasa_yuzde", "REAL"), ("strateji", "TEXT")
        ]:
            try:
                await db_pool.execute(
                    f"ALTER TABLE sinyaller ADD COLUMN IF NOT EXISTS {kolon} {tip}"
                )
            except:
                pass
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
# COOLING OFF
# ================================================
def cooling_off_kontrol(mac):
    dakika = mac.get('dakika', 0)
    gol_fark = abs(mac.get('ev_gol', 0) - mac.get('dep_gol', 0))
    dangerous_toplam = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)

    if gol_fark >= 3 and dakika >= 65 and dangerous_toplam < 20:
        return True, f"Skor {mac['ev_gol']}-{mac['dep_gol']} netlesmis + dusuk atak"

    return False, ""


# ================================================
# ALTIN PENCERE
# ================================================
def zaman_bonusu_hesapla(dakika):
    if 54 <= dakika <= 60:
        return 3, "POWER WINDOW (54-60dk) +3"
    elif 24 <= dakika <= 36:
        return 2, "ERKEN BASKISI (24-36dk) +2"
    elif 45 <= dakika <= 49:
        return 2, "UZATMA (45-49dk) +2"
    elif 7 <= dakika <= 15:
        return 1, "ERKEN ACILIS (7-15dk) +1"
    return 0, ""


# ================================================
# SİNYAL SİSTEMİ
# ================================================
def sinyal_hesapla(mac):
    puan = 0
    puan_detay = []
    stratejiler = []

    dakika = mac.get('dakika', 0)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    son_gol = mac.get('son_gol', 0)
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    possession_dep = mac.get('possession_dep', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    kirmizi = mac.get('kirmizi_kart', 0)
    corner_ev = mac.get('corner_ev', 0)
    corner_dep = mac.get('corner_dep', 0)
    corner_toplam = mac.get('corner_toplam', 0)
    ah_deger = mac.get('ah_deger', 0.0)

    toplam_gol = ev_gol + dep_gol
    gol_fark = abs(ev_gol - dep_gol)
    kg_var = ev_gol > 0 and dep_gol > 0
    esit_skor =
