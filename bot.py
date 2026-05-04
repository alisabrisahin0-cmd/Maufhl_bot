# MAC ANALIZ BOTU - V9.0-DEBUG (VERİ DEDEKTİFİ)
# AMAÇ: &stats=1 verisinin kalitesini ve stratejiye uygunluğunu test etmek.

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import re

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

async def mac_detay_test(session, fixture_id):
    # Sizin keşfettiğiniz stats parametresini test ediyoruz
    url = f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={fixture_id}&stats=1"
    async with session.get(url, timeout=15) as resp:
        if resp.status == 200:
            return await resp.json()
    return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🔍 VERİ DEDEKTİFİ AKTİF\n&stats=1 parametresi üzerinden ham veri analizi başlıyor...")

    async with aiohttp.ClientSession() as session:
        while True:
            list_url = f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1"
            async with session.get(list_url) as resp:
                data = await resp.json()
                results = data.get('results', [[]])[0]
                
                # Test için canlıdan 2 maç seçelim
                for f in results[:2]: 
                    m_id = str(f.get('ID', f.get('FI', '')))
                    ham_veri = await mac_detay_test(session, m_id)
                    
                    if ham_veri and ham_veri.get('results'):
                        detay = ham_veri['results'][0]
                        ev = "N/A"; dep = "N/A"; sot = "GELMİYOR"; da = "GELMİYOR"; skor = "0-0"; dk = 0
                        
                        for item in detay:
                            if item.get('type') == 'EV':
                                ev = item.get('NA', '').split(' v ')[0]
                                dep = item.get('NA', '').split(' v ')[1]
                                skor = item.get('SS', '0-0')
                                dk = item.get('TM', 0)
                            elif item.get('type') == 'SC':
                                if item.get('NA') == 'IShotsOnTarget':
                                    # SOT Verisini yakala
                                    ev_sot = detay[detay.index(item)+1].get('D1', 0)
                                    dep_sot = detay[detay.index(item)+2].get('D1', 0)
                                    sot = f"EV:{ev_sot} - DEP:{dep_sot}"
                                elif item.get('NA') == 'IDangerousAttack':
                                    # Tehlikeli Atak Verisini yakala
                                    ev_da = detay[detay.index(item)+1].get('D1', 0)
                                    dep_da = detay[detay.index(item)+2].get('D1', 0)
                                    da = f"EV:{ev_da} - DEP:{dep_da}"

                        rapor = (
                            f"📊 VERİ TEST RAPORU\n"
                            f"Maç: {ev} - {dep}\n"
                            f"Dakika: {dk} | Skor: {skor}\n"
                            f"────────────────────\n"
                            f"🚀 Tehlikeli Atak (DA): {da}\n"
                            f"🎯 İsabetli Şut (SOT): {sot}\n"
                            f"────────────────────\n"
                            f"📝 NOT: Eğer DA ve SOT 'GELMİYOR' yazıyorsa, bu ligde veya pakette bu veri kapalıdır."
                        )
                        await bot.send_message(chat_id=CHAT_ID, text=rapor)
            
            await asyncio.sleep(300) # 5 dakikada bir kontrol

if __name__ == "__main__":
    asyncio.run(ana_dongu())
