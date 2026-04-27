"""
MAC ANALIZ BOTU - OPTIMIZE SISTEM (HATA DÜZELTİLDİ)
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta, timezone
import json

# Yapılandırma - .strip() ile gizli karakter temizliği
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
GEMINI_KEY = os.getenv("GEMINI_KEY", "").strip()
MIN_PUAN = int(os.getenv("MIN_PUAN", "6"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
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
    gun = tr_saati.weekday() 

    if gun <= 4:  # Hafta içi: 19:00 - 00:00
        return 19 <= saat <= 23
    else:  # Hafta sonu: 19:00 - 23:00
        return 19 <= saat <= 22

# ================================================
# VERİTABANI İŞLEMLERİ
# ================================================
async def db_baglant():
    global db_pool
    if not DATABASE_URL:
        logger.error("DATABASE_URL eksik!")
        return
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
                INSERT INTO sinyaller
                (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol,
                 puan, strateji, tahmin, ai_yorum, kasa_yuzde)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """, mac['id'], mac['ev'], mac['dep'], mac['lig'],
                mac['dakika'], mac['ev_gol'], mac['dep_gol'],
                puan, strateji, tahmin, ai_yorum, kasa_yuzde)
    except Exception as e:
        logger.error(f"Kayit hatası: {e}")

# ================================================
# ANALİZ VE FİLTRELEME SİSTEMİ (HATALI KISIM BURADA DÜZELTİLDİ)
# ================================================
def winning_code_kontrol(mac):
    possession_ev = mac.get('possession_ev', 50)
    dakika = mac.get('dakika', 0)
    danger_ev = mac.get('dangerous_attacks_ev', 0)
    danger_dep = mac.get('dangerous_attacks_dep', 0)
    son_gol_dakika = mac.get('son_gol', 0)
    
    VU = (possession_ev >= 40)
    TUM = (danger_ev + danger_dep) >= 15
    
    # Hatalı olan parantez burada kapatıldı ve mantık tamamlandı
    son_golden_beri = dakika - son_gol_dakika
    MA = not (son_golden_beri > 10 and (danger_ev + danger_dep) < 5)
    
    DIYI = (danger_ev >= danger_dep * 0.8)
    
    return {
        'gecti': VU and TUM and MA and DIYI,
        'detay': f"VU:{int(VU)} TUM:{int(TUM)} MA:{int(MA)} DIYI:{int(DIYI)}"
    }

def sinyal_hesapla(mac):
    wc = winning_code_kontrol(mac)
    if not wc['gecti']: return 0, [], "", wc
    
    puan = 6
    puan_detay = [f"+6: Temel filtreler ({wc['detay']})"]
    
    if mac['dakika'] > 60:
        puan += 2
        puan_detay.append("+2: 60+ Dakika Baskısı")

    return puan, puan_detay, "STRATEJI_A", wc

async def gemini_analiz(mac, puan, strateji, tahmin, neden, wc):
    if not GEMINI_KEY: return "AI Analizi Yapılamadı.", 1.5
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    
    prompt = (f"Analiz: {mac['ev']} vs {mac['dep']}. Skor: {mac['ev_gol']}-{mac['dep_gol']}, "
              f"Dakika: {mac['dakika']}. Bu maçı 10 kelimelik bir uzman gibi yorumla ve JSON dön: "
              f"{{\"yorum\": \"...\", \"kasa\": 2.0}}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    if "```" in text:
                        text = text.split("```")[1].replace("json", "").strip()
                    res = json.loads(text)
                    return res.get('yorum', ''), float(res.get('kasa', 1.5))
    except: pass
    return "Maç dengeli görünüyor.", 1.5

# ================================================
# API VE BİLDİRİM
# ================================================
async def macları_cek():
    url = f"{BASE_URL}/fixtures?live=all"
    results = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=API_HEADERS, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for f in data.get('response', []):
                        fix = f['fixture']
                        if 10 <= fix['status']['elapsed'] <= 85:
                            m = {
                                'id': str(fix['id']),
                                'ev': f['teams']['home']['name'],
                                'dep': f['teams']['away']['name'],
                                'lig': f['league']['name'],
                                'dakika': fix['status']['elapsed'],
                                'ev_gol': f['goals']['home'] or 0,
                                'dep_gol': f['goals']['away'] or 0,
                                'possession_ev': 50, # Statik örnek değer
                                'dangerous_attacks_ev': 20,
                                'dangerous_attacks_dep': 15,
                                'son_gol': 0
                            }
                            results.append(m)
                else:
                    logger.error(f"API Sorgu Hatası: {resp.status}")
    except Exception as e: 
        logger.error(f"API Baglanti Hatası: {e}")
    return results

async def bildirim_gonder(bot, mac, puan, puan_detay, strateji, tahmin, ai_yorum, kasa):
    mesaj = (f"⚽️ {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
             f"Dakika: {mac['dakika']} | Puan: {puan}\n"
             f"Tahmin: {tahmin}\nAI Yorum: {ai_yorum}\nÖneri: %{kasa}")
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa)
    except Exception as e:
        logger.error(f"Telegram Hatası: {e}")

# ================================================
# ANA DÖNGÜ
# ================================================
async def ana_dongu():
    if not TELEGRAM_TOKEN or not APISPORTS_KEY:
        logger.error("Gerekli API anahtarları eksik!")
        return

    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()
    logger.info("Bot başlatıldı...")

    uyku_modu = False

    while True:
        try:
            if not aktif_mi():
                if not uyku_modu:
                    logger.info("Uyku moduna gecildi. (19:00 bekleniyor)")
                    uyku_modu = True
                await asyncio.sleep(600)
                continue
            
            if uyku_modu:
                logger.info("Bot uyandı!")
                await bot.send_message(chat_id=CHAT_ID, text="🟢 Bot Aktif! Maçlar taranıyor...")
                uyku_modu = False

            maclar = await macları_cek()
            logger.info(f"Taranan maç sayısı: {len(maclar)}")
            
            for mac in maclar:
                if mac['id'] not in bildirim_gonderilen:
                    puan, detay, strat, wc = sinyal_hesapla(mac)
                    if puan >= MIN_PUAN:
                        ai_y, ai_k = await gemini_analiz(mac, puan, strat, "GOL", "Analiz", wc)
                        await bildirim_gonder(bot, mac, puan, detay, strat, "GOL (0.5 ÜST)", ai_y, ai_k)
                        bildirim_gonderilen[mac['id']] = True
            
            await asyncio.sleep(420) # 7 dakikada bir kontrol
        except Exception as e:
            logger.error(f"Döngü hatası: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
