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
    """Saate göre kontrol süresini belirle (saniye)"""
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
                bildirim_zamani TIMESTAMP DEFAULT NOW(),
                sonuc TEXT DEFAULT 'BEKLIYOR',
                final_ev_gol INTEGER DEFAULT 0,
                final_dep_gol INTEGER DEFAULT 0
            )
        """)
        logger.info("✅ Veritabanı bağlandı!")
    except Exception as e:
        logger.error(f"DB hatası: {e}")


async def sinyal_kaydet(mac, puan, tahmin):
    try:
        if db_pool:
            await db_pool.execute("""
                INSERT INTO sinyaller 
                (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol, puan, tahmin)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """, mac['id'], mac['ev'], mac['dep'], mac['lig'],
                mac['dakika'], mac['ev_gol'], mac['dep_gol'], puan, tahmin)
    except Exception as e:
        logger.error(f"Kayıt hatası: {e}")


async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        if db_pool:
            await db_pool.execute("""
                UPDATE sinyaller 
                SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3
                WHERE mac_id=$4 AND sonuc='BEKLIYOR'
            """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e:
        logger.error(f"Güncelleme hatası: {e}")


async def haftalik_rapor_gonder(bot):
    try:
        bir_hafta_once = datetime.now() - timedelta(days=7)
        rows = await db_pool.fetch("SELECT * FROM sinyaller WHERE bildirim_zamani > $1 AND sonuc != 'BEKLIYOR'", bir_hafta_once)
        toplam = len(rows)
        if toplam == 0: return
        kazanan = len([r for r in rows if r['sonuc'] == 'TUTTU'])
        oran = round(kazanan / toplam * 100, 1)
        mesaj = f"📊 *HAFTALIK RAPOR*\n\n📈 Toplam: {toplam}\n✅ Kazanan: {kazanan}\n🎯 Başarı Oranı: %{oran}"
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Rapor hatası: {e}")


async def macları_cek():
    url = "https://v3.football.api-sports.io/fixtures?live=all"
    headers = {"x-apisports-key": RAPIDAPI_KEY}
    maclar = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    fixtures = data.get('response', [])
                    for f in fixtures:
                        try:
                            fixture = f.get('fixture', {})
                            teams = f.get('teams', {})
                            goals = f.get('goals', {})
                            league = f.get('league', {})
                            dakika = int(fixture.get('status', {}).get('elapsed', 0) or 0)
                            if dakika < 5 or dakika > 85: continue
                            maclar.append({
                                'id': str(fixture.get('id', '')),
                                'ev': teams.get('home', {}).get('name', '?'),
                                'dep': teams.get('away', {}).get('name', '?'),
                                'lig': league.get('name', '?'),
                                'dakika': dakika,
                                'ev_gol': int(goals.get('home', 0) or 0),
                                'dep_gol': int(goals.get('away', 0) or 0)
                            })
                        except: continue
                else: logger.error(f"API Hata: {resp.status}")
    except Exception as e: logger.error(f"API Bağlantı: {e}")
    return maclar


def sinyal_hesapla(mac):
    puan = 0
    toplam_gol = mac['ev_gol'] + mac['dep_gol']
    if toplam_gol >= 2: puan += 4
    if mac['dakika'] >= 30: puan += 3
    return puan, ["✅ Aktif Veri"]


def tavsiye_uret(mac):
    return "GOL OLACAK (S)"


async def bildirim_gonder(bot, mac, puan, sinyaller, tahmin):
    mesaj = f"⚽ *{mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}*\n⏱ {mac['dakika']}. dk\n📊 Puan: {puan}\n💡 *Tahmin: {tahmin}*"
    await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode=ParseMode.MARKDOWN)
    await sinyal_kaydet(mac, puan, tahmin)


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
                puan, sinyaller = sinyal_hesapla(mac)
                if puan >= MIN_PUAN and mac['id'] not in bildirim_gonderilen:
                    tahmin = tavsiye_uret(mac)
                    await bildirim_gonder(bot, mac, puan, sinyaller, tahmin)
                    bildirim_gonderilen[mac['id']] = {'puan': puan, 'tahmin': tahmin}
        except Exception as e:
            logger.error(f"Döngü hatası: {e}")
        await asyncio.sleep(sure or 300)


if __name__ == "__main__":
    asyncio.run(ana_dongu())
