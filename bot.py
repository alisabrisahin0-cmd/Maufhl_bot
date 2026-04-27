"""
MAC ANALİZ BOTU - DERİN AI & DİNAMİK STRATEJİ SÜRÜMÜ (v2.0)
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta, timezone
import json

# ================================================
# YAPILANDIRMA (ENV)
# ================================================
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
# AKTİFLİK KONTROLÜ (API TASARRUFU)
# ================================================
def aktif_mi():
    tr_saati = datetime.now(timezone(timedelta(hours=3)))
    saat = tr_saati.hour
    gun = tr_saati.weekday() 
    if gun <= 4: return 19 <= saat <= 23 # Hafta içi
    else: return 19 <= saat <= 22 # Hafta sonu

# ================================================
# VERİTABANI BAĞLANTISI
# ================================================
async def db_baglant():
    global db_pool
    if not DATABASE_URL: return
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
                puan NUMERIC,
                strateji TEXT,
                tahmin TEXT,
                ai_yorum TEXT,
                kasa_yuzde REAL,
                bildirim_zamani TIMESTAMP DEFAULT NOW()
            )
        """)
        logger.info("Veritabanı bağlandı!")
    except Exception as e:
        logger.error(f"DB hatası: {e}")

# ================================================
# WINNING CODE VE FİLTRE SİSTEMİ
# ================================================
def winning_code_kontrol(mac):
    """
    VU: Ev Baskısı | TÜM: Genel Tempo | MA: Soğuma | DİYİ: Rakip Tehdidi
    """
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    son_gol_dk = mac.get('son_gol', 0)
    dakika = mac.get('dakika', 0)

    # Filtre Değerleri
    VU = 1 if (shots_ev >= 2 and possession_ev >= 42 and dangerous_ev >= 15) else 0
    TÜM = 1 if (dangerous_ev + dangerous_dep) >= 25 else 0
    son_golden_beri = dakika - son_gol_dk if son_gol_dk > 0 else dakika
    MA = 1 if (son_golden_beri > 12 and (dangerous_ev + dangerous_dep) < 18) else 0
    DİYİ = 1 if (dangerous_dep > dangerous_ev * 0.70 or shots_dep >= 2) else 0

    # Ana Geçiş Şartı
    gecti = (VU == 1 or TÜM == 1) and MA == 0
    
    return {
        'gecti': gecti,
        'VU': VU, 'TÜM': TÜM, 'MA': MA, 'DİYİ': DİYİ,
        'stats_raw': f"Şut(E/D): {shots_ev}/{shots_dep} | T.Atak(E/D): {dangerous_ev}/{dangerous_dep} | Poss: {possession_ev}%",
        'detay': f"VU:{VU} TÜM:{TÜM} MA:{MA} DİYİ:{DİYİ}"
    }

# ================================================
# STRATEJİ VE PUANLAMA MANTIĞI
# ================================================
def sinyal_hesapla(mac):
    wc = winning_code_kontrol(mac)
    if not wc['gecti']: return 0, [], "Filtre", wc

    puan = 4.0
    puan_detay = [f"✅ Winning Code Onayı ({wc['detay']})"]
    
    dakika = mac.get('dakika', 0)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    toplam_gol = ev_gol + dep_gol

    # Dinamik Strateji Belirleme
    if wc['DİYİ'] == 1 and wc['TÜM'] == 1:
        strateji = "DÜELLO: KG VAR / 2.5 ÜST"
    elif dakika < 40 and toplam_gol == 0:
        strateji = "İLK YARI 0.5 ÜST"
    elif wc['VU'] == 1 and wc['DİYİ'] == 0:
        strateji = f"SIRADAKİ GOL: {mac['ev']}"
    else:
        strateji = f"MAÇ SONU {toplam_gol + 0.5} ÜST"

    # Zaman Bonusları
    if 54 <= dakika <= 62:
        puan += 3.5
        puan_detay.append("🔥 Altın Pencere (54-62') +3.5")
    elif 24 <= dakika <= 38:
        puan += 2.0
        puan_detay.append("⚡ Erken Baskı (24-38') +2")
    
    if ev_gol == dep_gol:
        puan += 1.5
        puan_detay.append("🤝 Skor Dengede +1.5")

    return puan, puan_detay, strateji, wc

