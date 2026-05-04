# MAC ANALIZ BOTU - V10 THE MATRIX (NİHAİ SÜRÜM)
# Şifreli API verilerini (S1, S4) okuyup strateji filtrelerini uygular.

import asyncio
import aiohttp
import os
import logging
from telegram import Bot

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# AYARLAR
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")
MIN_PUAN = float(os.getenv("MIN_PUAN", "6.0"))

bildirim_gonderilen = {}

def verileri_ayikla(results):
    """API'den gelen şifreli JSON (S1, S4, vb.) bloğunu anlamlı maç verisine çevirir."""
    mac_verisi = {
        'dk': 0, 'ev_gol': 0, 'dep_gol': 0, 
        'ev_sot': 0, 'dep_sot': 0, 'ev_da': 0, 'dep_da': 0, 
        'ev_isim': 'Ev', 'dep_isim': 'Dep'
    }
    
    for item in results:
        # EV (Event) Bloğu: Dakika, Skor ve İsimler
        if item.get('type') == 'EV':
            mac_verisi['dk'] = int(item.get('TM', 0))
            skor = item.get('SS', '0-0')
            if '-' in skor:
                mac_verisi['ev_gol'] = int(skor.split('-')[0])
                mac_verisi['dep_gol'] = int(skor.split('-')[1])
            isimler = item.get('NA', 'Ev v Dep')
            if ' v ' in isimler:
                mac_verisi['ev_isim'], mac_verisi['dep_isim'] = isimler.split(' v ', 1)
                
        # TE (Team) Bloğu: Şutlar ve Ataklar
        elif item.get('type') == 'TE':
            # OR: '0' Genellikle Ev Sahibi, OR: '1' Deplasman
            if str(item.get('OR')) == '0': 
                mac_verisi['ev_sot'] = int(item.get('S1', 0)) # S1 = İsabetli Şut
                mac_verisi['ev_da'] = int(item.get('S4', 0))  # S4 = Tehlikeli Atak
            elif str(item.get('OR')) == '1':
                mac_verisi['dep_sot'] = int(item.get('S1', 0))
                mac_verisi['dep_da'] = int(item.get('S4', 0))
                
    return mac_verisi

def strateji_uygula(mac):
    """Sizin kurallarınıza (Altın Pencere, Hücum Epilasyonu) göre puanlama yapar."""
    dk = mac['dk']
    toplam_gol = mac['ev_gol'] + mac['dep_gol']
    fark = abs(mac['ev_gol'] - mac['dep_gol'])
    toplam_sot = mac['ev_sot'] + mac['dep_sot']
    toplam_da = mac['ev_da'] + mac['dep_da']

    # HARD BLOCKS (Kesin Ret Kuralları)
    if toplam_gol >= 5: return 0.0, [], "KAOS_ESIGI"
    if fark >= 3: return 0.0, [], "OLUM_BOLGESI"
    
    puan = 0.0
    detay = []

    # ALTIN PENCERE
    if 55 <= dk <= 60:
        puan += 4.0
        detay.append("ALTIN PENCERE (55-60') +4.0")
    elif 60 < dk <= 75:
        puan += 2.0
        detay.append("GECIS OYUNU EVRESI +2.0")

    # SOT CEZA (HÜCUM EPİLASYONU)
    if toplam_sot <= 8:
        puan += (toplam_sot * 0.25)
        detay.append(f"MAKUL SUT SEVIYESI ({toplam_sot})")
    else:
        puan -= 1.5
        detay.append("HUCUM EPILASYONU (SOT > 8) CEZA!")

    # TEHLİKELİ ATAK
    if toplam_da > 80:
        puan += 1.0
        detay.append("YUKSEK TEHLIKELI ATAK +1.0")

    return round(puan, 1), detay, "SUCCESS"

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    logger.info("V10 MASTER SİSTEM BAŞLATILDI - Filtreler ve Şifre Çözücü Aktif.")
    try:
        await bot.send_message(chat_id=CHAT_ID, text="🏆 SİSTEM HAZIR\nŞifreler çözüldü, kurallar devrede. Uygun sinyaller bekleniyor...")
    except: pass

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # 1. Bültendeki canlı maçların listesini çek
                list_url = f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1"
                async with session.get(list_url, timeout=15) as resp:
                    data = await resp.json()
                    results = data.get('results', [])
                    if results and isinstance(results[0], list):
                        results = results[0]

                # 2. Her bir maç için ID alıp detay sorgula
                for item in results:
                    mac_id = item.get('FI') or item.get('id') or item.get('ID')
                    if not mac_id: continue

                    # Daha önce bildirim atıldıysa tekrar etme
                    if str(mac_id) in bildirim_gonderilen: continue

                    # 3. İstatistikleri Çek (stats=1)
                    event_url = f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={mac_id}&stats=1"
                    async with session.get(event_url, timeout=15) as event_resp:
                        event_data = await event_resp.json()
                        
                        if event_data.get('success') == 1:
                            mac_detaylari = event_data.get('results', [{}])[0]
                            
                            # JSON Şifresini Çöz
                            mac_verisi = verileri_ayikla(mac_detaylari)
                            
                            # Filtreye Sok
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
            
            # API Limitlerine takılmamak için 2 dakika bekle
            await asyncio.sleep(120)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
