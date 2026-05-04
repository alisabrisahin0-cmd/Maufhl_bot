import asyncio
import aiohttp
import os
import json
from telegram import Bot

async def main():
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("CHAT_ID")
    betsapi_token = os.getenv("BETSAPI_TOKEN")

    bot = Bot(token=token)
    await bot.send_message(chat_id=chat_id, text="🔍 API'den ham veri çekiliyor, isimler kontrol ediliyor...")

    url = f"https://api.betsapi.com/v1/bet365/inplay?token={betsapi_token}"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=15) as resp:
                data = await resp.json()
                results = data.get('results', [])
                
                if not results:
                    await bot.send_message(chat_id=chat_id, text="⚠️ Şu an bültende hiç canlı maç yok.")
                    return

                # İçinde 'stats' (istatistik) olan ilk maçı bulalım
                for match in results:
                    stats = match.get('stats')
                    if stats:
                        ev = match.get('home', {}).get('name', 'Ev')
                        dep = match.get('away', {}).get('name', 'Dep')
                        
                        # Veriyi okunaklı hale getir
                        stats_metni = json.dumps(stats, indent=2)
                        
                        mesaj = (
                            f"✅ VERİ BAŞARIYLA ÇEKİLDİ!\n"
                            f"Maç: {ev} - {dep}\n\n"
                            f"İşte API'nin bize gönderdiği orijinal veri isimleri:\n\n"
                            f"{stats_metni[:3500]}" # Telegram sınırına takılmaması için
                        )
                        await bot.send_message(chat_id=chat_id, text=mesaj)
                        return
                        
                await bot.send_message(chat_id=chat_id, text="Maçlar bulundu ama hiçbirinde 'stats' (istatistik) verisi yok.")
                
        except Exception as e:
            await bot.send_message(chat_id=chat_id, text=f"❌ API Bağlantı Hatası: {e}")

if __name__ == "__main__":
    asyncio.run(main())
