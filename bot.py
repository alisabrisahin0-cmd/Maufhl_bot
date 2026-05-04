# MAC ANALIZ BOTU - V9.6-TEST (HAM VERI DOĞRULAYICI)
# Kurallar devre dışı; sadece Tehlikeli Atak, Şut ve Korner akışını test eder.

import asyncio
import aiohttp
from telegram import Bot
import logging
import os

# AYARLAR
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

async def ham_veri_cek():
    # Tek istekte tüm istatistikleri getiren güvenli endpoint
    url = f"https://api.betsapi.com/v1/bet365/inplay?token={BETSAPI_TOKEN}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=20) as resp:
                data = await resp.json()
                return data.get('results', [])
        except Exception as e:
            logger.error(f"API Hatası: {e}")
    return []

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    
    # Başlangıç onayı
    try:
        await bot.send_message(chat_id=CHAT_ID, text="📡 VERİ AKIŞ TESTİ BAŞLADI\nKurallar kapatıldı. Tehlikeli Atak ve Şut verileri kontrol ediliyor...")
    except: pass

    while True:
        results = await ham_veri_cek()
        
        if not results:
            logger.warning("Bültende canlı maç bulunamadı.")
        else:
            # Bültendeki ilk 3 maçı ham verileriyle raporla
            for f in results[:3]:
                try:
                    ev = f.get('home', {}).get('name', 'N/A')
                    dep = f.get('away', {}).get('name', 'N/A')
                    dk = f.get('timer', {}).get('tm', 0)
                    skor = f.get('ss', '0-0')
                    
                    # Ham İstatistiklerin Çekilmesi[cite: 3]
                    stats = f.get('stats', {})
                    sot = stats.get('on_target', [0, 0])
                    da = stats.get('dangerous_attacks', [0, 0])
                    korner = stats.get('corners', [0, 0])
                    
                    rapor = (
                        f"📊 CANLI VERİ RAPORU\n"
                        f"⚽ {ev} - {dep}\n"
                        f"⏱ Dakika: {dk} | Skor: {skor}\n"
                        f"--------------------\n"
                        f"🚀 Tehlikeli Atak (DA): {da[0]} - {da[1]}\n"
                        f"🎯 İsabetli Şut (SOT): {sot[0]} - {sot[1]}\n"
                        f"🚩 Korner: {korner[0]} - {korner[1]}\n"
                        f"--------------------"
                    )
                    await bot.send_message(chat_id=CHAT_ID, text=rapor)
                    await asyncio.sleep(2)
                except: continue
        
        # 3 dakikada bir kontrol et
        await asyncio.sleep(180)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
