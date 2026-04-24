import asyncio
import aiohttp
from telegram import Bot
from telegram.constants import ParseMode
import logging
import os
import asyncpg
from datetime import datetime, timedelta

# ================= CONFIG =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "7"))
# ==========================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db_pool = None
bildirim_gonderilen = {}
biten_maclar = {}


# ================= DB =================
async def db_baglant():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

    await db_pool.execute("""
        CREATE TABLE IF NOT EXISTS sinyaller (
            id SERIAL PRIMARY KEY,
            mac_id TEXT,
            ev TEXT,
            dep TEXT,
            lig TEXT,
            dakika INT,
            ev_gol INT,
            dep_gol INT,
            puan INT,
            tahmin TEXT,
            bildirim_zamani TIMESTAMP DEFAULT NOW(),
            sonuc TEXT DEFAULT 'BEKLIYOR',
            final_ev_gol INT DEFAULT 0,
            final_dep_gol INT DEFAULT 0
        )
    """)


# ================= MATCH FETCH =================
async def macları_cek():
    url = "https://api-football-v1.p.rapidapi.com/v3/fixtures?live=all"
    headers = {
        "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
        "x-rapidapi-key": RAPIDAPI_KEY
    }

    maclar = []

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=15) as resp:
            if resp.status != 200:
                return []

            data = await resp.json()

            for f in data.get("response", []):
                fixture = f.get("fixture", {})
                teams = f.get("teams", {})
                goals = f.get("goals", {})
                league = f.get("league", {})

                maclar.append({
                    "id": str(fixture.get("id")),
                    "ev": teams["home"]["name"],
                    "dep": teams["away"]["name"],
                    "lig": league.get("name", ""),
                    "dakika": fixture.get("status", {}).get("elapsed", 0),
                    "ev_gol": goals.get("home", 0),
                    "dep_gol": goals.get("away", 0)
                })

    return maclar


# ================= SCORE =================
def sinyal_hesapla(mac):
    puan = 0

    ev = mac["ev_gol"]
    dep = mac["dep_gol"]

    if ev > 0 and dep > 0:
        puan += 1
    if ev + dep >= 3:
        puan += 1
    if abs(ev - dep) >= 2:
        puan += 1

    return puan, []


def tavsiye_uret(mac):
    return "GOL OLACAK (S)"


# ================= RESULT CHECK =================
def sonuc_kontrol(tahmin, b_ev, b_dep, f_ev, f_dep):
    yeni_ev = f_ev - b_ev
    yeni_dep = f_dep - b_dep
    toplam = yeni_ev + yeni_dep

    if tahmin == "GOL OLACAK (S)":
        return "TUTTU" if toplam >= 1 else "DSTU"
    return "BELIRSIZ"


# ================= MAIN LOOP =================
async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()

    logger.info("BOT STARTED")

    while True:
        try:
            maclar = await macları_cek()
            aktif_idler = [m["id"] for m in maclar]

            # Biten maçlar
            for mac_id, bilgi in list(biten_maclar.items()):
                if mac_id not in aktif_idler:

                    sonuc = sonuc_kontrol(
                        bilgi["tahmin"],
                        bilgi["baslangic_ev"],
                        bilgi["baslangic_dep"],
                        bilgi["son_ev"],
                        bilgi["son_dep"]
                    )

                    del biten_maclar[mac_id]

            # Aktif maçlar
            for mac in maclar:
                mac_id = mac["id"]

                puan, sinyaller = sinyal_hesapla(mac)

                if puan >= MIN_PUAN:
                    tahmin = tavsiye_uret(mac)

                    bildirim_gonderilen[mac_id] = {
                        "puan": puan,
                        "tahmin": tahmin,
                        "ev_gol": mac["ev_gol"],
                        "dep_gol": mac["dep_gol"]
                    }

                    biten_maclar[mac_id] = {
                        "ev": mac["ev"],
                        "dep": mac["dep"],
                        "tahmin": tahmin,
                        "baslangic_ev": mac["ev_gol"],
                        "baslangic_dep": mac["dep_gol"],
                        "son_ev": mac["ev_gol"],
                        "son_dep": mac["dep_gol"]
                    }

        except Exception as e:
            logger.error(f"HATA: {e}")

        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(ana_dongu())
