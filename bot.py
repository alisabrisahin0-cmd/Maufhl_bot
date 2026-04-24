import asyncio
import aiohttp
from telegram import Bot
from telegram.constants import ParseMode
import logging
import os
import asyncpg
from datetime import datetime, timedelta

# =============================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "7"))
# =============================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
db_pool = None


def kontrol_suresi_al():
    saat = datetime.now().hour
    dakika = datetime.now().minute

    if saat < 11 or (saat == 11 and dakika < 30):
        return None, "uyku"
    elif (saat == 11 and dakika >= 30) or (12 <= saat < 15):
        return 480, "öğle"
    elif 15 <= saat < 19:
        return 420, "erken_aksam"
    elif 19 <= saat < 23:
        return 360, "ana_program"
    else:
        return None, "uyku"


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
            dakika INTEGER,
            ev_gol INTEGER,
            dep_gol INTEGER,
            puan INTEGER,
            tahmin TEXT,
            bildirim_zamani TIMESTAMP DEFAULT NOW(),
            sonuc TEXT DEFAULT 'BEKLIYOR',
            final_ev_gol INTEGER DEFAULT 0,
            final_dep_gol INTEGER DEFAULT 0
        )
    """)


async def sinyal_kaydet(mac, puan, tahmin):
    if db_pool:
        await db_pool.execute("""
            INSERT INTO sinyaller 
            (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol, puan, tahmin)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        """, mac['id'], mac['ev'], mac['dep'], mac['lig'],
            mac['dakika'], mac['ev_gol'], mac['dep_gol'], puan, tahmin)


async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    if db_pool:
        await db_pool.execute("""
            UPDATE sinyaller 
            SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3
            WHERE mac_id=$4 AND sonuc='BEKLIYOR'
        """, sonuc, final_ev, final_dep, mac_id)


def sonuc_kontrol(tahmin, baslangic_ev, baslangic_dep, final_ev, final_dep):
    yeni_ev = final_ev - baslangic_ev
    yeni_dep = final_dep - baslangic_dep
    yeni_toplam = yeni_ev + yeni_dep

    if tahmin == "GOL OLACAK (S)":
        return "TUTTU" if yeni_toplam >= 1 else "DSTU"
    elif tahmin == "EV GOL ATACAK (S)":
        return "TUTTU" if yeni_ev >= 1 else "DSTU"
    elif tahmin == "DEP GOL ATACAK (S)":
        return "TUTTU" if yeni_dep >= 1 else "DSTU"
    return "BELIRSIZ"


def sinyal_hesapla(mac):
    puan = 0
    dakika = mac.get('dakika', 0)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)

    toplam = ev_gol + dep_gol
    gol_fark = abs(ev_gol - dep_gol)

    if ev_gol > 0 and dep_gol > 0:
        puan += 1
    if toplam >= 3:
        puan += 1
    if gol_fark >= 2:
        puan += 1

    return puan, []


def tavsiye_uret(mac):
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)

    if ev_gol + dep_gol >= 4:
        return "GOL OLACAK (S)"
    return "GOL OLACAK (S)"


async def macları_cek():
    url = "https://api-football-v1.p.rapidapi.com/v3/fixtures?live=all"
    headers = {
        "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
        "x-rapidapi-key": RAPIDAPI_KEY
    }

    maclar = []

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                return []

            data = await resp.json()

            for f in data.get('response', []):
                fixture = f.get('fixture', {})
                teams = f.get('teams', {})
                goals = f.get('goals', {})
                league = f.get('league', {})

                maclar.append({
                    'id': str(fixture.get('id')),
                    'ev': teams['home']['name'],
                    'dep': teams['away']['name'],
                    'lig': league.get('name', ''),
                    'dakika': fixture.get('status', {}).get('elapsed', 0),
                    'ev_gol': goals.get('home', 0),
                    'dep_gol': goals.get('away', 0)
                })

    return maclar


async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()

    while True:
        try:
            maclar = await macları_cek()
            aktif_idler = [m['id'] for m in maclar]

            for mac_id, bilgi in list(biten_maclar.items()):
                if mac_id not in aktif_idler:

                    sonuc = sonuc_kontrol(
                        bilgi['tahmin'],
                        bilgi['baslangic_ev'],
                        bilgi['baslangic_dep'],
                        bilgi['son_ev'],
                        bilgi['son_dep']
                    )

                    del biten_maclar[mac_id]

            for mac in maclar:
                puan, sinyaller = sinyal_hesapla(mac)
                mac_id = mac['id']

                if puan >= MIN_PUAN:
                    tahmin = tavsiye_uret(mac)

                    bildirim_gonderilen[mac_id] = {
                        'puan': puan,
                        'tahmin': tahmin,
                        'ev_gol': mac['ev_gol'],
                        'dep_gol': mac['dep_gol']
                    }

                    biten_maclar[mac_id] = {
                        'ev': mac['ev'],
                        'dep': mac['dep'],
                        'tahmin': tahmin,
                        'baslangic_ev': mac['ev_gol'],
                        'baslangic_dep': mac['dep_gol'],
                        'son_ev': mac['ev_gol'],
                        'son_dep': mac['dep_gol'],
                    }

        except Exception as e:
            logger.error(e)

        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(ana_dongu())
