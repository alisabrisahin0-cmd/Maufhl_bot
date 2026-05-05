# MAC ANALIZ BOTU - V18.1 SİSTEM KONTROL (TAMİR EDİLDİ)
# Hata Çözümü: 'list object has no attribute get' hatası esnek liste çözücüyle giderildi.

import asyncio
import aiohttp
from telegram import Bot
import os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

# Katmanları otomatik soyan esnek fonksiyon
def esnek_liste_duzelt(veri):
    duz_liste = []
    if isinstance(veri, list):
        for eleman in veri:
            duz_liste.extend(esnek_liste_duzelt(eleman))
    elif isinstance(veri, dict):
        duz_liste.append(veri)
    return duz_liste

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🔧 V18.1 KONTROL: Veri tipi hatası giderildi, rapor hazırlanıyor...")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                data = await r.json()
                res = esnek_liste_duzelt(data.get('results', []))
            
            if res and len(res) > 0:
                m_id = res[0].get('id') or res[0].get('FI')
                
                if m_id:
                    async with session.get(f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={m_id}&stats=1") as er:
                        e_data = await er.json()
                        # Event içindeki listeyi de esnek şekilde düzeltiyoruz
                        results = esnek_liste_duzelt(e_data.get('results', []))
                        
                        report = "📋 SİSTEM VERİ KONTROL RAPORU\n\n"
                        for item in results:
                            t = item.get('type')
                            if t == 'EV':
                                report += f"⚽ Maç: {item.get('NA')}\n⏱ Dakika: {item.get('TM')}\n🥅 Skor: {item.get('SS')}\n\n"
                            elif t == 'TE':
                                side = "EV SAHİBİ" if str(item.get('ID')) == '1' else "DEPLASMAN"
                                report += (f"📍 {side} VERİLERİ:\n"
                                          f"S1 (İsabetli Şut): {item.get('S1')}\n"
                                          f"S2 (Korner): {item.get('S2')}\n"
                                          f"S3 (Sarı Kart): {item.get('S3')}\n"
                                          f"S4 (Tehlikeli Atak): {item.get('S4')}\n"
                                          f"S5 (Kırmızı Kart): {item.get('S5')}\n"
                                          f"S6 (Dışarı Şut): {item.get('S6')}\n"
                                          f"S7 (Topla Oynama %): {item.get('S7')}\n"
                                          f"S8 (Ataklar): {item.get('S8')}\n"
                                          f"S11 (Saves/Kurtarış): {item.get('S11')}\n"
                                          f"S13 (Serbest Vuruş): {item.get('S13')}\n"
                                          f"S14 (Kale Vuruşu): {item.get('S14')}\n"
                                          f"--------------------\n")
                        
                        await bot.send_message(chat_id=CHAT_ID, text=report)
                else:
                    await bot.send_message(chat_id=CHAT_ID, text="❌ Maç ID bulunamadı.")
            
        except Exception as e: 
            await bot.send_message(chat_id=CHAT_ID, text=f"💥 Hata Devam Ediyor: {e}")

if __name__ == "__main__":
    asyncio.run(ana_dongu())
