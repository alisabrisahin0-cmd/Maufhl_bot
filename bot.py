# MAC ANALIZ BOTU - V10.1 (KALP ATIŞI EKLENTİLİ)
# Şifreli verileri (S1, S4) okur, stratejiyi uygular ve 30 dakikada bir yaşıyorum mesajı atar.

import asyncio
import aiohttp
import os
import time
import logging
from telegram import Bot

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# AYARLAR
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")
MIN_PUAN = float(os.getenv("MIN_PUAN", "6.0"))
KALP_ATISI_SURESI = 1800  # 30 dakika (1800 saniye)

bildirim_gonderilen = {}

def verileri_ayikla(results):
    mac_verisi = {
        'dk': 0, 'ev_gol': 0, 'dep_gol': 0, 
        'ev_sot': 0, 'dep_sot': 0, 'ev_da': 0, 'dep_da': 0, 
        'ev_isim': 'Ev', 'dep_isim': 'Dep'
    }
    
    for item in results:
        if item.get('type') == 'EV':
            mac_verisi['dk'] = int(item.get('TM', 0))
            skor = item.get('SS', '0-0')
            if '-' in skor:
                mac_verisi['ev_gol'] = int(skor.split('-')[0])
                mac_verisi['dep_gol'] = int(skor.split('-')[1])
            isimler = item.get('NA', 'Ev v Dep')
            if ' v ' in isimler:
                mac_verisi['ev_isim'], mac_verisi['dep_isim'] = isimler.split(' v ', 1)
                
        elif item.get('type') == 'TE':
            if str(item.get('OR')) == '0': 
                mac_verisi['ev_sot'] = int(item.get('S1', 0))
                mac_verisi['ev_da'] = int(item.get('S4', 0))
            elif str(item.get('OR')) == '1':
                mac_verisi['dep_sot'] = int(item.get('S1', 0))
                mac_verisi['dep_da'] = int(item.get('S4', 0))
                
    return mac_verisi

def strateji_uygula(mac):
    dk = mac['dk']
    toplam_gol = mac['ev_gol'] + mac['dep_gol']
    fark = abs(mac['ev_gol'] - mac['dep_gol'])
    toplam_sot = mac['ev_sot'] + mac['dep_sot']
    toplam_da = mac['ev_da'] + mac['dep_da']

    if toplam_gol >= 5: return 0.0, [], "KAOS_ESIGI"
    if fark >= 3: return 0.0, [], "OLUM_BOLGESI"
    
    puan = 0.0
    detay = []

    if 55 <= dk <= 60:
        puan += 4.0
        detay.append("ALTIN PENCERE (55-60') +4.0")
    elif 60 < dk <= 75:
        puan += 2.0
        detay.append("GECIS OYUNU EVRESI +2.0")

    if toplam_sot <= 8:
        puan += (toplam_sot * 0.25)
        detay.append(f"MAKUL SUT SEVIYESI ({toplam_sot})")
    else:
        puan -= 1.5
        detay.append("HUCUM EPILASYONU (SOT > 8) CEZA!")

    if toplam_da > 80:
        puan += 1.0
        detay.append("YUKSEK TEHLIKELI ATAK +1.0")

    return round(puan, 1), detay, "SUCCESS"

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    logger.info("V10.1 MASTER SİSTEM (KALP ATIŞLI) BAŞLATILDI.")
    try:
        await bot.send_message(chat_id=CHAT_ID, text="🏆 SİSTEM HAZIR\nŞifreler çözüldü, kurallar devrede.\nSize her 30 dakikada bir durum raporu (Kalp Atışı) geçeceğim.")
    except: pass

    son_kalp_atisi = time.time()

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                list_url = f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1"
                async with session.get(list_url, timeout=15) as resp:
                    data = await resp.json()
                    results = data.get('results', [])
                    if results and isinstance(results[0], list):
                        results = results[0]

                mac_sayisi = len(results)

                # --- KALP ATIŞI KONTROLÜ ---
                su_an = time.time()
                if su_an - son_kalp_atisi > KALP_ATISI_SURESI:
                    try:
                        rapor = (
                            f"💓 KALP ATIŞI (Sistem Aktif)\n"
                            f"Arka planda pür dikkat çalışıyorum.\n"
                            f"Şu an bültendeki {mac_sayisi} canlı maçı taradım ve eledim.\n"
                            f"Kriterlerinize (dk 55-60, SOT vs.) uyan kusursuz bir an kollamaya devam ediyorum."
                        )
                        await bot.send_message(chat_id=CHAT_ID, text=rapor)
                        son_kalp_atisi = su_an # Sayacı sıfırla
                    except: pass

                # --- MAÇLARI TARAMA ---
                for item in results:
                    mac_id = item.get('FI') or item.get('id') or item.get('ID')
                    if not mac_id: continue

                    if str(mac_id) in bildirim_gonderilen: continue

                    event_url = f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={mac_id}&stats=1"
                    async with session.get(event_url, timeout=15) as event_resp:
                        event_data = await event_resp.json()
                        
                        if event_data.get('success') == 1:
                            mac_detaylari = event_data.get('results', [{}])[0]
                            mac_verisi = verileri_ayikla(mac_detaylari)
                            puan, detaylar, durum = strateji_uygula(mac_verisi)
                            
                            if durum == "SUCCESS" and puan >= MIN_PUAN:
                                mesaj = (
                                    f"🔥 STRATEJİK SİNYAL\n"
                                    f"Maç: {mac_verisi['ev_isim']} {mac_verisi['ev_gol']}-{mac_verisi['dep_gol']} {mac_verisi['dep_isim']}\n"
                                    f"Dakika: {mac_verisi['dk']}'\n"
                                    f"--------------------\n"
                                    f"Puan: {puan}/12\n"
                                    f"Analiz: " + ", ".join(detaylar) + "\n"
                                    f"--------------------\n"
                                    f"🎯 Şut (SOT): {mac_verisi['ev_sot']} - {mac_verisi['dep_sot']}\n"
                                    f"🚀 Atak (DA): {mac_verisi['ev_da']} - {mac_verisi['dep_da']}\n"
                                )
                                await bot.send_message(chat_id=CHAT_ID, text=mesaj)
                                bildirim_gonderilen[str(mac_id)] = True
                                
            except Exception as e:
                logger.error(f"Döngü Hatası: {e}")
            
            await asyncio.sleep(120)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
