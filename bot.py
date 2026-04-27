"""
MAC ANALIZ BOTU - OPTIMIZE SISTEM
Zamanlama:
- Hafta ici: 19:00 - 00:00 (Pazartesi-Cuma)
- Hafta sonu: 19:00 - 23:00 (Cumartesi-Pazar)
- Diger saatler: Uyku (API hak tasarrufu)

API Optimizasyonu:
- Odds sadece sinyal bulunan maclara sorulur
- Corner verisini ana endpoint'ten alir (ayri istek yok)

Winning Code Sistemi:
- VU/TUM/MA/DIYI hard filter
- Altin pencere bonuslari
- Beraberlik bonusu
- Cooling Off korumasi
- Gemini AI derin analiz
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta
import json

# Ortam değişkenlerini alıp, gizli \n karakterlerini temizliyoruz (strip)
# Bu kısım hatayı düzelten kritik kısımdır.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
GEMINI_KEY = os.getenv("GEMINI_KEY", "").strip()
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
# ZAMAN YÖNETİMİ — OPTİMİZE
# ================================================
def aktif_mi():
    """
    Hafta ici (0=Pzt, 4=Cuma): 19:00 - 00:00
    Hafta sonu (5=Cmt, 6=Pzr): 19:00 - 23:00
    """
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday()  # 0=Pazartesi, 6=Pazar

    hafta_ici = gun <= 4  # Pzt-Cuma
    hafta_sonu = gun >= 5  # Cmt-Pzr

    if hafta_ici:
        return 19 <= saat or saat < 0  # 19:00 - 00:00
    elif hafta_sonu:
        return 19 <= saat <= 22  # 19:00 - 23:00

    return False


def kontrol_suresi_al():
    """Aktif saatlerde 7 dakika, uyku modunda 30 dakika"""
    if aktif_mi():
        return 420  # 7 dakika
    return None  # Uyku


def sonraki_aktif_saat():
    simdi = datetime.now()
    gun = simdi.weekday()
    saat = simdi.hour

    if gun <= 4:
        return "19:00 (Hafta ici)"
    else:
        return "19:00 (Hafta sonu)"


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
# WINNING CODE — VU/TUM/MA/DİYİ HARD FILTER
# ================================================
def winning_code_kontrol(mac):
    """
    VU=1: Ev sahibi yeterli baski yapıyor
    TUM=1: Mac aktif, her iki taraf hucumda
    MA=0: Momentum durmus degil (taze)
    DIYI=0: Deplasman savunmada kalmis
    
    Hepsi gecmezse sinyal URETILMEZ.
    """
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    son_gol = mac.get('son_gol', 0)
    dakika = mac.get('dakika', 0)
    corner_ev = mac.get('corner_ev', 0)
    corner_dep = mac.get('corner_dep', 0)

    # VU=1: Ev sahibi aktif baskida
    VU = (
        shots_ev >= 2 and
        possession_ev >= 42 and
        dangerous_ev >= 15
    )

    # TUM=1: Mac genel olarak aktif
    TUM = (dangerous_ev + dangerous_dep) >= 25

    # MA=0: Momentum hala devam ediyor
    if son_gol > 0:
        son_golden_beri = dakika - son_gol
        toplam_atak = dangerous_ev + dangerous_dep
        MA = not (son_golden_beri > 8 and toplam_atak < 20)
    else:
        MA = not (dakika > 15 and dangerous_ev < 8)

    # DIYI=0: Deplasman savunmaya cekilmis
    DIYI = (
        dangerous_dep <= dangerous_ev * 0.65 and
        shots_dep <= shots_ev + 3
    )

    return {
        'VU': VU,
        'TUM': TUM,
        'MA': MA,
        'DIYI': DIYI,
        'gecti': VU and TUM and MA and DIYI,
        'detay': f"VU:{1 if VU else 0} TUM:{1 if TUM else 0} MA:{0 if not MA else 1} DIYI:{0 if DIYI else 1}"
    }


# ================================================
# ALTIN PENCERE BONUSLARI
# ================================================
def zaman_bonusu_hesapla(dakika):
    if 54 <= dakika <= 60:
        return 3, "POWER WINDOW 54-60dk"
    elif 24 <= dakika <= 36:
        return 2, "ERKEN BASKISI 24-36dk"
    elif 45 <= dakika <= 49:
        return 2, "UZATMA 45-49dk"
    elif 7 <= dakika <= 15:
        return 1, "ERKEN ACILIS 7-15dk"
    return 0, ""


# ================================================
# COOLING OFF KORUMASI
# ================================================
def cooling_off_kontrol(mac):
    dakika = mac.get('dakika', 0)
    son_gol = mac.get('son_gol', 0)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    corner_ev = mac.get('corner_ev', 0)
    corner_dep = mac.get('corner_dep', 0)
    gol_fark = abs(mac.get('ev_gol', 0) - mac.get('dep_gol', 0))
    dangerous_toplam = dangerous_ev + dangerous_dep
    corner_toplam = corner_ev + corner_dep

    # Skor 3+ fark + gec dakika + dusuk aktivite
    if gol_fark >= 3 and dakika >= 62 and dangerous_toplam < 20:
        return True, f"Skor netlesmis ({mac['ev_gol']}-{mac['dep_gol']}) + gec donem + dusuk atak"

    # Son golden cok zaman gecti + aktivite dusuk
    if son_gol > 0:
        gecen =
