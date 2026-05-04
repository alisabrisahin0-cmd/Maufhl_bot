# MAC ANALIZ BOTU - V9.0-ID_FIX (ID VE PARAMETRE DÜZELTİLMİŞ)
# AMAÇ: 'PARAM_INVALID FI' hatasını gidermek ve stats verisini çekmek.

import asyncio
import aiohttp
from telegram import Bot
import logging
import os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

async def mac_detay_test(session, fixture_id):
    # FI Boşsa hiç istek yapma
    if not fixture_id or fixture_id == "None":
        return None
        
    url = f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={fixture_id}&stats=1"
    try:
        async with session.get(url, timeout=15) as resp:
            data = await resp.json()
            if data.get('success') == 1:
                return data.get('results', [{}])[0]
            else:
                # Hatayı Telegram'a bas ki ne olduğunu görelim
                logger.error(f"Detay Hatası (ID: {fixture_id}): {data}")
    except Exception as e:
        logger.error(f"Bağlantı Hatası: {e}")
    return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    
    try:
        await bot.send_message(chat_id=CHAT_ID, text="🚀 ID DÜZELTME MODU AKTİF\nMaç ID'leri doğrulanıyor...")
    except: pass

    async with aiohttp.ClientSession() as session:
        while True:
            list_url = f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1"
            try:
                async with session.get(list_url, timeout=20) as resp:
                    data = await resp.json()
                    results = data.get('results', [])
                    
                    # Veri katmanlarını temizle
                    if results and isinstance(results[0], list):
                        results = results[0]
                        
                    if not results:
                        await bot.send_message(chat_id=CHAT_ID, text="📭 Şu an bültende maç bulunamadı.")
                    else:
                        # İlk 3 maçı derinlemesine inceleyelim
                        for f in results[:3]:
                            # ID varyasyonlarını tek tek kontrol et
                            m_id = str(f.get('ID') or f.get('FI') or f.get('id') or "")
                            
                            if not m_id:
                                logger.warning("Maç ID bulunamadı, atlanıyor.")
                                continue

                            ham_veri = await mac_detay_test(session, m_id)
                            
                            if ham_veri:
                                ev = "N/A"; dep = "N/A"; stats_ok = "❌ Veri Yok"
                                
                                for item in ham_veri:
                                    if item.get('type') == 'EV':
                                        ev = item.get('NA', '').split(' v ')[0]
                                        dep = item.get('NA', '').split(' v ')[1]
                                    elif item.get('type') == 'SC' and item.get('NA') == 'IShotsOnTarget':
                                        stats_ok = "✅ İstatistikler Akıyor"

                                rapor = (
                                    f"✅ BAĞLANTI BAŞARILI\n"
                                    f"Maç: {ev} - {dep}\n"
                                    f"ID: {m_id}\n"
                                    f"İstatistik: {stats_ok}\n"
                                    f"────────────────────"
                                )
                                await bot.send_message(chat_id=CHAT_ID, text=rapor)
                                await asyncio.sleep(2)
                                
            except Exception as e:
                logger.error(f"Ana Döngü Hatası: {e}")
            
            await asyncio.sleep(120) # 2 dakikada bir kontrol

if __name__ == "__main__":
    asyncio.run(ana_dongu())
