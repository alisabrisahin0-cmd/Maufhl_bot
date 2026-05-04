# MAC ANALIZ BOTU - V9.4 THE STABLE SIGNAL (KARARLI SİNYAL)
# Strateji: Altın Pencere & Hücum Epilasyonu (V9.1 Mimari)

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import random
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

# ================================================
# AYARLAR
# ================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")
GEMINI_KEYS = [os.getenv("GEMINI_KEY_1", ""), os.getenv("GEMINI_KEY_2", ""), os.getenv("GEMINI_KEY_3", "")]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]
MIN_PUAN = float(os.getenv("MIN_PUAN", "6.0"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}

# ================================================
# VERİ MOTORU (DIRECT INPLAY)
# ================================================
async def maclari_cek():
    maclar = []
    url = f"https://api.betsapi.com/v1/bet365/inplay?token={BETSAPI_TOKEN}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=20) as resp:
                data = await resp.json()
                results = data.get('results', [])
                for f in results:
                    try:
                        ev_isim = f.get('home', {}).get('name', 'Ev')
                        dep_isim = f.get('away', {}).get('name', 'Dep')
                        lig_isim = f.get('league', {}).get('name', 'Lig')
                        dk = int(f.get('timer', {}).get('tm', 0))
                        skor = f.get('ss', '0-0')
                        ev_gol = int(skor.split('-')[0]) if '-' in skor else 0
                        dep_gol = int(skor.split('-')[1]) if '-' in skor else 0
                        stats = f.get('stats', {})
                        ev_korner = int(stats.get('corners', [0, 0])[0])
                        dep_korner = int(stats.get('corners', [0, 0])[1])
                        ev_sot = int(stats.get('on_target', [0, 0])[0])
                        dep_sot = int(stats.get('on_target', [0, 0])[1])
                        ev_da = int(stats.get('dangerous_attacks', [0, 0])[0])
                        dep_da = int(stats.get('dangerous_attacks', [0, 0])[1])
                        
                        maclar.append({
                            'id': f.get('id'), 'ev': ev_isim, 'dep': dep_isim, 'lig': lig_isim, 
                            'dakika': dk, 'ev_gol': ev_gol, 'dep_gol': dep_gol, 
                            'ev_korner': ev_korner, 'dep_korner': dep_korner, 
                            'ev_sot': ev_sot, 'dep_sot': dep_sot, 'ev_da': ev_da, 'dep_da': dep_da
                        })
                    except: continue
        except Exception as e:
            logger.error(f"Veri çekme hatası: {e}")
    return maclar

# ================================================
# SAF STRATEJİ ANALİZİ (V9.1 Mimari)
# ================================================
def strateji_filtrele(mac):
    dk = mac['dakika']; ev_gol = mac['ev_gol']; dep_gol = mac['dep_gol']
    toplam_gol = ev_gol + dep_gol; fark = abs(ev_gol - dep_gol)
    toplam_sot = mac['ev_sot'] + mac['dep_sot']
    toplam_korner = mac['ev_korner'] + mac['dep_korner']
    toplam_da = mac['ev_da'] + mac['dep_da']

    if toplam_gol >= 5: return 0.0, [], "KAOS_ESIGI"
    if fark >= 3: return 0.0, [], "OLUM_BOLGESI"
    
    puan = 0.0; detay = []

    if 55 <= dk <= 60:
        puan += 4.0; detay.append("ALTIN PENCERE (55-60') +4.0")
    elif 60 < dk <= 75:
        puan += 2.0; detay.append("GECIS OYUNU EVRESI +2.0")

    if (ev_gol, dep_gol) in [(2,1), (1,2), (3,1), (1,3)]:
        puan += 2.0; detay.append(f"OPTIMUM SKOR ({ev_gol}-{dep_gol}) +2.0")

    if toplam_sot <= 8:
        puan += (toplam_sot * 0.25); detay.append(f"MAKUL SUT SEVIYESI ({toplam_sot})")
    else:
        puan -= 1.5; detay.append("HUCUM EPILASYONU (SOT > 8) CEZA!")

    if toplam_korner > 12:
        puan -= 1.0; detay.append("ETKISIZ KORNER BASKISI -1.0")
    if toplam_da > 80:
        puan += 1.0; detay.append("YUKSEK TEHLIKELI ATAK +1.0")

    return round(puan, 1), detay, "SUCCESS"

# ================================================
# ORACLE AI VE BİLDİRİM SİSTEMİ
# ================================================
async def gemini_oracle(mac):
    if not GEMINI_KEYS: return "Analiz uygun.", True
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={random.choice(GEMINI_KEYS)}"
    prompt = f"Analist olarak bu maci yorumla: {mac['ev']}-{mac['dep']} ({mac['dakika']}. dk) | SOT: {mac['ev_sot']}+{mac['dep_sot']}. JSON: {{\"yorum\": \"...\", \"gir\": true}}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}, timeout=10) as resp:
                data = await resp.json(); res = json.loads(data['candidates'][0]['content']['parts'][0]['text'])
                return res['yorum'], res['gir']
    except: return "Istatistikler kirilma noktasinda.", True

async def bildirim_gonder(bot, mac, puan, detay):
    ai_yorum, ai_gir = await gemini_oracle(mac)
    if not ai_gir: return
    
    tavsiye = "ALTIN FIRSAT" if 55 <= mac['dakika'] <= 60 else "STRATEJIK SINYAL"
    
    # Karakter hatasını önlemek için güvenli format
    mesaj = (
        f"🤖 {tavsiye}\n"
        f"Maç: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"Dakika: {mac['dakika']} | Lig: {mac['lig']}\n"
        f"--------------------\n"
        f"Strateji Puanı: {puan}/12\n"
        f"Analiz: " + ", ".join(detay) + "\n"
        f"--------------------\n"
        f"Veri: SOT: {mac['ev_sot']}/{mac['dep_sot']} | Atak: {mac['ev_da']}/{mac['dep_da']}\n"
        f"--------------------\n"
        f"Üstad AI: {ai_yorum}\n"
        f"--------------------\n"
        f"💡 Tavsiye: SIRADAKİ GOL (S)"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
    except Exception as e:
        logger.error(f"Telegram Mesaj Gönderilemedi: {e}")

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    logger.info("Bot Başlatıldı.")
    while True:
        try:
            maclar = await maclari_cek()
            for mac in maclar:
                puan, detay, durum = strateji_filtrele(mac)
                if puan >= MIN_PUAN and mac['id'] not in bildirim_gonderilen:
                    await bildirim_gonder(bot, mac, puan, detay)
                    bildirim_gonderilen[mac['id']] = puan
        except Exception as e: 
            logger.error(f"Döngü Hatası: {e}")
        await asyncio.sleep(180)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
