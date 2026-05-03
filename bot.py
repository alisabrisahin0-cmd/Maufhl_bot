"""
V5.8 QUANT MASTER - THE STABILIZER
Özellikler: Increased Timeout (30s), Error Recovery, 343 Match Optimization, 3.0 Barajı
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

def aktif_mi():
    return 13 <= datetime.now().hour <= 23

# ================================================
# ANALİZ MOTORU
# ================================================
def sinyal_hesapla(mac):
    mac_id = mac['id']; dakika = max(mac.get('dakika', 1), 1)
    
    suanki_tehlikeli = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    suanki_sut = mac.get('shots_on_target_ev', 0) + mac.get('shots_on_target_dep', 0)
    
    ilk_tarama = mac_id not in mac_gecmisi 
    gecmis = mac_gecmisi.get(mac_id, {'atak': suanki_tehlikeli, 'sut': suanki_sut})
    delta_atak = max(0, suanki_tehlikeli - gecmis['atak'])
    delta_sut = max(0, suanki_sut - gecmis['sut'])
    mac_gecmisi[mac_id] = {'atak': suanki_tehlikeli, 'sut': suanki_sut}
    
    # İvme yoksa bildirim gönderme
    if not ilk_tarama and delta_atak < 4 and delta_sut < 1: return 0, [], False

    puan = 4.0 if not ilk_tarama else 2.0
    puan += (suanki_sut * 0.5)
    return round(puan, 1), [f"Atak: +{delta_atak}", f"Şut: +{delta_sut}"], True

async def bildirim_gonder(bot, mac, puan, detay):
    mesaj = (f"⚽ {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
             f"🏆 {mac['lig']} | ⏱️ {mac['dakika']}. DK\n"
             f"📈 PUAN: {puan}\n📝 {', '.join(detay)}")
    try: await bot.send_message(chat_id=CHAT_ID, text=mesaj)
    except: pass

# ================================================
# VERİ MOTORU (GÜÇLENDİRİLMİŞ BAĞLANTI)
# ================================================
async def mac_detay_cek(session, semaphore, fixture_id):
    async with semaphore:
        try:
            url = f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={fixture_id}"
            # Zaman aşımı 30 saniyeye çıkarıldı
            async with session.get(url, timeout=30) as resp:
                data = await resp.json()
                if data.get('success') == 1 and data.get('results'):
                    res = data['results']
                    return res[0] if isinstance(res, list) and res else res
        except: return None

async def maclari_cek():
    maclar = []
    semaphore = asyncio.Semaphore(5) # Daha yavaş ama daha stabil sorgu
    async with aiohttp.ClientSession() as session:
        list_url = f"https://api.betsapi.com/v3/bet365/inplay?token={BETSAPI_TOKEN}"
        async with session.get(list_url, timeout=20) as resp:
            data = await resp.json()
            raw_results = data.get('results', [])
            if raw_results and isinstance(raw_results[0], list): raw_results = raw_results[0]
            
            adaylar = []
            for f in raw_results:
                if not isinstance(f, dict): continue
                if str(f.get('NA', '')).lower() == 'soccer' or f.get('type') == 'EV':
                    m_id = str(f.get('ID', f.get('id', f.get('FI', ''))))
                    if m_id: adaylar.append(m_id)

            logger.info(f"📊 {len(adaylar)} Futbol maçı derin incelemeye alınıyor...")

            tasks = [mac_detay_cek(session, semaphore, m_id) for m_id in adaylar]
            detaylar = await asyncio.gather(*tasks)

            for detay in detaylar:
                if not detay or not isinstance(detay, dict): continue
                try:
                    timer = detay.get('timer', {})
                    dk = int(timer.get('tm', 0)) if isinstance(timer, dict) else 0
                    if not (5 <= dk <= 88): continue
                    
                    stats = detay.get('stats', {}); skor = detay.get('ss', '0-0')
                    ev_gol, dep_gol = map(int, skor.split('-')) if '-' in skor else (0, 0)
                    
                    def gs(key, idx):
                        v = stats.get(key, [0, 0])
                        return int(v[idx]) if isinstance(v, list) and len(v) > idx else 0

                    maclar.append({
                        'id': str(detay.get('id', '')), 'ev': detay.get('home', {}).get('name'), 
                        'dep': detay.get('away', {}).get('name'), 'lig': detay.get('league', {}).get('name', 'Lig'), 
                        'dakika': dk, 'ev_gol': ev_gol, 'dep_gol': dep_gol,
                        'shots_on_target_ev': gs('on_target', 0), 'shots_on_target_dep': gs('on_target', 1),
                        'dangerous_attacks_ev': gs('dangerous_attacks', 0), 'dangerous_attacks_dep': gs('dangerous_attacks', 1)
                    })
                except: continue
    return maclar

async def ana_dongu():
    threading.Thread(target=run_health_check, daemon=True).start()
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="⚙️ V5.8 STABILIZER AKTİF - TIMEOUT FIX UYGULANDI")
    while True:
        if aktif_mi():
            try:
                maclar = await maclari_cek()
                for mac in maclar:
                    if mac['id'] in bildirim_gonderilen: continue
                    puan, detay_list, gecti = sinyal_hesapla(mac)
                    if gecti and puan >= MIN_PUAN:
                        await bildirim_gonder(bot, mac, puan, detay_list)
                        bildirim_gonderilen[mac['id']] = True
            except Exception as e:
                logger.error(f"Ana döngü hatası: {e}")
        await asyncio.sleep(180)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
