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
    await bot.send_message(chat_id=chat_id, text="🔍 API Veri Yapısı Çözülüyor...")

    url = f"https://api.betsapi.com/v1/bet365/inplay?token={betsapi_token}"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=15) as resp:
                data = await resp.json()
                
                # API liste mi gönderdi kutu mu? Otomatik ayıklayıcı:
                results = []
                if isinstance(data, dict):
                    results = data.get('results', [])
                elif isinstance(data, list):
                    results = data
                    
                # BetsAPI bazen veriyi iç içe liste olarak gizler: [ [mac1, mac2] ]
                if results and isinstance(results[0], list):
                    results = results[0]

                if not results:
                    await bot.send_message(chat_id=chat_id, text="⚠️ Bültende canlı maç yok veya veri boş.")
                    return

                # İçinde 'stats' olan ilk maçı bul
                for match in results:
                    if isinstance(match, dict) and match.get('stats'):
                        ev = match.get('home', {}).get('name', 'Ev')
                        dep = match.get('away', {}).get('name', 'Dep')
                        stats = match.get('stats')
                        
                        stats_metni = json.dumps(stats, indent=2)
                        mesaj = (
                            f"✅ KİLİT AÇILDI!\n"
                            f"Maç: {ev} - {dep}\n\n"
                            f"İşte API'nin bize gönderdiği orijinal veri isimleri:\n\n"
                            f"{stats_metni[:3500]}"
                        )
                        await bot.send_message(chat_id=chat_id, text=mesaj)
                        return
                        
                await bot.send_message(chat_id=chat_id, text="Maçlar bulundu ama hiçbirinde 'stats' (istatistik) verisi yok.")
                
        except Exception as e:
            await bot.send_message(chat_id=chat_id, text=f"❌ Veri Okuma Hatası: {e}")

if __name__ == "__main__":
    asyncio.run(main())
