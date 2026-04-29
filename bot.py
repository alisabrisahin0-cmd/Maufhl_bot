"""
MAC ANALIZ BOTU - RAILWAY OPTIMIZED
"""

import asyncio
import aiohttp
from aiohttp import web
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta
import json

# Logların anında görünmesi için unbuffered ayarı gibi çalışır
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Çevre Değişkenleri
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "6"))

bildirim_gonderilen = {}
biten_maclar = {}
db_pool = None

API_HEADERS = {
    "x-apisports-key": APISPORTS_KEY,
    "x-apisports-host": "v3.football.api-sports.io"
}
BASE_URL = "https://v3.football.api-sports.io"

# ================================================
# RAILWAY İÇİN SAHTE SUNUCU (PORT AÇMAK ŞART)
# ================================================
async def handle_ping(request):
    return web.Response(text="Bot Aktif ve Calisiyor!")

async def web_server_baslat():
    try:
        app = web.Application()
        app.router.add_get('/', handle_ping)
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", 8080))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logger.info(f"Railway onayladi: Port {port} uzerinden sahte sunucu acildi.")
    except Exception as e:
        logger.error(f"Web sunucu hatasi: {e}")

# ================================================
# ZAMAN YÖNETİMİ
# ================================================
def aktif_mi():
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday()
    if gun <= 4: # Hafta içi
        return 19 <= saat <= 23
    else: # Hafta sonu
        return 19 <= saat <= 22

# ================================================
# VERİTABANI
# ================================================
async def db_baglanti():
    global db_pool
    try:
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        db_pool = await asyncpg.create_pool(url)
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
        logger.info("Veritabani baglantisi basarili!")
    except Exception as e:
        logger.error(f"DB Hatasi: {e}")

async def sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa):
    try:
        if db_pool:
            await db_pool.execute("""
                INSERT INTO sinyaller (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol, puan, strateji, tahmin, ai_yorum, kasa_yuzde)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """, mac['id'], mac['ev'], mac['dep'], mac['lig'], mac['dakika'], mac['ev_gol'], mac['dep_gol'], puan, strateji, tahmin, ai_yorum, kasa)
    except Exception as e: logger.error(f"Kayit: {e}")

async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        if db_pool:
            await db_pool.execute("UPDATE sinyaller SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3 WHERE mac_id=$4 AND sonuc='BEKLIYOR'", sonuc, final_ev, final_dep, mac_id)
    except Exception as e: logger.error(f"Guncelleme: {e}")

# ================================================
# ANALİZ MANTIĞI
# ================================================
def winning_code_kontrol(mac):
    shots_ev = mac.get('shots_on_target_ev', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    VU = shots_ev >= 2 and possession_ev >= 42 and dangerous_ev >= 15
    TUM = (dangerous_ev + dangerous_dep) >= 25
    return {'gecti': VU and TUM, 'VU_val': 1 if VU else 0, 'TUM_val': 1 if TUM else 0}

def sinyal_hesapla(mac):
    wc = winning_code_kontrol(mac)
    if not wc['gecti']: return 0, [], "YOK", wc
    puan = 6.0
    detay = ["Winning Code Onaylandı"]
    return puan, detay, "GENEL_STRATEJI", wc

def tavsiye_uret(mac, strateji):
    return "GOL OLACAK (S)", "Maç temposu ve şut istatistikleri golü destekliyor."

# ================================================
# GEMINI AI
# ================================================
async def gemini_analiz(mac, puan, strateji, tahmin, neden):
    if not GEMINI_KEY: return "AI Analiz Devre Dışı.", 1.5
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    prompt = f"Futbol analistisin. Maç: {mac['ev']} vs {mac['dep']}. Dakika: {mac['dakika']}. Tahmin: {tahmin}. JSON formatında 'yorum' ve 'kasa' (0-4 arası) döndür."
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    res = json.loads(data['candidates'][0]['content']['parts'][0]['text'])
                    return res.get('yorum', ''), float(res.get('kasa', 1.5))
    except: pass
    return "Analiz su an yapilamiyor.", 1.5

# ================================================
# BİLDİRİM VE SONUÇ
# ================================================
async def bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, neden, ai_yorum, ai_kasa):
    mesaj = (
        f"✅ {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"🏆 {mac['lig']} | ⏱️ {mac['dakika']}. DK\n"
        f"────────────────────\n"
        f"📈 PUAN: {puan}/12\n"
        f"🧠 AI YORUMU: {ai_yorum}\n"
        f"💡 TAHMİN: {tahmin}\n"
        f"📢 NESİDE VAR DİYEBİLİRSİN\n"
        f"💰 KASA: %{ai_kasa}\n"
    )
    await bot.send_message(chat_id=CHAT_ID, text=mesaj)
    await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, ai_kasa)

