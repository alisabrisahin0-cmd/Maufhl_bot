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
                tahmin TEXT,
                bildirim_zamani TIMESTAMP DEFAULT NOW()
            )
        """)
        logger.info("✅ Veritabanı bağlandı!")
    except Exception as e:
        logger.error(f"DB hatası: {e}")

async def macları_cek():
    url = "https://v3.football.api-sports.io/fixtures?live=all"
    headers = {"x-apisports-key": RAPIDAPI_KEY}
    maclar = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    fixtures = data.get('response', [])
                    for f in fixtures:
                        try:
                            fixture = f.get('fixture', {})
                            teams = f.get('teams', {})
                            goals = f.get('goals', {})
                            dakika = int(fixture.get('status', {}).get('elapsed', 0) or 0)
                            if dakika < 5 or dakika > 85: continue
                            maclar.append({
                                'id': str(fixture.get('id', '')),
                                'ev': teams.get('home', {}).get('name', '?'),
                                'dep': teams.get('away', {}).get('name', '?'),
                                'lig': f.get('league', {}).get('name', '?'),
                                'dakika': dakika,
                                'ev_gol': int(goals.get('home', 0) or 0),
                                'dep_gol': int(goals.get('away', 0) or 0)
                            })
                        except: continue
                else: logger.error(f"API Hata: {resp.status}")
    except Exception as e: logger.error(f"Bağlantı hatası: {e}")
    return maclar

def sinyal_hesapla(mac):
    puan = 0
    toplam_gol = mac['ev_gol'] + mac['dep_gol']
    if toplam_gol >= 2: puan += 4
    if mac['dakika'] >= 30: puan += 3
    return puan

async def bildirim_gonder(bot, mac, puan):
    tahmin = "GOL OLACAK (S)"
    mesaj = f"⚽ *{mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}*\n⏱ {mac['dakika']}. dk\n📊 Puan: {puan}\n💡 *Tahmin: {tahmin}*"
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode=ParseMode.MARKDOWN)
        if db_pool:
            await db_pool.execute("INSERT INTO sinyaller (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol, puan, tahmin) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                mac['id'], mac['ev'], mac['dep'], mac['lig'], mac['dakika'], mac['ev_gol'], mac['dep_gol'], puan, tahmin)
    except Exception as e: logger.error(f"Bildirim hatası: {e}")

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()
    while True:
        try:
            sure, mod = kontrol_suresi_al()
            if sure is None:
                await asyncio.sleep(600)
                continue
            maclar = await macları_cek()
            for mac in maclar:
                puan = sinyal_hesapla(mac)
                if puan >= MIN_PUAN and mac['id'] not in bildirim_gonderilen:
                    await bildirim_gonder(bot, mac, puan)
                    bildirim_gonderilen[mac['id']] = puan
            await asyncio.sleep(sure or 300)
        except Exception as e:
            logger.error(f"Hata: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
