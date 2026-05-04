# MAC ANALIZ BOTU - V9.5 THE LIVE VERIFIER (CANLI DOĞRULAYICI)
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
    # DIRECT INPLAY: Tek istekte tüm stats gelir
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
            logger.error(f"Veri çekme hatası: {e}")[cite: 2]
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

    # HARD BLOCKS[cite: 1]
    if toplam_gol >= 5: return 0.0, [], "KAOS_ESIGI"
    if fark >= 3: return 0.0, [], "OLUM_BOLGESI"
    
    puan = 0.0; detay = []

    # ALTIN PENCERE[cite: 1]
    if 55 <= dk <= 60:
        puan += 4.0; detay.append("ALTIN PENCERE (55-60') +4.0")
    elif 60 < dk <= 75:
        puan += 2.0; detay.append("GECIS OYUNU EVRESI +2.0")

    # OPTIMUM SKOR[cite: 1]
    if (ev_gol, dep_gol) in [(2,1), (1,2), (3,1), (1,3)]:
        puan += 2.0; detay.append(f"OPTIMUM SKOR ({ev_gol}-{dep_gol}) +2.0")

    # SOT CEZA (HÜCUM EPİLASYONU)[cite: 1]
    if toplam_sot <= 8:
        puan += (toplam_sot * 0.25); detay.append(f"MAKUL SUT SEVIYESI ({toplam_sot})")
    else:
        puan -= 1.5; detay.append("HUCUM EPILASYONU (SOT > 8) CEZA!")

    # KORNER VE ATAK[cite: 1]
    if toplam_korner > 12:
        puan -= 1.0; detay.append("ETKISIZ KORNER BASKISI -1.0")
    if toplam_da > 80:
        puan += 1.0; detay.append("YUKSEK TEHLIKELI ATAK +1.0")

    return round(puan, 1), detay, "SUCCESS"

# ================================================
# BİLDİRİM SİSTEMİ
# ================================================
async def bildirim_gonder(bot, mac, puan, detay):
    tavsiye = "💎 ALTIN FIRSAT" if 55 <= mac['dakika'] <= 60 else "🔥 STRATEJIK SINYAL"
    mesaj = (
        f"🤖 {tavsiye}\n"
        f"Maç: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"Dakika: {mac['dakika']} | Lig: {mac['lig']}\n"
        f"--------------------\n"
        f"Puan: {puan}/12\n"
        f"Analiz: " + ", ".join(detay) + "\n"
        f"--------------------\n"
        f"Veri: SOT: {mac['ev_sot']}/{mac['dep_sot']} | Atak: {mac['ev_da']}/{mac['dep_da']}\n"
        f"💡 SIRADAKİ GOL (S)"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
    except Exception as e:
        logger.error(f"Telegram Hatası: {e}")[cite: 2]

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    
    # BAĞLANTI TESTİ: Botun yaşadığını anında doğrula[cite: 3]
    try:
        await bot.send_message(chat_id=CHAT_ID, text="🚀 V9.5 SİSTEM AKTİF\nBağlantı başarılı, bülten taranıyor...")
    except Exception as e:
        logger.error(f"Telegram Başlatma Hatası (Token/ID Kontrol!): {e}")[cite: 2]

    logger.info("Bot Başlatıldı ve Telegram'a Test Mesajı Gönderildi.")[cite: 2]
    
    while True:
        try:
            maclar = await maclari_cek()
            logger.info(f"Tarama Tamamlandı: {len(maclar)} maç incelendi.")[cite: 2]
            
            for mac in maclar:
                puan, detay, durum = strateji_filtrele(mac)
                if puan >= MIN_PUAN and mac['id'] not in bildirim_gonderilen:
                    await bildirim_gonder(bot, mac, puan, detay)
                    bildirim_gonderilen[mac['id']] = puan
        except Exception as e: 
            logger.error(f"Döngü Hatası: {e}")[cite: 2]
        await asyncio.sleep(180) # 3 dakikada bir tarama

if __name__ == "__main__":
    asyncio.run(ana_dongu())
