# MAC ANALIZ BOTU - V12.2 HASSAS TAKİP
# Hedef: 3 Puan barajı ve neden bildirim gelmediğini açıklayan loglar.

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

bildirim_gonderilen = {}
mac_atak_gecmisi = {} 

async def analiz_et(mac_detay, m_id):
    ev_adi = ""; dep_adi = ""; dk = 0; skor = "0-0"; ev_sot = 0; dep_sot = 0; ev_da = 0; dep_da = 0
    lig = "Lig"

    for item in mac_detay:
        t = item.get('type')
        if t == 'EV':
            ev_adi = item.get('NA', '').split(' v ')[0]
            dep_adi = item.get('NA', '').split(' v ')[1]
            dk = int(item.get('TM', 0))
            skor = item.get('SS', '0-0')
            lig = item.get('CT', 'Lig')
        elif t == 'TE':
            if item.get('ID') == '1':
                ev_sot = int(item.get('S1', 0))
                ev_da = int(item.get('S4', 0))
            elif item.get('ID') == '2':
                dep_sot = int(item.get('S1', 0))
                dep_da = int(item.get('S4', 0))

    toplam_da = ev_da + dep_da
    ev_gol = int(skor.split('-')[0]) if '-' in skor else 0
    dep_gol = int(skor.split('-')[1]) if '-' in skor else 0
    fark = abs(ev_gol - dep_gol)

    # --- HASSAS FİLTRELER ---
    puan = 0.0
    
    # Zaman Penceresi Kontrolü
    if 20 <= dk <= 85: # Aralığı iyice genişlettik (20-85 dk)
        puan += 4.0
    else:
        logger.info(f"⏭️ {ev_adi} elendi: Dakika ({dk}) kapsam dışı.")
        return None

    # Skor ve Fark Blokları
    if fark >= 3:
        logger.info(f"⏭️ {ev_adi} elendi: Fark çok açık ({skor}).")
        return None
    if (ev_gol + dep_gol) >= 6:
        logger.info(f"⏭️ {ev_adi} elendi: Çok fazla gol var.")
        return None

    # Skor Bonusu (1-1, 2-2, 0-0 vb.)
    onayli_skorlar = [(1,1), (2,2), (0,1), (2,0), (2,1), (1,2), (0,0)]
    if (ev_gol, dep_gol) in onayli_skorlar:
        puan += 3.0

    # İvme (Delta 5'e çekildi)
    onceki_atak = mac_atak_gecmisi.get(m_id, toplam_da)
    delta_atak = toplam_da - onceki_atak
    mac_atak_gecmisi[m_id] = toplam_da
    if delta_atak >= 5:
        puan += 2.0

    # BİLDİRİM EŞİĞİ (Sizin istediğiniz gibi 3.0)
    if puan >= 3.0:
        return {
            "mesaj": (f"🔔 SİNYAL (Puan: {puan})\n{ev_adi} {skor} {dep_adi}\n"
                      f"Dakika: {dk} | SOT: {ev_sot+dep_sot}\n"
                      f"Tavsiye: Sıradaki Gol")
        }
    return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="✅ V12.2 AKTİF: Bildirim eşiği 3.0, zaman 20-85 dk.")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                    list_data = await r.json()
                    results = list_data.get('results', [[]])[0]
                    logger.info(f"📡 {len(results)} maç taranıyor...")

                for m in results:
                    m_id = str(m.get('FI') or m.get('ID'))
                    if not m_id or m_id in bildirim_gonderilen: continue

                    async with session.get(f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={m_id}&stats=1") as er:
                        e_data = await er.json()
                        if e_data.get('success') == 1:
                            sonuc = await analiz_et(e_data['results'][0], m_id)
                            if sonuc:
                                await bot.send_message(chat_id=CHAT_ID, text=sonuc['mesaj'])
                                bildirim_gonderilen[m_id] = True
            except Exception as e:
                logger.error(f"Hata: {e}")
            await asyncio.sleep(120)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
