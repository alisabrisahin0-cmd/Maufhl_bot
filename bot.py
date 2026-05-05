# MAC ANALIZ BOTU - V14.4 LİSTE RÖNTGENİ
# Yenilik: BetsAPI'nin gönderdiği inplay listesindeki ilk maçın ham etiketleri inceleniyor.

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import json

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="📦 V14.4 LİSTE RÖNTGENİ: Gelen ilk maçın etiketleri inceleniyor...")
    
    async with aiohttp.ClientSession() as session:
        try:
            # Doğrudan canlı maç listesini çekiyoruz
            async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                data = await r.json()
                res = data.get('results', [])
            
            # İç içe liste bug'ını düzelt
            if res and isinstance(res, list) and len(res) > 0 and isinstance(res[0], list):
                res = res[0]

            if res and len(res) > 0:
                # Listedeki sadece İLK maçın ham verisini JSON formatında Telegram'a gönder
                ornek_mac = json.dumps(res[0], indent=2, ensure_ascii=False)[:3000]
                await bot.send_message(chat_id=CHAT_ID, text=f"🚨 İLK MAÇIN HAM ETİKETLERİ:\n```json\n{ornek_mac}\n```", parse_mode="Markdown")
            else:
                await bot.send_message(chat_id=CHAT_ID, text="❌ Hata: BetsAPI'den gelen inplay_filter listesi tamamen boş!")
                
        except Exception as e: 
            await bot.send_message(chat_id=CHAT_ID, text=f"💥 Sistem Hatası: {e}")

if __name__ == "__main__":
    asyncio.run(ana_dongu())
