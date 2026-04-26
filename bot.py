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

def kontrol_suresi_al():
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday()
    if gun >= 5:
        if 18 <= saat < 23:
            return 360
        else:
            return None
    else:
        if 19 <= saat <= 23:
            return 360
        else:
            return None

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
        logger.info("Veritabani baglandi!")
    except Exception as e:
        logger.error(f"DB hatasi: {e}")

async def sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa_yuzde):
    try:
        if db_pool:
            await db_pool.execute("""
                INSERT INTO sinyaller (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol, puan, strateji, tahmin, ai_yorum, kasa_yuzde)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """, mac['id'], mac['ev'], mac['dep'], mac['lig'], mac['dakika'], mac['ev_gol'], mac['dep_gol'], puan, strateji, tahmin, ai_yorum, kasa_yuzde)
    except Exception as e:
        logger.error(f"Kayit hatasi: {e}")

def sinyal_hesapla(mac):
    puan = 0
    puan_detay = []
    strateji = "GENEL"
    dk = mac['dakika']
    eg = mac['ev_gol']
    dg = mac['dep_gol']
    
    if (eg + dg) >= 3:
        puan += 1
        puan_detay.append("+1: 3+ Gol")
    if eg > 0 and dg > 0:
        puan += 1
        puan_detay.append("+1: KG Var")
    if eg == dg:
        puan += 2
        puan_detay.append("+2: Beraberlik")
        strateji = "BERABERLIK"
    
    if 54 <= dk <= 60:
        puan += 3
        puan_detay.append("+3: Power Window")
        
    return puan, puan_detay, strateji

def tavsiye_uret(mac, strateji):
    if strateji == "BERABERLIK":
        return "GOL OLACAK (S)", "Skor dengede, baski artiyor."
    return "GOL OLACAK (S)", "Mac temposu gol icin uygun."

async def gemini_analiz(mac, puan, strateji, tahmin, neden):
    if not GEMINI_KEY: return "AI Devre Disi", 1.5
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    prompt = f"Mac: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}. Puan:{puan}. Tahmin:{tahmin}. Analiz et ve JSON formatinda 'yorum' ve 'kasa' dondur."
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    res = data['candidates'][0]['content']['parts'][0]['text']
                    start = res.find("{")
                    end = res.rfind("}") + 1
                    j = json.loads(res[start:end])
                    return j.get('yorum', "Analiz hazir."), j.get('kasa', 1.5)
    except: pass
    return "Analiz yapılamadı.", 1.5

async def bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, neden, ai_y, ai_k):
    mesaj = (
        f"✅ {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"{mac['lig']} | {mac['dakika']}.dk\n\n"
        f"PUAN: {puan}/12\n"
        f"TAHMIN: {tahmin}\n"
        f"AI: {ai_y}\n"
        f"KASA: %{ai_k}"
    )
    await bot.send_message(chat_id=CHAT_ID, text=mesaj)

async def macları_cek():
    url = f"{BASE_URL}/fixtures?live=all"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=API_HEADERS) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    res = []
                    for f in data.get('response', []):
                        res.append({
                            'id': str(f['fixture']['id']),
                            'ev': f['teams']['home']['name'],
                            'dep': f['teams']['away']['name'],
                            'lig': f['league']['name'],
                            'dakika': f['fixture']['status']['elapsed'] or 0,
                            'ev_gol': f['goals']['home'] or 0,
                            'dep_gol': f['goals']['away'] or 0
                        })
                    return res
    except: return []

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()
    logger.info("Bot basladi.")

    while True:
        try:
            sure = kontrol_suresi_al()
            if sure is None:
                await asyncio.sleep(1800)
                continue

            maclar = await macları_cek()
            await asyncio.sleep(3) # Rate limit koruması

            for mac in maclar:
                if 5 <= mac['dakika'] <= 85:
                    puan, detay, strat = sinyal_hesapla(mac)
                    if puan >= MIN_PUAN and mac['id'] not in bildirim_gonderilen:
                        tahmin, neden = tavsiye_uret(mac, strat)
                        ai_y, ai_k = await gemini_analiz(mac, puan, strat, tahmin, neden)
                        await bildirim_gonder(bot, mac, puan, detay, strat, tahmin, neden, ai_y, ai_k)
                        await sinyal_kaydet(mac, puan, strat, tahmin, ai_y, ai_k)
                        bildirim_gonderilen[mac['id']] = puan
        except Exception as e:
            logger.error(f"Hata: {e}")
        
        await asyncio.sleep(sure if sure else 360)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
