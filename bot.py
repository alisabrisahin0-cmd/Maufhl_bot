# MAC ANALIZ BOTU - V14.3 DERİN RÖNTGEN
# Yenilik: BetsAPI'nin neden boş veri gönderdiğini anlamak için ham sunucu yanıtı Telegram'a basılacak.

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
    await bot.send_message(chat_id=CHAT_ID, text="🕵️‍♂️ V14.3 DERİN RÖNTGEN: BetsAPI'nin gizli yanıtı çekiliyor...")
    
    async with aiohttp.ClientSession() as session:
        try:
            # 1. Adım: Maç listesini al
            async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                data = await r.json()
                res = data.get('results', [])
            
            if res and isinstance(res, list) and len(res) > 0 and isinstance(res[0], list):
                res = res[0]

            # 2. Adım: Sadece İLK maçın kimliğini al ve detayını sor
            hedef_id = None
            for m in res:
                if isinstance(m, dict):
                    hedef_id = m.get('FI') or m.get('ID')
                    if hedef_id and str(hedef_id).lower() != "none":
                        break
            
            if hedef_id:
                await bot.send_message(chat_id=CHAT_ID, text=f"Hedef Maç ID bulundu: {hedef_id}. Sunucuya soruluyor...")
                
                async with session.get(f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={hedef_id}&stats=1") as er:
                    e_data = await er.json()
                    
                    # 3. Adım: BetsAPI'nin verdiği ham cevabı (hata veya veri) Telegram'a bas
                    ham_cevap_metni = json.dumps(e_data, indent=2, ensure_ascii=False)[:3000]
                    await bot.send_message(chat_id=CHAT_ID, text=f"🚨 BetsAPI HAM YANIT:\n```json\n{ham_cevap_metni}\n```", parse_mode="Markdown")
            else:
                await bot.send_message(chat_id=CHAT_ID, text="❌ Hata: Listede geçerli bir maç ID'si bulunamadı.")
                
        except Exception as e: 
            await bot.send_message(chat_id=CHAT_ID, text=f"💥 Sistem Hatası: {e}")

if __name__ == "__main__":
    asyncio.run(ana_dongu())
