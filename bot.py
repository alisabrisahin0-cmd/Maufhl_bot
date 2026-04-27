import asyncio
import aiohttp
from telegram import Bot
import logging
import os
from datetime import datetime
import pytz  # Saat dilimi için gerekli

# --- AYARLAR ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
TR_SAAT = pytz.timezone('Europe/Istanbul') # Türkiye saat dilimi

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# --- STRATEJİK FİLTRELEME (Rapora Göre) ---

def mac_analiz_et(mac):
    puan = 0
    detaylar = []
    dakika = mac.get('dakika', 0)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    ah = mac.get('ah_deger', 0)
    korner = mac.get('korner', 0)
    
    # 1. Winning Code Sentezi (Zorunlu Filtre)
    # VU=1, TUM=1, MA=0, DIYI=0 kuralı sağlanmazsa maç direkt elenir
    if not (mac.get('vu') == 1 and mac.get('tum') == 1 and mac.get('ma') == 0 and mac.get('diyi') == 0):
        return None, 0, []

    # 2. Altın Pencereler (Rapor Sayfa 3-4)
    # 60. Dakika Düğüm Çözücü (DDC)
    if 54 <= dakika <= 60:
        puan += 3.5
        detaylar.append("DDC Stratejisi: 60' Güç Penceresi")
        if ev_gol == dep_gol: # Beraberlik bonusu
            puan += 1.5
            detaylar.append("Beraberlik Kırılma Bonusu")

    # Elite Ev Sahibi Dominansı (EED) - İlk Yarı
    elif 24 <= dakika <= 36:
        puan += 2.5
        detaylar.append("EED Stratejisi: Erken Baskı")
        if ah <= -0.75:
            puan += 1.5
            detaylar.append("Piyasa Favori Onayı")

    # Uzatma ve Yüksek Tansiyon (UTV)
    elif 41 <= dakika <= 49:
        if mac.get('usa') == 1:
            puan += 3.0
            detaylar.append("UTV Stratejisi: Uzatma/Kaos Sinyali")

    # 3. Ofansif Baskı (Korner Endeksi)
    if korner >= 10.5:
        puan += 2.0
        detaylar.append("Yüksek Korner (Abluka)")
    elif korner >= 8.0:
        puan += 1.0

    return "Gol Olacak (S)", puan, detaylar

# --- SAAT KONTROLÜ (Düzeltildi) ---
def calisma_saati_uygun_mu():
    simdi = datetime.now(TR_SAAT)
    saat = simdi.hour
    gun = simdi.weekday() # 0=Pazartesi, 6=Pazar

    # Hafta içi: 19:00 - 00:00
    if gun < 5:
        return saat >= 19
    # Hafta sonu: 19:00 - 23:00
    else:
        return 19 <= saat < 23

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    logger.info("Bot Aktif Edildi. Türkiye Saati Takip Ediliyor.")

    while True:
        if not calisma_saati_uygun_mu():
            logger.info("Çalışma saatleri dışındayız (19:00 bekleniyor). Uyku modu.")
            await asyncio.sleep(600) # 10 dakika sonra tekrar kontrol et
            continue

        try:
            # API'den maç çekme ve analiz süreci buraya gelecek
            # Örnek akış:
            # maclar = await maclari_getir()
            # for mac in maclar:
            #    tahmin, puan, neden = mac_analiz_et(mac)
            #    if puan >= 6:
            #        await bildirim_gonder(mac, puan, neden)
            
            logger.info("Maçlar taranıyor...")
            await asyncio.sleep(120) # 2 dakikada bir güncelle

        except Exception as e:
            logger.error(f"Döngü hatası: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
