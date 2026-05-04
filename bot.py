# MAC ANALIZ BOTU - V12-STRATEJİK GÜNCELLEME
# [span_2](start_span)Güncelleme: Skor Genişletme (1-1, 2-2), Zaman (25-65') ve İvme (Delta 7)[span_2](end_span)

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
# Maçların bir önceki taramadaki tehlikeli atak sayılarını tutar (Delta hesabı için)
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
            if item.get('ID') == '1': # Ev Sahibi
                ev_sot = int(item.get('S1', 0))
                ev_da = int(item.get('S4', 0))
            elif item.get('ID') == '2': # Deplasman
                dep_sot = int(item.get('S1', 0))
                dep_da = int(item.get('S4', 0))

    toplam_da = ev_da + dep_da
    toplam_sot = ev_sot + dep_sot
    ev_gol = int(skor.split('-')[0]) if '-' in skor else 0
    dep_gol = int(skor.split('-')[1]) if '-' in skor else 0
    fark = abs(ev_gol - dep_gol)

    # -[span_3](start_span)-- YENİ STRATEJİK FİLTRELER[span_3](end_span) ---
    puan = 0.0
    detaylar = []

    # 1. [span_4](start_span)Genişletilmiş Zaman Penceresi (25-65 dk)[span_4](end_span)
    # [span_5](start_span)Verilerdeki 24, 25, 36, 45, 48, 54 ve 56. dakikalardaki başarılar baz alındı[span_5](end_span).
    if 25 <= dk <= 65:
        puan += 4.0
        detaylar.append(f"⏱️ Zaman Uygun ({dk}') +4.0")
    else:
        return None # Belirtilen kritik pencere dışı

    # 2. [span_6](start_span)Genişletilmiş Skor Filtresi[span_6](end_span)
    # [span_7](start_span)En kârlı 1-1 skoru ve diğer başarılı skorlar (2-2, 0-1, 2-0) eklendi[span_7](end_span).
    onayli_skorlar = [(1,1), (2,2), (0,1), (2,0), (2,1), (1,2), (0,0)]
    if (ev_gol, dep_gol) in onayli_skorlar:
        puan += 3.0
        detaylar.append(f"🎯 Kritik Skor ({skor}) +3.0")
        if (ev_gol, dep_gol) == (1,1): 
            [span_8](start_span)puan += 1.0 # 1-1 için ekstra bonus[span_8](end_span)

    # 3. [span_9](start_span)İvme (Delta) Kontrolü[span_9](end_span)
    # [span_10](start_span)Atak artış sınırı 10'dan 7'ye çekilerek botun nefes alması sağlandı[span_10](end_span).
    onceki_atak = mac_atak_gecmisi.get(m_id, toplam_da)
    delta_atak = toplam_da - onceki_atak
    mac_atak_gecmisi[m_id] = toplam_da # Güncel atağı kaydet

    if delta_atak >= 7:
        puan += 2.0
        detaylar.append(f"🚀 Atak İvmesi (Δ:{delta_atak}) +2.0")

    # 4. SOT Kontrolü (Hücum Epilasyonu)
    if toplam_sot <= 8:
        puan += 1.0
    elif toplam_sot > 12:
        puan -= 2.0 # Kısırlık cezası

    # Genel Bloklar
    if fark >= 3 or (ev_gol + dep_gol) >= 5: return None

    if puan >= 6.0:
        return {
            "mesaj": (f"💎 STRATEJİK SİNYAL (V12)\n{ev_adi} {skor} {dep_adi}\n"
                      f"Dakika: {dk} | Lig: {lig}\n"
                      f"--------------------\n"
                      f"Puan: {puan}/12\n"
                      f"Analiz: {', '.join(detaylar)}\n"
                      f"--------------------\n"
                      f"SOT: {ev_sot}/{dep_sot} | DA: {ev_da}/{dep_da}\n"
                      f"💡 Tavsiye: SIRADAKİ GOL (S)")
        }
    return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🚀 V12 AKTİF: Skorlar Genişletildi (1-1/2-2), Zaman 25-65 dk ve Delta 7 olarak güncellendi.")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                    list_data = await r.json()
                    results = list_data.get('results', [[]])[0]

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
            except: pass
            await asyncio.sleep(120)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
