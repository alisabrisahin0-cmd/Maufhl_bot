import asyncio
import aiohttp
from telegram import Bot
import logging
import os
from datetime import datetime, timedelta
import json

# --- AYARLAR ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
# Minimum puan eşiği (Raporundaki güven endeksine göre 6 idealdir)
MIN_PUAN = 6 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}

# --- SAAT KONTROLÜ (Kütüphanesiz Türkiye Saati) ---
def calisma_saati_uygun_mu():
    # Sunucu saati ne olursa olsun UTC+3 (Türkiye) hesaplar
    tr_saati = datetime.utcnow() + timedelta(hours=3)
    saat = tr_saati.hour
    gun = tr_saati.weekday() # 0=Pazartesi, 6=Pazar

    # Hafta içi: 19:00 - 00:00
    if gun < 5:
        return saat >= 19
    # Hafta sonu: 19:00 - 23:00
    else:
        return 19 <= saat < 23

# --- STRATEJİK ANALİZ MOTORU (Rapor Tabanlı) ---
def mac_analiz_et(mac):
    """
    Raporundaki 'Winning Code' ve 'Altın Pencere' stratejilerini uygular.
    """
    puan = 0
    detaylar = []
    
    # 1. Winning Code (Hard Filter) - Rapor Sayfa 7
    # VU=1, TUM=1, MA=0, DIYI=0 zorunludur.
    vu = mac.get('vu', 0)
    tum = mac.get('tum', 0)
    ma = mac.get('ma', 0)
    diyi = mac.get('diyi', 0)
    
    if not (vu == 1 and tum == 1 and ma == 0 and diyi == 0):
        return None, 0, []

    dakika = mac.get('dakika', 0)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    korner = mac.get('korner', 0)
    ah = mac.get('ah_deger', 0)
    usa = mac.get('usa', 0)

    # 2. Altın Pencereler ve Stratejiler
    # DDC Stratejisi: 60. Dakika Düğüm Çözücü (54-60 dk)
    if 54 <= dakika <= 60:
        puan += 3.5
        detaylar.append("DDC: 60' Güç Penceresi")
        if ev_gol == dep_gol: # Beraberlik durumunda başarı oranı artar
            puan += 1.5
            detaylar.append("Beraberlik Kırılma Bonusu")

    # EED Stratejisi: Elite Ev Sahibi Dominansı (24-36 dk)
    elif 24 <= dakika <= 36:
        puan += 2.5
        detaylar.append("EED: Erken Baskı Analizi")
        if ah <= -0.75:
            puan += 1.5
            detaylar.append("Piyasa Favori Onayı")

    # UTV Stratejisi: Uzatma ve Yüksek Tansiyon (41-49 dk)
    elif 41 <= dakika <= 49:
        if usa == 1:
            puan += 3.0
            detaylar.append("UTV: Uzatma/Kaos Sinyali (USA=1)")

    # 3. Ofansif Baskı (Korner Endeksi)
    if korner >= 10.5:
        puan += 2.5
        detaylar.append("Abluka (Yüksek Korner)")
    elif korner >= 8.0:
        puan += 1.0

    # 4. Skor Durumu Analizi
    # Maç berabereyken gol ihtimali %40 daha yüksektir (Rapor bulgusu)
    if ev_gol == dep_gol:
        puan += 1.0
    
    # 5. Soğuma (Cooling Off) Kontrolü
    # Skor 3-0 gibi netleşmiş ve dakika geçmişse puan kırılır
    if abs(ev_gol - dep_gol) >= 3 and dakika > 60:
        puan -= 2.0
        detaylar.append("Doyum Noktası (Risk)")

    return "Gol Olacak (S)", puan, detaylar

async def bildirim_gonder(bot, mac, puan, detaylar):
    mesaj = (
        f"🎯 **YENİ SİNYAL: {puan} PUAN**\n"
        f"⚽ {mac['ev']} vs {mac['dep']}\n"
        f"⏰ Dakika: {mac['dakika']}' | Skor: {mac['ev_gol']}-{mac['dep_gol']}\n"
        f"📊 Korner: {mac['korner']} | AH: {mac['ah_deger']}\n\n"
        f"📝 **Analiz Notları:**\n- " + "\n- ".join(detaylar) + "\n\n"
        f"✅ **Tahmin: Gol Olacak (S)**"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Telegram hatası: {e}")

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    logger.info("BOT BAŞLATILDI - Türkiye Saati Takip Ediliyor (19:00 Aktif)")

    async with aiohttp.ClientSession() as session:
        while True:
            # Çalışma saati kontrolü
            if not calisma_saati_uygun_mu():
                logger.info("Çalışma saatleri dışındayız. 19:00 bekleniyor...")
                await asyncio.sleep(600) # 10 dk uyku
                continue

            try:
                # Burası API'den veri çektiğin kısım olmalı
                # Örnek maç verisi yapısı (Simüle edilmiştir):
                maclar = [] # maclari_cek() fonksiyonundan gelen veri
                
                for mac in maclar:
                    mac_id = mac.get('id')
                    if mac_id in bildirim_gonderilen:
                        continue

                    tahmin, puan, detaylar = mac_analiz_et(mac)
                    
                    if tahmin and puan >= MIN_PUAN:
                        await bildirim_gonder(bot, mac, puan, detaylar)
                        bildirim_gonderilen[mac_id] = True
                
                logger.info("Tarama tamamlandı, 2 dakika bekleniyor...")
                await asyncio.sleep(120)

            except Exception as e:
                logger.error(f"Döngü hatası: {e}")
                await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
