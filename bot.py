"""
V6.6 QUANT MASTER - THE X-RAY
Özellikler: No Filters (All Minutes), Detailed Row Logging, 40 Match Sniper
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

# ================================================
# AYARLAR
# ================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")
MIN_PUAN = float(os.getenv("MIN_PUAN", "3.0")) 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
mac_gecmisi = {} 

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"Bot Aktif")

def run_health_check():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

# ================================================
# ANALİZ MOTORU
# ================================================
def sinyal_hesapla(mac):
    mac_id = mac['id']
    suanki_tehlikeli = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    suanki_sut = mac.get('shots_on_target_ev', 0) + mac.get('shots_on_target_dep', 0)
    
    ilk_tarama = mac_id not in mac_gecmisi 
    gecmis = mac_gecmisi.get(mac_id, {'atak': suanki_tehlikeli, 'sut': suanki_sut})
    delta_atak = max(0, suanki_tehlikeli - gecmis['atak'])
    delta_sut = max(0, suanki_sut - gecmis['sut'])
    mac_gecmisi[mac_id] = {'atak': suanki_tehlikeli, 'sut': suanki_sut}
    
    logger.info(f"🧐 ANALİZ: {mac['ev']} | Dakika: {mac['dakika']} | +{delta_atak} Atak | +{delta_sut} Şut")

    if not ilk_tarama and delta_atak < 4 and delta_sut < 1: return 0, [], False
    puan = 4.0 if not ilk_tarama else 2.0
    puan += (suanki_sut * 0.5)
    return round(puan, 1), [f"Atak: +{delta_atak}", f"Şut: +{delta_sut}"], True

# ================================================
# VERİ MOTORU (FİLTRESİZ)
# ================================================
async def mac_detay_cek(session, fixture_id):
    try:
        url = f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={fixture_id}"
        async with session.get(url, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('success') == 1 and data.get('results'):
                    return data['results'][0]
                else:
                    logger.warning(f"⚠️ {fixture_id} için veri yapısı sorunu: {data.get('error')}")
            elif resp.status == 429: return "LIMIT"
    except: return None
    return None

async def maclari_cek():
    maclar = []
    async with aiohttp.ClientSession() as session:
        list_url = f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1"
        async with session.get(list_url, timeout=20) as resp:
            data = await resp.json()
            raw_results = data.get('results', [])
            if raw_results and isinstance(raw_results[0], list): raw_results = raw_results[0]
            
            adaylar = raw_results[:40] 
            logger.info(f"🎯 X-RAY: {len(raw_results)} maçtan {len(adaylar)} tanesi derin incelemeye alındı...")

            for f in adaylar:
                m_id = str(f.get('ID', f.get('id', f.get('FI', ''))))
                detay = await mac_detay_cek(session, m_id)
                
                if detay == "LIMIT":
                    logger.error("🚫 BetsAPI Limiti Dolu!")
                    break
                
                if detay and isinstance(detay, dict):
                    try:
                        timer = detay.get('timer', {})
                        stats = detay.get('stats', {})
                        skor = str(detay.get('ss', '0-0'))
                        
                        # X-RAY MODU: Hiçbir filtreleme (dakika vb.) yapmadan her şeyi listeye ekle
                        maclar.append({
                            'id': m_id, 'ev': detay.get('home', {}).get('name', 'Bilinmiyor'), 
                            'dep': detay.get('away', {}).get('name', 'Bilinmiyor'), 
                            'lig': detay.get('league', {}).get('name', 'Lig'), 
                            'dakika': int(timer.get('tm', 0)) if isinstance(timer, dict) else 0,
                            'ev_gol': int(skor.split('-')[0]) if '-' in skor else 0,
                            'dep_gol': int(skor.split('-')[1]) if '-' in skor else 0,
                            'shots_on_target_ev': int(stats.get('on_target', [0,0])[0]) if stats else 0,
                            'shots_on_target_dep': int(stats.get('on_target', [0,0])[1]) if stats else 0,
                            'dangerous_attacks_ev': int(stats.get('dangerous_attacks', [0,0])[0]) if stats else 0,
                            'dangerous_attacks_dep': int(stats.get('dangerous_attacks', [0,0])[1]) if stats else 0
                        })
                        await asyncio.sleep(1.5)
                    except Exception as e:
                        logger.error(f"⚠️ Maç verisi işlenemedi: {e}")
    return maclar

async def ana_dongu():
    threading.Thread(target=run_health_check, daemon=True).start()
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🔬 V6.6 X-RAY AKTİF\n🔓 Tüm filtreler kaldırıldı.")
    while True:
        try:
            maclar = await maclari_cek()
            logger.info(f"📊 İşlem Tamam: {len(maclar)} maç analize hazır.")
            for mac in maclar:
                if mac['id'] in bildirim_gonderilen: continue
                puan, detay_list, gecti = sinyal_hesapla(mac)
                if gecti and puan >= MIN_PUAN:
                    mesaj = f"⚽ {mac['ev']} - {mac['dep']}\n📈 Puan: {puan}\n⏱️ {mac['dakika']}. DK\n📝 {', '.join(detay_list)}"
                    await bot.send_message(chat_id=CHAT_ID, text=mesaj)
                    bildirim_gonderilen[mac['id']] = True
        except Exception as e:
            logger.error(f"🔴 Hata: {e}")
        await asyncio.sleep(180)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
