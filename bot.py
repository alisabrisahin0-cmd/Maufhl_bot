# MAC ANALIZ BOTU - V9.2 THE KEYMASTER (ID ANAHTAR DÜZELTME)
# AMAÇ: FI ve ID karmaşasını çözüp stats verisine %100 erişim sağlamak.

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import json

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

async def mac_detay_test(session, fi_id):
    if not fi_id: return None
    
    # KESİN ÇÖZÜM: Parametre olarak sadece doğrulanan FI kullanılacak
    url = f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={fi_id}&stats=1"
    try:
        async with session.get(url, timeout=15) as resp:
            data = await resp.json()
            if data.get('success') == 1:
                return data.get('results', [{}])[0]
            else:
                # Hala hata alıyorsak loglayalım
                logger.error(f"⚠️ API Reddetti (FI: {fi_id}): {data.get('error')}")
    except Exception as e:
        logger.error(f"Bağlantı Hatası: {e}")
    return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🔑 KEYMASTER MODU AKTİF\nFI Anahtarları öncelikli olarak taranıyor...")

    async with aiohttp.ClientSession() as session:
        while True:
            list_url = f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1"
            try:
                async with session.get(list_url, timeout=20) as resp:
                    data = await resp.json()
                    results = data.get('results', [])
                    
                    if results and isinstance(results[0], list):
                        results = results[0]
                        
                    if not results:
                        await bot.send_message(chat_id=CHAT_ID, text="📭 Bülten şu an boş.")
                    else:
                        # İlk 2 maçı analiz edelim
                        for f in results[:2]:
                            # KRİTİK DEĞİŞİKLİK: Önce FI anahtarına bak, yoksa diğerlerine geç
                            # BetsAPI event detayında 'FI' parametresi için 'FI' verisi şarttır.
                            fi_id = str(f.get('FI') or f.get('ID') or f.get('id') or "")
                            
                            if not fi_id: continue

                            ham_veri = await mac_detay_test(session, fi_id)
                            
                            if ham_veri:
                                ev = "N/A"; dep = "N/A"; stats_durumu = "❌ İstatistik Yok"
                                
                                for item in ham_veri:
                                    if item.get('type') == 'EV':
                                        ev = item.get('NA', '').split(' v ')[0]
                                        dep = item.get('NA', '').split(' v ')[1]
                                    elif item.get('type') == 'SC' and item.get('NA') == 'IShotsOnTarget':
                                        stats_durumu = "✅ İstatistikler Akıyor"

                                rapor = (
                                    f"🎯 DOĞRU ANAHTAR BULUNDU\n"
                                    f"Maç: {ev} - {dep}\n"
                                    f"FI No: {fi_id}\n"
                                    f"Durum: {stats_durumu}\n"
                                    f"────────────────────"
                                )
                                await bot.send_message(chat_id=CHAT_ID, text=rapor)
                                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Hata: {e}")
            
            await asyncio.sleep(120)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
