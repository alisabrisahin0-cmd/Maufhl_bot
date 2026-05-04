# MAC ANALIZ BOTU - V9.0-DEEP-DEBUG (DERİN ANALİZ)
# AMAÇ: Botun neden sustuğunu ve verinin nerede takıldığını bulmak.

import asyncio
import aiohttp
from telegram import Bot
import logging
import os

# Telegram ve API Bilgileri
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

async def mac_detay_test(session, fixture_id):
    url = f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={fixture_id}&stats=1"
    try:
        async with session.get(url, timeout=15) as resp:
            data = await resp.json()
            if resp.status == 200 and data.get('success') == 1:
                return data.get('results', [{}])[0]
            else:
                logger.error(f"Detay Hatası: {data}")
    except Exception as e:
        logger.error(f"Bağlantı Hatası: {e}")
    return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    
    # 1. ADIM: Botun yaşadığını teyit edelim
    try:
        await bot.send_message(chat_id=CHAT_ID, text="🔎 DERİN ANALİZ BAŞLADI\nBot şu an canlı maç listesini kontrol ediyor...")
    except Exception as e:
        logger.error(f"Telegram Mesaj Hatası: {e}")

    async with aiohttp.ClientSession() as session:
        while True:
            list_url = f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1"
            try:
                async with session.get(list_url, timeout=20) as resp:
                    data = await resp.json()
                    results = data.get('results', [])
                    
                    # 2. ADIM: Maç listesi boş mu kontrolü
                    if not results:
                        await bot.send_message(chat_id=CHAT_ID, text="📭 Şu an canlı maç listesi boş (Bülten yok).")
                    else:
                        # Bazı API versiyonlarında veri results[0] içinde gelir
                        if isinstance(results[0], list):
                            results = results[0]
                        
                        await bot.send_message(chat_id=CHAT_ID, text=f"📊 Toplam {len(results)} canlı maç bulundu. İlk 2'si analiz ediliyor...")

                        for f in results[:2]:
                            m_id = str(f.get('ID', f.get('FI', '')))
                            ham_veri = await mac_detay_test(session, m_id)
                            
                            if ham_veri:
                                ev = "Bilinmiyor"; dep = "Bilinmiyor"; stats_durumu = "❌ İstatistik Yok"
                                
                                # Veri tarama
                                for item in ham_veri:
                                    if item.get('type') == 'EV':
                                        ev = item.get('NA', '').split(' v ')[0]
                                        dep = item.get('NA', '').split(' v ')[1]
                                    elif item.get('type') == 'SC' and item.get('NA') == 'IShotsOnTarget':
                                        stats_durumu = "✅ İstatistikler Akıyor (&stats=1 Aktif)"

                                rapor = (
                                    f"📝 TEST SONUCU\n"
                                    f"Maç: {ev} - {dep}\n"
                                    f"ID: {m_id}\n"
                                    f"Durum: {stats_durumu}\n"
                                    f"────────────────────"
                                )
                                await bot.send_message(chat_id=CHAT_ID, text=rapor)
            
            except Exception as e:
                logger.error(f"Ana Döngü Hatası: {e}")
                await bot.send_message(chat_id=CHAT_ID, text=f"❌ HATA: {str(e)}")
            
            await asyncio.sleep(60) # Test için 1 dakikaya düşürdüm

if __name__ == "__main__":
    asyncio.run(ana_dongu())
