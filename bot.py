# MAC ANALIZ BOTU - V24.1 NABIZ (LOG DESTEKLİ)
# Yenilik: Railway loglarına her taramada 'Scanning...' yazar, kilitlenmeleri raporlar.

import asyncio
import aiohttp
from telegram import Bot
import os
import traceback

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🛰 V24.1 NABIZ: Sistem uyandı. Railway LOG sekmesini kontrol edin.")
    
    async with aiohttp.ClientSession() as session:
        sayac = 0
        while True:
            try:
                sayac += 1
                # Railway Loglarına yaz (Bu mesajları Railway panelinde göreceksiniz)
                print(f">>> [DÖNGÜ {sayac}] BetsAPI taranıyor...") 
                
                async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                    data = await r.json()
                    res = data.get('results', [])
                    print(f"--- Toplam {len(res)} maç bulundu. Filtreler uygulanıyor...")

                # Burada analiz ve sinyal gönderme kodları yer alacak...
                # (Sinyal gelirse Telegram'a atacak, gelmezse sessizce loglara yazmaya devam edecek)

            except Exception as e:
                print(f"!!! HATA OLUŞTU: {e}")
                traceback.print_exc()
            
            await asyncio.sleep(60) # Her 1 dakika bir loglara yazması lazım

if __name__ == "__main__":
    asyncio.run(ana_dongu())