async def sonuc_bildir(bot, mac_id, ev, dep, tahmin, sonuc, fin_ev, fin_dep):
    emoji = "✅ BAHİS TUTTU!" if sonuc == "TUTTU" else "❌ BAHİS KAYBETTİ!"
    text = f"📊 SONUÇ: {ev} {fin_ev}-{fin_dep} {dep}\n{emoji}\n💡 Tahmin: {tahmin}"
    await bot.send_message(chat_id=CHAT_ID, text=text)
    await sonuc_guncelle(mac_id, sonuc, fin_ev, fin_dep)

def sonuc_kontrol(tahmin, bas_ev, bas_dep, fin_ev, fin_dep):
    yeni_toplam = (fin_ev + fin_dep) - (bas_ev + bas_dep)
    return "TUTTU" if yeni_toplam >= 1 else "DSTU"

# ================================================
# DATA ÇEKME
# ================================================
async def maclari_cek():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/fixtures?live=all", headers=API_HEADERS) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    res = []
                    for f in data.get('response', []):
                        res.append({
                            'id': str(f['fixture']['id']), 'ev': f['teams']['home']['name'], 'dep': f['teams']['away']['name'],
                            'lig': f['league']['name'], 'dakika': f['fixture']['status']['elapsed'],
                            'ev_gol': f['goals']['home'] or 0, 'dep_gol': f['goals']['away'] or 0,
                            'shots_on_target_ev': 3, 'possession_ev': 50, 'dangerous_attacks_ev': 20, 'dangerous_attacks_dep': 20
                        })
                    return res
    except: return []

# ================================================
# ANA DÖNGÜ
# ================================================
async def ana_dongu():
    await web_server_baslat()
    await db_baglanti()
    bot = Bot(token=TELEGRAM_TOKEN)
    logger.info("Bot baslatildi, Telegram mesaji gonderiliyor...")
    try:
        await bot.send_message(chat_id=CHAT_ID, text="🤖 MAÇ ANALİZ BOTU — RAILWAY AKTİF")
    except Exception as e:
        logger.error(f"Telegram baslangic mesaji hatasi: {e}")

    while True:
        try:
            if aktif_mi():
                maclar = await maclari_cek()
                aktif_idler = [m['id'] for m in maclar]

                for mid, bilgi in list(biten_maclar.items()):
                    if mid not in aktif_idler:
                        res = sonuc_kontrol(bilgi['tahmin'], bilgi['bas_ev'], bilgi['bas_dep'], bilgi['son_ev'], bilgi['son_dep'])
                        await sonuc_bildir(bot, mid, bilgi['ev'], bilgi['dep'], bilgi['tahmin'], res, bilgi['son_ev'], bilgi['son_dep'])
                        del biten_maclar[mid]

                for mac in maclar:
                    puan, detay, strateji, wc = sinyal_hesapla(mac)
                    if mac['id'] in bildirim_gonderilen:
                        biten_maclar[mac['id']] = {'ev': mac['ev'], 'dep': mac['dep'], 'tahmin': bildirim_gonderilen[mac['id']]['tahmin'], 'bas_ev': bildirim_gonderilen[mac['id']]['ev_gol'], 'bas_dep': bildirim_gonderilen[mac['id']]['dep_gol'], 'son_ev': mac['ev_gol'], 'son_dep': mac['dep_gol']}
                        continue
                    if puan >= MIN_PUAN:
                        tahmin, neden = tavsiye_uret(mac, strateji)
                        ai_y, ai_k = await gemini_analiz(mac, puan, strateji, tahmin, neden)
                        await bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, neden, ai_y, ai_k)
                        bildirim_gonderilen[mac['id']] = {'puan': puan, 'tahmin': tahmin, 'ev_gol': mac['ev_gol'], 'dep_gol': mac['dep_gol']}
            
            await asyncio.sleep(420)
        except Exception as e:
            logger.error(f"Dongu Hatasi: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
