"""
MAC ANALIZ BOTU - GEMINI AI ENTEGRASYONU
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
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Log ve Çevre Değişkenleri
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "6"))

# Railway Port Kandırmacası (Health Check)
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Aktif")

def run_health_check():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# Global Değişkenler
bildirim_gonderilen = {}
db_pool = None
API_HEADERS = {
    "x-apisports-key": APISPORTS_KEY,
    "x-apisports-host": "v3.football.api-sports.io"
}
BASE_URL = "https://v3.football.api-sports.io"

# ================================================
# GEMİNİ AI — ÖZGÜN ANALİZ FONKSİYONU
# ================================================
async def gemini_analiz(mac, puan, strateji, tahmin):
    if not GEMINI_KEY:
        return "AI aktif değil.", 1.5

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    
    prompt = f"""Sen deneyimli bir bahis analistisin. 
    MAÇ: {mac['ev']} vs {mac['dep']} | LİG: {mac['lig']} | DK: {mac['dakika']}
    SKOR: {mac['ev_gol']}-{mac['dep_gol']}
    İSTATİSTİKLER: Şut {mac['shots_on_target_ev']}/{mac['shots_on_target_dep']}, Top %{mac['possession_ev']}
    BOT TAHMİNİ: {tahmin}
    
    GÖREV: Bu verilere dayanarak, "istatistikler şunu diyor" gibi kalıplar kullanmadan, maça özel 2-3 cümlelik keskin bir yorum yap. 
    Cevabı şu JSON formatında ver: {{"yorum": "yorumun", "kasa": 1.5}}"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    result = json.loads(text)
                    return result.get('yorum', ''), float(result.get('kasa', 1.5))
    except Exception as e:
        logger.error(f"Gemini Hatası: {e}")
    return "Analiz yapılamadı.", 1.5

# ================================================
# ANA SİSTEM (Senin Mevcut Fonksiyonların)
# ================================================
# Burada senin winning_code_kontrol, sinyal_hesapla ve maclari_cek 
# fonksiyonlarının olduğunu varsayıyorum.

async def ana_dongu():
    # Railway'i kandır
    threading.Thread(target=run_health_check, daemon=True).start()
    
    bot = Bot(token=TELEGRAM_TOKEN)
    logger.info("Bot başlatıldı!")
    await bot.send_message(chat_id=CHAT_ID, text="🚀 Gemini AI Analiz Botu Railway'de Aktif!")

    while True:
        try:
            # Buraya senin mevcut maç çekme ve bildirim mantığın gelecek
            # Örnek kullanım:
            # ai_yorum, ai_kasa = await gemini_analiz(mac, puan, strateji, tahmin)
            # await bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, neden, ai_yorum, ai_kasa)
            
            await asyncio.sleep(420) # 7 dakika
        except Exception as e:
            logger.error(f"Hata: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