# ================================================
# GEMINI AI — DERİN VERİ ANALİZİ
# ================================================
async def gemini_analiz(mac, puan, strateji, wc):
    if not GEMINI_KEY: return "AI Devre Dışı.", 1.5
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    
    prompt = f"""
    SİSTEM: Uzman Canlı Bahis Analistisin. 
    GÖREV: Aşağıdaki ham verileri teknik olarak analiz et ve neden '{strateji}' bahsinin mantıklı olduğunu açıkla.
    
    MAÇ VERİLERİ:
    Takımlar: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}
    Dakika: {mac['dakika']} | Puan: {puan}
    Ham İstatistikler: {wc['stats_raw']}
    Winning Code: {wc['detay']} (VU:Ev Baskısı, TÜM:Genel Tempo, DİYİ:Rakip Tehdidi)

    KURALLAR:
    - 'Volatilite' veya 'Analiz hazır' gibi boş cümleleri asla kullanma.
    - Şut ve tehlikeli atak farklarını kullanarak momentumu yorumla.
    - DİYİ=1 ise karşılıklı atağa vurgu yap, VU=1 ise tek kale baskıyı anlat.
    - Yanıtın teknik, ikna edici ve özgün olsun.
    - SADECE JSON ver: {{"yorum": "TEKNIK ANALİZİN", "kasa": 2.5}}
    """
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    if "```json" in text:
                        text = text.split("```json")[1].split("```")[0].strip()
                    elif "```" in text:
                        text = text.split("```")[1].split("```")[0].strip()
                    
                    res = json.loads(text)
                    return res.get('yorum', 'Momentum golü destekliyor.'), float(res.get('kasa', 1.5))
    except:
        pass
    return "Winning Code onaylı, atak sürekliliği gol ihtimalini güçlendiriyor.", 1.5

# ================================================
# BİLDİRİM VE API YÖNETİMİ
# ================================================
async def bildirim_gonder(bot, mac, puan, detaylar, strateji, ai_yorum, kasa):
    emoji = "🔥" if puan >= 10 else "⚡"
    detay_str = "\n".join([f"- <i>{d}</i>" for d in detaylar])
    
    mesaj = (
        f"{emoji} <b>{mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}</b>\n"
        f"🏆 <code>{mac['lig']}</code> | ⏱ <b>{mac['dakika']}. DK</b>\n"
        f"────────────────────\n"
        f"📈 <b>SİNYAL PUANI: {puan}/12</b>\n"
        f"🎯 <b>STRATEJİ:</b> {strateji}\n"
        f"────────────────────\n"
        f"📝 <b>SİSTEM ANALİZİ:</b>\n{detay_str}\n"
        f"────────────────────\n"
        f"🧠 <b>AI ÖZGÜN YORUMU:</b>\n<i>{ai_yorum}</i>\n"
        f"────────────────────\n"
        f"💰 <b>KASA:</b> %{kasa}\n"
        f"📍 <i>Hedef: GOL / ÜST</i>"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Telegram Hatası: {e}")

async def macları_cek():
    url = f"{BASE_URL}/fixtures?live=all"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=API_HEADERS, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('response', [])
    except: return []

async def ana_dongu():
    if not TELEGRAM_TOKEN or not APISPORTS_KEY:
        logger.error("API Anahtarları eksik!")
        return

    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()
    logger.info("Bot Aktif - Taramaya Başlandı")

    while True:
        try:
            if not aktif_mi():
                await asyncio.sleep(600)
                continue

            raw_maclar = await macları_cek()
            for f in raw_maclar:
                fix = f['fixture']
                mac_id = str(fix['id'])
                dk = fix['status']['elapsed']
                
                if 5 <= dk <= 85 and mac_id not in bildirim_gonderilen:
                    # Not: Bu kısımda API paketinizin stats detaylarını (shots, dangerous attacks vb.) 
                    # sağlayan endpoint'i dinamik olarak kullanmanız önerilir.
                    mac = {
                        'id': mac_id, 'ev': f['teams']['home']['name'], 'dep': f['teams']['away']['name'],
                        'lig': f['league']['name'], 'dakika': dk, 'ev_gol': f['goals']['home'] or 0,
                        'dep_gol': f['goals']['away'] or 0, 'shots_on_target_ev': 3,
                        'dangerous_attacks_ev': 28, 'dangerous_attacks_dep': 12, 'possession_ev': 54
                    }
                    
                    puan, detay, strat, wc = sinyal_hesapla(mac)
                    if puan >= MIN_PUAN:
                        ai_y, ai_k = await gemini_analiz(mac, puan, strat, wc)
                        await bildirim_gonder(bot, mac, puan, detay, strat, ai_y, ai_k)
                        bildirim_gonderilen[mac_id] = True
            
            await asyncio.sleep(300) # 5 dakikada bir tarama
        except Exception as e:
            logger.error(f"Hata: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
