# MAC ANALIZ BOTU - V11-FINAL MASTER
# Strateji: Altın Pencere (55-60') & Hücum Epilasyonu (SOT <= 8)

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

async def analiz_et(mac_detay):
    ev_adi = ""; dep_adi = ""; dk = 0; skor = "0-0"; ev_sot = 0; dep_sot = 0; ev_da = 0; dep_da = 0
    lig = "Bilinmeyen Lig"

    for item in mac_detay:
        t = item.get('type')
        if t == 'EV':
            ev_adi = item.get('NA', '').split(' v ')[0]
            dep_adi = item.get('NA', '').split(' v ')[1]
            dk = int(item.get('TM', 0))
            skor = item.get('SS', '0-0')
            lig = item.get('CT', 'Lig')
        elif t == 'TE':
            if item.get('ID') == '1': # Ev Sahibi
                ev_sot = int(item.get('S1', 0))
                ev_da = int(item.get('S4', 0))
            elif item.get('ID') == '2': # Deplasman
                dep_sot = int(item.get('S1', 0))
                dep_da = int(item.get('S4', 0))

    toplam_sot = ev_sot + dep_sot
    ev_gol = int(skor.split('-')[0]) if '-' in skor else 0
    dep_gol = int(skor.split('-')[1]) if '-' in skor else 0
    fark = abs(ev_gol - dep_gol)

    # --- STRATEJİK FİLTRELER ---
    puan = 0.0
    detaylar = []

    # 1. Altın Pencere Kontrolü (55-60 dk)
    if 55 <= dk <= 60:
        puan += 4.0
        detaylar.append("🌟 Altın Pencere (55-60') +4.0")
    elif 61 <= dk <= 75:
        puan += 2.0
        detaylar.append("⏱️ Geçiş Oyunu (61-75') +2.0")
    else:
        return None # Zaman dışı

    # 2. Hücum Epilasyonu (SOT Sınırı)
    if toplam_sot <= 8:
        puan += (toplam_sot * 0.25)
        detaylar.append(f"🎯 SOT Verimliliği ({toplam_sot})")
    else:
        puan -= 2.0 # Şut çoksa kısırlık riski
        detaylar.append("🛑 SOT Sınırı Aşıldı -2.0")

    # 3. Skor ve Fark Kontrolü
    if fark >= 3: return None # Kopmuş maç
    if (ev_gol + dep_gol) >= 5: return None # Kaos eşiği

    if puan >= 6.0:
        return {
            "mesaj": (f"💎 STRATEJİK SİNYAL\n{ev_adi} {skor} {dep_adi}\n"
                      f"Dakika: {dk} | Lig: {lig}\n"
                      f"--------------------\n"
                      f"Puan: {puan}/12\n"
                      f"Analiz: {', '.join(detaylar)}\n"
                      f"--------------------\n"
                      f"SOT: {ev_sot}/{dep_sot} | DA: {ev_da}/{dep_da}\n"
                      f"💡 Tavsiye: SIRADAKİ GOL")
        }
    return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🏆 V11-FINAL MASTER AKTİF\nKurallar ve filtreler %100 devrede.")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # Canlı listeyi al
                async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                    list_data = await r.json()
                    results = list_data.get('results', [[]])[0]

                for m in results:
                    m_id = m.get('FI') or m.get('ID')
                    if not m_id or m_id in bildirim_gonderilen: continue

                    # Detay ve stats çek
                    async with session.get(f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={m_id}&stats=1") as er:
                        e_data = await er.json()
                        if e_data.get('success') == 1:
                            sonuc = await analiz_et(e_data['results'][0])
                            if sonuc:
                                await bot.send_message(chat_id=CHAT_ID, text=sonuc['mesaj'])
                                bildirim_gonderilen[m_id] = True
            except: pass
            await asyncio.sleep(120)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
