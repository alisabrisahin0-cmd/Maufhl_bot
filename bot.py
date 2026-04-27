"""
MAC ANALIZ BOTU - OPTIMIZE SISTEM (GÜNCEL VERSİYON)
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta, timezone
import json

# Yapılandırma
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
# ZAMAN YÖNETİMİ — TÜRKİYE SAATİNE GÖRE (GMT+3)
# ================================================
def aktif_mi():
    # Sunucu saati ne olursa olsun Türkiye saatini (UTC+3) baz alalım
    tr_saati = datetime.now(timezone(timedelta(hours=3)))
    saat = tr_saati.hour
    gun = tr_saati.weekday()  # 0=Pzt, 6=Pzr

    if gun <= 4:  # Hafta içi
        # 19:00 - 23:59 arası aktif
        return 19 <= saat <= 23
    else:  # Hafta sonu
        # 19:00 - 22:59 arası aktif
        return 19 <= saat <= 22

def kontrol_suresi_al():
    if aktif_mi():
        return 420  # 7 dakika
    return None  # Uyku

def sonraki_aktif_saat():
    return "19:00 (TR Saati)"

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
        # Eksik kolon kontrolü
        for kolon, tip in [("ai_yorum", "TEXT"), ("kasa_yuzde", "REAL"), ("strateji", "TEXT")]:
            try:
                await db_pool.execute(f"ALTER TABLE sinyaller ADD COLUMN IF NOT EXISTS {kolon} {tip}")
            except: pass
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
        logger.error(f"Kayit hatası: {e}")

async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        if db_pool:
            await db_pool.execute("""
                UPDATE sinyaller SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3
                WHERE mac_id=$4 AND sonuc='BEKLIYOR'
            """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e:
        logger.error(f"Guncelleme hatası: {e}")

# ================================================
# ANALİZ VE FİLTRELEME SİSTEMİ
# ================================================
def winning_code_kontrol(mac):
    shots_ev = mac.get('shots_on_target_ev', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    dakika = mac.get('dakika', 0)
    son_gol = mac.get('son_gol', 0)

    VU = (shots_ev >= 2 and possession_ev >= 42 and dangerous_ev >= 15)
    TUM = (dangerous_ev + dangerous_dep) >= 25
    
    if son_gol > 0:
        son_golden_beri = dakika - son_gol
        MA = not (son_golden_beri >
