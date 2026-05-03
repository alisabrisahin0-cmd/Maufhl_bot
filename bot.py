"""
V5.5 QUANT MASTER - DIAGNOSIS & FORCE MODE
Özellikler: Data Structure Logger, Flexible Sport Filter, Turbo Batch Processing
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
gol_hafizasi = {} 

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"Bot Aktif")

def run_health_check():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

def aktif_mi():
    # Mesai: 13:00 - 00:00 (Adana Saati)
    return 13 <= datetime.now().hour <= 23

# ================================================
# ANALİZ VE BİLDİRİM
# ================================================
def sinyal_hesapla(mac):
    mac_id = mac['id']; dakika = max(mac.get('dakika', 1), 1)
    ev_gol = mac.get('ev_gol', 0); dep_gol = mac.get('dep_gol', 0)
    
    suanki_tehlikeli = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    suanki_sut = mac.get('shots_on_target_ev', 0) + mac.get('shots_on_target_dep', 0)
    
    ilk_tarama = mac_id not in mac_gecmisi 
    gecmis = mac_gecmisi.get(mac_id, {'atak': suanki_tehlikeli, 'sut': suanki_sut})
    delta_atak = max(0, suanki_tehlikeli - gecmis['atak'])
    delta_sut = max(0, suanki_sut - gecmis['sut'])
    mac_gecmisi[mac_id] = {'atak': suanki_tehlikeli, 'sut': suanki_sut}
    
    # 3.0 Barajı için ivme kontrolü
    if not ilk_tarama and delta_atak < 4 and delta_sut < 1 and dakika > 20: return 0, [], False

    puan = 4.0 if not ilk_tarama else 2.0
    puan += (suanki_sut * 0.5)
    return round(puan, 1), [f"Atak: +{delta_atak}", f"Şut: +{delta_sut}"], True

async def bildirim_gonder(bot, mac, puan, detay):
    mesaj = (f"⚽ {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
             f
