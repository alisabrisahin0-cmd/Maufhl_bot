# MAC ANALIZ BOTU - V9.0-DEBUG (HATASIZ VERİ DEDEKTİFİ)
# AMAÇ: &stats=1 verisinin kalitesini ve stratejiye uygunluğunu test etmek.

import asyncio
import aiohttp
from telegram import Bot
import logging
import os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

# Loglama ayarları
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

async def mac_detay_test(session, fixture_id):
    # Stats parametresini test etmek için asıl URL burası
    url = f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={fixture_id}&stats=1"
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('success') == 1 and data.get('results'):
                    return data['results'][0]
    except Exception as e:
        logger.error(f"Detay çekme hatası: {e}")
    return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    logger.info("🔍 Veri Dedektifi Başlatılıyor...")
    
    try:
        await bot.send_message(chat_id=CHAT_ID, text="🔍 VERİ DEDEKTİFİ AKTİF\n&stats=1 parametresi üzerinden ham veri analizi başlıyor...")
    except Exception as e:
        logger.error(f"Telegram başlangıç mesajı hatası: {e}")

    async with aiohttp.ClientSession() as session:
        while True:
            list_url = f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1"
            try:
                async with session.get(list_url, timeout=20) as resp:
                    data = await resp.json()
                    results = data.get('results', [])
                    
                    # Eğer results bir liste değilse listeye çevir (Hata düzeltmesi burası)
                    if not isinstance(results, list):
                        results = [results] if results else []

                    # Test için listedeki ilk 2 maçı alalım
                    test_adaylari = results[:2]
                    
                    for f in test_adaylari: 
                        m_id = str(f.get('ID', f.get('FI', '')))
                        ham_veri = await mac_detay_test(session, m_id)
                        
                        if ham_veri:
                            ev = "N/A"; dep = "N/A"; sot = "GELMİYOR"; da = "GELMİYOR"; skor = "0-0"; dk = 0
                            
                            # JSON içindeki verileri ayıklayalım
                            for item in ham_veri:
                                if isinstance(item, dict):
                                    t = item.get('type')
                                    if t == 'EV':
                                        ev = item.get('NA', '').split(' v ')[0] if ' v ' in item.get('NA', '') else 'Ev'
                                        dep = item.get('NA', '').split(' v ')[1] if ' v ' in item.get('NA', '') else 'Dep'
                                        skor = item.get('SS', '0-0')
                                        dk = item.get('TM', 0)
                                    elif t == 'SC':
                                        isim = item.get('NA')
                                        if isim == 'IShotsOnTarget':
                                            # Indeks hatasını önlemek için güvenli veri çekimi
                                            try:
                                                idx = ham_veri.index(item)
                                                ev_sot = ham_veri[idx+1].get('D1', 0)
                                                dep_sot = ham_veri[idx+2].get('D1', 0)
                                                sot = f"EV:{ev_sot} - DEP:{dep_sot}"
                                            except: pass
                                        elif isim == 'IDangerousAttack':
                                            try:
                                                idx = ham_veri.index(item)
                                                ev_da = ham_veri[idx+1].get('D1', 0)
                                                dep_da = ham_veri[idx+2].get('D1', 0)
                                                da = f"EV:{ev_da} - DEP:{dep_da}"
                                            except: pass

                            rapor = (
                                f"📊 VERİ TEST RAPORU\n"
                                f"Maç: {ev} - {dep}\n"
                                f"Dakika: {dk} | Skor: {skor}\n"
                                f"────────────────────\n"
                                f"🚀 Tehlikeli Atak (DA): {da}\n"
                                f"🎯 İsabetli Şut (SOT): {sot}\n"
                                f"────────────────────\n"
                                f"📝 NOT: Eğer DA ve SOT 'GELMİYOR' yazıyorsa, bu ligde veri kısıtlıdır."
                            )
                            await bot.send_message(chat_id=CHAT_ID, text=rapor)
                            await asyncio.sleep(2) # Telegram limitine takılmamak için
            except Exception as e:
                logger.error(f"Döngü hatası: {e}")
            
            await asyncio.sleep(300) # 5 dakikada bir kontrol

if __name__ == "__main__":
    asyncio.run(ana_dongu())
