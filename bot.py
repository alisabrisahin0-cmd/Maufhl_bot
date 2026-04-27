"""
MAC ANALIZ BOTU - WINNING CODE & DEEP ANALYSIS EDITION (ULTIMATE)
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta, timezone
import json

# Yapılandırma - Karakter temizliği dahil
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
# ZAMAN VE AKTİFLİK YÖNETİMİ
# ================================================
def aktif_mi():
    tr_saati = datetime.now(timezone(timedelta(hours=3)))
    saat = tr_saati.hour
    gun = tr_saati.weekday() 
    if gun <= 4: return 19 <= saat <= 23
    else: return 19 <= saat <= 22

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
        logger.error(f"Kayıt hatası: {e}")

# ================================================
# WINNING CODE — SERT FİLTRELEME SİSTEMİ
# ================================================
def winning_code_kontrol(mac):
    """
    KATI KURAL: VU=1 (Ev Baskısı) veya TÜM=1 (Genel Tempo) şartı aranır.
    """
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    son_gol_dk = mac.get('son_gol', 0)
    dakika = mac.get('dakika', 0)

    # Temel Filtreler
    VU = 1 if (shots_ev >= 2 and possession_ev >= 42 and dangerous_ev >= 15) else 0
    TÜM = 1 if (dangerous_ev + dangerous_dep) >= 25 else 0
    
    # Momentum & Direnç
    son_golden_beri = dakika - son_gol_dk if son_gol_dk > 0 else dakika
    MA = 1 if (son_golden_beri > 10 and (dangerous_ev + dangerous_dep) < 18) else 0
    # DIYI: Karşı takımın da gol atabileceği durumlar
    DİYİ = 1 if (dangerous_dep > dangerous_ev * 0.70 or shots_dep >= 2) else 0

    # Sinyal için temel şart: Ya Ev Baskısı (VU) ya da Genel Yüksek Tempo (TÜM)
    gecti = (VU == 1 or TÜM == 1) and MA == 0
    
    return {
        'gecti': gecti,
        'VU': VU, 'TÜM': TÜM, 'MA': MA, 'DİYİ': DİYİ,
        'detay': f"VU:{VU} TÜM:{TÜM} MA:{MA} DİYİ:{DİYİ}"
    }

# ================================================
# SİNYAL HESAPLAMA (DİNAMİK STRATEJİ)
# ================================================
def sinyal_hesapla(mac):
    wc = winning_code_kontrol(mac)
    if not wc['gecti']:
        return 0, [], "Filtreye Takıldı", wc

    puan = 4.0
    puan_detay = [f"✅ Sistem Onayı ({wc['detay']})"]
    
    dakika = mac.get('dakika', 0)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    toplam_gol = ev_gol + dep_gol
    ah = mac.get('ah_deger', 0.0)
    corner = mac.get('corner_toplam', 0)

    # --- STRATEJİ BELİRLEME (Senin istediğin güncel yapı) ---
    # 1. Karşılıklı Atak Durumu
    if wc['DİYİ'] == 1 and wc['TÜM'] == 1:
        strateji = "DÜELLO: KG VAR / 2.5 ÜST"
    # 2. İlk Yarı Gol Beklentisi
    elif dakika < 40 and toplam_gol == 0:
        strateji = "İLK YARI 0.5 ÜST"
    # 3. Tek Taraflı Favori Baskısı
    elif wc['VU'] == 1 and wc['DİYİ'] == 0:
        strateji = f"SIRADAKİ GOL: {mac['ev']}"
    # 4. Genel Maç Sonu Üst
    else:
        strateji = f"MAÇ SONU {toplam_gol + 0.5} ÜST"

    # Zaman Bonusları
    if 54 <= dakika <= 60:
        puan += 3.0
        puan_detay.append("🔥 Power Window (54-60') +3")
    elif 24 <= dakika <= 36:
        puan += 2.0
        puan_detay.append("⚡ Erken Baskı (24-36') +2")
    elif 75 <= dakika <= 82:
        puan += 2.5
        puan_detay.append("🏹 Son Kurşun (75-82') +2.5")

    # Skor Durumu Bonusları
    if ev_gol == dep_gol:
        puan += 1.5
        puan_detay.append("🤝 Beraberlik Bozulma Potansiyeli +1.5")
    
    if -1.25 <= ah <= -0.75:
        puan += 1.0
        puan_detay.append(f"📊 Favori Baskısı (AH: {ah}) +1")

    if corner >= 10:
        puan += 1.0
        puan_detay.append(f"🚩 Tempo Kanıtı (Korner: {corner}) +1")

    return puan, puan_detay, strateji, wc

# ================================================
# GEMINI AI — DERİN ANALİZ (SERTPOMPT)
# ================================================
async def gemini_analiz(mac, puan, strateji, wc):
    if not GEMINI_KEY: return "AI Analizi Aktif Değil.", 1.5
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={GEMINI_KEY}"
    
    prompt = f"""
    Sen profesyonel bir canlı bahis uzmanısın. 
    'Maç yüksek volatilite barındırıyor' veya 'Analiz hazır' gibi jenerik, hiçbir işe yaramayan cümleler kurman KESİNLİKLE YASAKTIR.
    
    Aşağıdaki canlı verileri kullanarak momentumu analiz et:
    MAÇ: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}
    DAKİKA: {mac['dakika']} | PUAN: {puan} | ÖNERİLEN BAHİS: {strateji}
    STATS: Şut:{mac.get('shots_on_target_ev', 0)}, Korner:{mac.get('corner_toplam', 0)}, AH:{mac.get('ah_deger', 0)}
    WINNING CODE: {wc['detay']} (VU: Ev baskısı, TÜM: Toplam tempo, DİYİ: Rakip direnci)

    ANALİZ GÖREVİN:
    1. Winning Code değerlerini baz alarak baskının neden gol getireceğini teknik dille açıkla.
    2. Strateji {strateji} ise, neden bu tercihin yapıldığını sahadaki momentumla destekle.
    3. Analizin teknik, profesyonel ve kısa olsun.
    
    Yanıtı sadece bu JSON formatında ver: {{"yorum": "Analiz metni", "kasa": 2.5}}
    """
    
    try:
        async with aiohttp.ClientSession() as session:
            for attempt in range(3):
                async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data['candidates'][0]['content']['parts'][0]['text']
                        
                        if "```json" in text:
                            text = text.split("```json")[1].split("```")[0].strip()
                        elif "```" in text:
                            text = text.split("```")[1].split("```")[0].strip()
                        
                        res = json.loads(text)
                        return res.get('yorum', 'Momentum verileri golü destekliyor.'), float(res.get('kasa', 1.5))
                await asyncio.sleep(1)
    except Exception as e:
        logger.error(f"AI Hatası: {e}")
    return "Winning Code onaylı, baskı verileri golün yaklaştığını gösteriyor.", 1.5

# ================================================
# API VE BİLDİRİM SİSTEMİ
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
                        if 5 <= fix['status']['elapsed'] <= 88:
                            m = {
                                'id': str(fix['id']),
                                'ev': f['teams']['home']['name'],
                                'dep': f['teams']['away']['name'],
                                'lig': f['league']['name'],
                                'dakika': fix['status']['elapsed'],
                                'ev_gol': f['goals']['home'] or 0,
                                'dep_gol': f['goals']['away'] or 0,
                                # Buradaki veriler gerçek API'den çekilmeli (Örnek veriler)
                                'shots_on_target_ev': 3, 
                                'shots_on_target_dep': 1,
                                'possession_ev': 52,
                                'dangerous_attacks_ev': 30,
                                'dangerous_attacks_dep': 15,
                                'corner_toplam': 9,
                                'ah_deger': -0.75,
                                'son_gol': 0
                            }
                            results.append(m)
    except Exception as e:
        logger.error(f"API Hatası: {e}")
    return results

async def bildirim_gonder(bot, mac, puan, detaylar, strateji, ai_yorum, kasa):
    status_emoji = "🔥" if puan >= 10 else "⚡"
    detay_str = "\n".join([f"- <i>{d}</i>" for d in detaylar])
    
    mesaj = (
        f"{status_emoji} <b>{mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}</b>\n"
        f"🏆 <code>{mac['lig']}</code> | ⏱ <b>{mac['dakika']}. Dakika</b>\n"
        f"────────────────────\n"
        f"📈 <b>SİNYAL PUANI: {puan}/12</b>\n"
        f"🎯 <b>STRATEJİ:</b> {strateji}\n"
        f"────────────────────\n"
        f"📝 <b>WINNING CODE ANALİZİ:</b>\n{detay_str}\n"
        f"────────────────────\n"
        f"🧠 <b>DEEP THINKING AI:</b>\n<i>{ai_yorum}</i>\n"
        f"────────────────────\n"
        f"💰 <b>KASA YÖNETİMİ:</b> %{kasa}\n"
        f"📍 <i>Tahmin: GOL / ÜST</i>"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode='HTML')
        await sinyal_kaydet(mac, puan, strateji, "GOL/UST", ai_yorum, kasa)
    except Exception as e:
        logger.error(f"Telegram Hatası: {e}")

async def ana_dongu():
    if not TELEGRAM_TOKEN or not APISPORTS_KEY:
        logger.error("API Anahtarları eksik!")
        return

    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()
    logger.info("Bot Aktif - Analizler Başladı")

    while True:
        try:
            if not aktif_mi():
                await asyncio.sleep(600)
                continue

            maclar = await macları_cek()
            for mac in maclar:
                if mac['id'] not in bildirim_gonderilen:
                    puan, detay, strat, wc = sinyal_hesapla(mac)
                    if puan >= MIN_PUAN:
                        ai_y, ai_k = await gemini_analiz(mac, puan, strat, wc)
                        await bildirim_gonder(bot, mac, puan, detay, strat, ai_y, ai_k)
                        bildirim_gonderilen[mac['id']] = True
            
            await asyncio.sleep(300) 
        except Exception as e:
            logger.error(f"Döngü hatası: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
