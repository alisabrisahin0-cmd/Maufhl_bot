# MAC ANALIZ BOTU - V14.2 VERİ RÖNTGENİ
# Yenilik: Tüm filtreler kaldırıldı. Gelen ham veri test amaçlı Telegram'a basılacak.

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

def safe_int(val):
    try:
        if not val: return 0
        s_val = str(val).replace(',', '.').split('.')[0]
        return int(''.join(filter(str.isdigit, s_val)) or 0)
    except: return 0

async def ham_veriyi_cikar(mac_detay):
    ev_adi = "Ev"; dep_adi = "Dep"; dk = 0; skor = "0-0"; ev_sot = 0; dep_sot = 0; ev_da = 0; dep_da = 0

    veri_listesi = []
    if isinstance(mac_detay, list):
        for x in mac_detay:
            if isinstance(x, dict): veri_listesi.append(x)
            elif isinstance(x, list):
                for y in x:
                    if isinstance(y, dict): veri_listesi.append(y)
    elif isinstance(mac_detay, dict):
        veri_listesi.append(mac_detay)

    for item in veri_listesi:
        t = item.get('type')
        if t == 'EV':
            names = item.get('NA', '').split(' v ')
            ev_adi = names[0] if len(names) > 0 else "Ev"
            dep_adi = names[1] if len(names) > 1 else "Dep"
            dk = safe_int(item.get('TM', 0))
            skor = item.get('SS', '0-0')
        elif t == 'TE':
            if item.get('ID') == '1':
                ev_sot = safe_int(item.get('S1', 0)); ev_da = safe_int(item.get('S4', 0))
            elif item.get('ID') == '2':
                dep_sot = safe_int(item.get('S1', 0)); dep_da = safe_int(item.get('S4', 0))

    # HİÇBİR FİLTRE YOK. Ne geldiyse onu döndürüyor.
    return (f"🔍 TEST VERİSİ (Filtresiz)\n"
            f"⚽ {ev_adi} {skor} {dep_adi}\n"
            f"⏱ Okunan Dakika: {dk}\n"
            f"📊 Tehlikeli Atak: {ev_da} - {dep_da}\n"
            f"🎯 İsabetli Şut: {ev_sot} - {dep_sot}\n"
            f"--------------------")

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🔬 V14.2 RÖNTGEN MODU AKTİF: Tüm filtreler kapatıldı. Sadece veri test ediliyor.")
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                    data = await r.json()
                    res = data.get('results', [])
                
                if res and isinstance(res, list) and len(res) > 0 and isinstance(res[0], list):
                    res = res[0]

                test_mesajlari = 0 # Sadece ilk 3 maçı test etmesi için sayaç

                for m in res:
                    if not isinstance(m, dict): continue
                    m_id = m.get('FI') or m.get('ID')
                    if m_id is None or str(m_id).lower() == "none": continue
                    
                    m_id_str = str(m_id)
                    
                    async with session.get(f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={m_id_str}&stats=1") as er:
                        e_data = await er.json()
                        if e_data.get('success') == 1 and e_data.get('results'):
                            msg = await ham_veriyi_cikar(e_data['results'])
                            if msg:
                                await bot.send_message(chat_id=CHAT_ID, text=msg)
                                test_mesajlari += 1
                                
                    if test_mesajlari >= 3: 
                        break # İlk 3 veriyi Telegram'a atıp döngüden çıkar
                
                # Sistemi spam yapmamak için 5 dakika duraklatır
                await bot.send_message(chat_id=CHAT_ID, text="🛑 Test tamamlandı. 5 dakika sonra tekrar 3 rastgele maç verisi çekilecek.")
                await asyncio.sleep(300)
                
            except Exception as e: 
                logger.error(f"Döngü Hatası: {e}")
                await asyncio.sleep(120)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
