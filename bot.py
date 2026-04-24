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
    if saat < 11: return None, "uyku"
    elif 11 <= saat < 19: return 420, "gündüz"
    elif 19 <= saat < 23: return 300, "akşam"
    else: return None, "uyku"

async def db_baglant():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        await db_pool.execute("CREATE TABLE IF NOT EXISTS sinyaller (id SERIAL PRIMARY KEY, mac_id TEXT, ev TEXT, dep TEXT, puan INTEGER, tahmin TEXT, zaman TIMESTAMP DEFAULT NOW())")
        logger.info("✅ Veritabanı bağlandı!")
    except Exception as e: logger.error(f"DB Hatası: {e}")

async def macları_cek():
    url = "https://v3.football.api-sports.io/fixtures?live=all"
    headers = {"x-apisports-key": RAPIDAPI_KEY}
    maclar = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for f in data.get('response', []):
                        try:
                            fix = f.get('fixture', {})
                            goals = f.get('goals', {})
                            dak = int(fix.get('status', {}).get('elapsed', 0) or 0)
                            if 5 < dak < 85:
                                maclar.append({'id': str(fix.get('id')), 'ev': f['teams']['home']['name'], 'dep': f['teams']['away']['name'], 'dakika': dak, 'ev_gol': goals.get('home', 0), 'dep_gol': goals.get('away', 0)})
                        except: continue
    except Exception as e: logger.error(f"API Hatası: {e}")
    return maclar

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()
    logger.info("🚀 Bot çalışmaya başladı...")
    while True:
        try:
            sure, mod = kontrol_suresi_al()
            if sure is None:
                await asyncio.sleep(600)
                continue
            maclar = await macları_cek()
            for mac in maclar:
                puan = 0
                if (mac['ev_gol'] + mac['dep_gol']) >= 1: puan += 4
                if mac['dakika'] >= 30: puan += 4
                
                if puan >= MIN_PUAN and mac['id'] not in bildirim_gonderilen:
                    msg = f"⚽ {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n⏱ {mac['dakika']}. dk\n💡 GOL OLACAK (S)"
                    await bot.send_message(CHAT_ID, msg, parse_mode=ParseMode.MARKDOWN)
                    bildirim_gonderilen[mac['id']] = puan
        except Exception as e: logger.error(f"Döngü: {e}")
        await asyncio.sleep(sure or 300)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
