# MAC ANALIZ BOTU - V9.6 THE RAW TRUTH (HAM VERİ KONTROLÜ)
# AMAÇ: Filtreleme yapmadan sadece stats verisinin (SOT, DA, Korner) akışını test etmek.

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
    # DIRECT INPLAY: Tüm istatistikleri (stats) tek pakette getiren en güvenli nokta
    url = f"https://api.betsapi.com/v1/bet365/inplay?token={BETSAPI_TOKEN}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=20) as resp:
                data = await resp.json()
                return data.get('results', [])
        except Exception as e:
            logger.error(f"API Hatası: {e}")[cite: 2]
    return []

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    
    # Botun başladığını teyit et[cite: 3]
    try:
        await bot.send_message(chat_id=CHAT_ID, text="📡 HAM VERİ MODU AKTİF\nKurallar devre dışı, sadece istatistik akışı kontrol ediliyor...")
    except: pass

    while True:
        results = await ham_veri_cek()
        
        if not results:
            logger.warning("Bülten boş, maç bulunamadı.")[cite: 2]
        else:
            # Spam olmaması için bültendeki ilk 5 maçı detaylı raporla
            for f in results[:5]:
                try:
                    ev = f.get('home', {}).get('name', 'N/A')
                    dep = f.get('away', {}).get('name', 'N/A')
                    dk = f.get('timer', {}).get('tm', 0)
                    skor = f.get('ss', '0-0')
                    
                    # Ham İstatistikler
                    stats = f.get('stats', {})
                    sot = stats.get('on_target', [0, 0]) # [Ev, Dep]
                    da = stats.get('dangerous_attacks', [0, 0]) # [Ev, Dep]
                    korner = stats.get('corners', [0, 0]) # [Ev, Dep]
                    
                    rapor = (
                        f"📊 MAÇ RAPORU\n"
                        f"⚽ {ev} - {dep}\n"
                        f"⏱ Dakika: {dk} | Skor: {skor}\n"
                        f"--------------------\n"
                        f"🎯 İsabetli Şut (SOT): {sot[0]} - {sot[1]}\n"
                        f"🚀 Tehlikeli Atak (DA): {da[0]} - {da[1]}\n"
                        f"🚩 Korner: {korner[0]} - {korner[1]}\n"
                        f"--------------------"
                    )
                    await bot.send_message(chat_id=CHAT_ID, text=rapor)
                    await asyncio.sleep(2) # Telegram limit koruması
                except: continue
        
        # 5 dakikada bir ham veri kontrolü
        await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
