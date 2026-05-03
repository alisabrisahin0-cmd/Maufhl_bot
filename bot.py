"""
V6.2 QUANT MASTER - THE ULTIMATE SURVIVOR
Özellikler: Hard Quota Limit (Max 60 matches), 10 Min Cycle, 1800/hr Safety
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
    
    # 3.0 Barajı
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
# VERİ MOTORU (KOTA KORUMALI)
# ================================================
async def mac_detay_cek(session, fixture_id):
    try:
        url = f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={fixture_id}"
        async with session.get(url, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data['results'][0] if data.get('success') == 1 and data.get('results') else None
            elif resp.status == 429:
                return "LIMIT_HIT"
    except: return None

async def maclari_cek():
    maclar = []
    async with aiohttp.ClientSession() as session:
        list_url = f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1"
        async with session.get(list_url, timeout=20) as resp:
            data = await resp.json()
            if data.get('success') == 0:
                logger.error(f"❌ BetsAPI Hatası: {data.get('error')}")
                return []
            
            raw_results = data.get('results', [])
            if raw_results and isinstance(raw_results[0], list): raw_results = raw_results[0]
            
            # KESKİN SINIR: Saatte 1800 istek sınırına takılmamak için tur başına en aktif 60 maçı seç
            # 10 dakikada bir tarama (saatte 6 tur) x 60 maç = saatte 360 istek (Çok Güvenli)
            adaylar = raw_results[:60] 
            logger.info(f"📊 {len(raw_results)} maç arasından en aktif 60 tanesi süzülüyor...")

            for f in adaylar:
                m_id = str(f.get('ID', f.get('id', f.get('FI', ''))))
                detay = await mac_detay_cek(session, m_id)
                
                if detay == "LIMIT_HIT":
                    logger.warning("🚫 Kota bitti, tur sonlandırılıyor.")
                    break
                
                if detay:
                    try:
                        timer = detay.get('timer', {})
                        dk = int(timer.get('tm', 0)) if isinstance(timer, dict) else 0
                        if not (5 <= dk <= 88): continue
                        
                        stats = detay.get('stats', {}); skor = detay.get('ss', '0-0')
                        ev_gol, dep_gol = map(int, skor.split('-')) if '-' in skor else (0, 0)
                        
                        maclar.append({
                            'id': m_id, 'ev': detay.get('home', {}).get('name'), 'dep': detay.get('away', {}).get('name'),
                            'lig': detay.get('league', {}).get('name', 'Lig'), 'dakika': dk, 
                            'ev_gol': ev_gol, 'dep_gol': dep_gol,
                            'shots_on_target_ev': int(stats.get('on_target', [0,0])[0]),
                            'shots_on_target_dep': int(stats.get('on_target', [0,0])[1]),
                            'dangerous_attacks_ev': int(stats.get('dangerous_attacks', [0,0])[0]),
                            'dangerous_attacks_dep': int(stats.get('dangerous_attacks', [0,0])[1])
                        })
                        await asyncio.sleep(2.0) # Her istek arası 2 saniye nefes al
                    except: continue
    return maclar

async def ana_dongu():
    threading.Thread(target=run_health_check, daemon=True).start()
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🛡️ V6.2 SURVIVOR AKTİF\n✅ Hedef: 1800 Limit Koruma\n📊 Kapsam: En Aktif 60 Maç")
    while True:
        try:
            maclar = await maclari_cek()
            for mac in maclar:
                if mac['id'] in bildirim_gonderilen: continue
                puan, detay_list, gecti = sinyal_hesapla(mac)
                if gecti and puan >= MIN_PUAN:
                    await bildirim_gonder(bot, mac, puan, detay_list)
                    bildirim_gonderilen[mac['id']] = True
        except Exception as e: logger.error(f"Döngü hatası: {e}")
        # Tarama aralığını 10 dakikaya çıkardık (Limit koruması için kritik)
        await asyncio.sleep(600)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
