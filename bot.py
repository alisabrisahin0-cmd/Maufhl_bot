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
    await bot.send_message(chat_id=chat_id, text="🚀 DESTEK EKİBİ ONAYLI TEST BAŞLIYOR...\nEn taze maç bulunup stats=1 ile sorgulanacak.")

    async with aiohttp.ClientSession() as session:
        try:
            # Güncel listeyi çek
            list_url = f"https://api.betsapi.com/v3/bet365/inplay_filter?token={betsapi_token}&sport_id=1"
            async with session.get(list_url, timeout=15) as resp:
                data = await resp.json()
                results = data.get('results', [])
                
                # Liste içindeki listeyi düzelt
                if results and isinstance(results[0], list):
                    results = results[0]

                if not results:
                    await bot.send_message(chat_id=chat_id, text="⚠️ Şu an canlı bültende hiç maç yok.")
                    return

                # En taze ilk maçı al
                ilk_mac = results[0]
                taze_id = ilk_mac.get('FI') or ilk_mac.get('id') or ilk_mac.get('ID')
                ev_isim = ilk_mac.get('home', {}).get('name', 'Ev')
                dep_isim = ilk_mac.get('away', {}).get('name', 'Dep')

                if not taze_id:
                    await bot.send_message(chat_id=chat_id, text="❌ Maç bulundu ama ID alınamadı.")
                    return

                await bot.send_message(chat_id=chat_id, text=f"✅ Taze ID Bulundu: {taze_id}\nMaç: {ev_isim} - {dep_isim}\nŞimdi stats=1 ile detay isteniyor...")

                # Onaylanan URL ile istatistikleri çek
                event_url = f"https://api.betsapi.com/v3/bet365/event?token={betsapi_token}&FI={taze_id}&stats=1"
                
                async with session.get(event_url, timeout=15) as event_resp:
                    event_data = await event_resp.json()
                    
                    if event_data.get('success') == 1:
                        ham_detay = event_data.get('results', [{}])[0]
                        detay_metni = json.dumps(ham_detay, indent=2)
                        
                        mesaj = (
                            f"🎯 ZAFER!\n"
                            f"Veriler başarıyla aktı!\n\n"
                            f"{detay_metni[:3500]}"
                        )
                        await bot.send_message(chat_id=chat_id, text=mesaj)
                    else:
                        await bot.send_message(chat_id=chat_id, text=f"❌ Event Hatası:\n{event_data}")

        except Exception as e:
            await bot.send_message(chat_id=chat_id, text=f"❌ Sistem Hatası: {e}")

if __name__ == "__main__":
    asyncio.run(main())

