import asyncio
import aiohttp
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.constants import ParseMode
import logging
import os
from datetime import datetime

# =============================================
# AYARLAR — Buraya kendi bilgilerini gir
# =============================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "BURAYA_BOT_TOKEN_YAZ")
CHAT_ID = os.getenv("CHAT_ID", "BURAYA_CHAT_ID_YAZ")
MIN_PUAN = 6  # Kaç puan üstünde bildirim gelsin (6+ tavsiye edilir)
KONTROL_SURESI = 120  # Kaç saniyede bir kontrol etsin (120 = 2 dakika)
# =============================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Daha önce bildirim gönderilmiş maçları takip et
bildirim_gonderilen = {}


def sinyal_hesapla(mac):
    """Maç verisinden sinyal puanı hesapla"""
    puan = 0
    aktif_sinyaller = []

    dakika = mac.get('dakika', 0)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    iy_ev = mac.get('iy_ev', 0)
    iy_dep = mac.get('iy_dep', 0)
    ev_corner = mac.get('ev_corner', 0)
    dep_corner = mac.get('dep_corner', 0)
    son_gol = mac.get('son_gol', 0)

    toplam_gol = ev_gol + dep_gol
    gol_fark = abs(ev_gol - dep_gol)
    toplam_corner = ev_corner + dep_corner
    corner_fark = abs(ev_corner - dep_corner)
    kg_var = ev_gol > 0 and dep_gol > 0
    gol_hizi = toplam_gol / dakika if dakika > 0 else 0

    # SİNYAL HESAPLAMA
    if kg_var:
        puan += 1
        aktif_sinyaller.append("✅ KG VAR")

    if gol_fark >= 2:
        puan += 1
        aktif_sinyaller.append(f"✅ Gol Farkı {gol_fark}")

    if toplam_gol >= 3:
        puan += 1
        aktif_sinyaller.append(f"✅ Toplam Gol {toplam_gol}")

    if gol_hizi >= 0.10:
        puan += 1
        aktif_sinyaller.append(f"✅ Gol Hızı {gol_hizi:.2f}/dk")

    if son_gol >= 70:
        puan += 1
        aktif_sinyaller.append(f"✅ Son Gol {son_gol}. dk")

    if corner_fark >= 5:
        puan += 1
        aktif_sinyaller.append(f"✅ Corner Dominant {ev_corner}-{dep_corner}")

    if dakika <= 30 and toplam_gol >= 2:
        puan += 1
        aktif_sinyaller.append(f"✅ Erken + Gol ({dakika}. dk)")

    if toplam_gol >= 4:
        puan += 1
        aktif_sinyaller.append(f"🔥 Toplam {toplam_gol} Gol!")

    if gol_fark >= 3 and dakika <= 30:
        puan += 1
        aktif_sinyaller.append(f"🔥 Büyük Fark ({gol_fark}) Erken")

    if gol_hizi >= 0.15:
        puan += 1
        aktif_sinyaller.append(f"🔥 Yüksek Hız {gol_hizi:.2f}/dk")

    return puan, aktif_sinyaller


def tavsiye_uret(mac, puan):
    """Puana göre tahmin tavsiyesi üret"""
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    toplam_gol = ev_gol + dep_gol
    gol_fark = ev_gol - dep_gol

    if puan >= 8:
        karar = "🔥 KESİN GİR"
    elif puan >= 6:
        karar = "✅ GİREBİLİRSİN"
    else:
        karar = "⚠️ DİKKATLİ OL"

    if toplam_gol >= 4:
        if gol_fark >= 2:
            tahmin = "EV GOL ATACAK (S)"
        elif gol_fark <= -2:
            tahmin = "DEP GOL ATACAK (S)"
        else:
            tahmin = "GOL OLACAK (S)"
    elif gol_fark >= 2:
        tahmin = "EV GOL ATACAK (S)"
    elif gol_fark <= -2:
        tahmin = "DEP GOL ATACAK (S)"
    else:
        tahmin = "GOL OLACAK (S)"

    return karar, tahmin


async def macları_cek():
    """SofaScore API'den canlı maçları çek"""
    url = "https://api.sofascore.com/api/v1/sport/football/events/live"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
        "Accept": "application/json",
        "Referer": "https://www.sofascore.com/"
    }

    maclar = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    events = data.get('events', [])

                    for event in events:
                        try:
                            # Dakika
                            status = event.get('status', {})
                            dakika = status.get('clock', {}).get('currentPeriodStartTimestamp', 0)
                            dakika_str = status.get('description', '')
                            
                            # Dakikayı parse et
                            if "'" in dakika_str:
                                dakika = int(dakika_str.replace("'", "").strip())
                            elif dakika_str.isdigit():
                                dakika = int(dakika_str)
                            else:
                                dakika = status.get('clock', {}).get('minutes', 0) or 0

                            # Sadece aktif maçlar (5-85. dk arası)
                            if dakika < 5 or dakika > 85:
                                continue

                            # Skorlar
                            home_score = event.get('homeScore', {})
                            away_score = event.get('awayScore', {})
                            ev_gol = home_score.get('current', 0) or 0
                            dep_gol = away_score.get('current', 0) or 0
                            iy_ev = home_score.get('period1', 0) or 0
                            iy_dep = away_score.get('period1', 0) or 0

                            # Takım isimleri
                            ev = event.get('homeTeam', {}).get('name', '?')
                            dep = event.get('awayTeam', {}).get('name', '?')
                            lig = event.get('tournament', {}).get('name', '?')
                            mac_id = event.get('id', '')

                            mac = {
                                'id': mac_id,
                                'ev': ev,
                                'dep': dep,
                                'lig': lig,
                                'dakika': dakika,
                                'ev_gol': ev_gol,
                                'dep_gol': dep_gol,
                                'iy_ev': iy_ev,
                                'iy_dep': iy_dep,
                                'ev_corner': 0,
                                'dep_corner': 0,
                                'son_gol': 0,
                            }
                            maclar.append(mac)

                        except Exception as e:
                            continue

    except Exception as e:
        logger.error(f"Veri çekme hatası: {e}")

    return maclar


async def bildirim_gonder(bot, mac, puan, sinyaller, karar, tahmin):
    """Telegram'a bildirim gönder"""
    ev_gol = mac['ev_gol']
    dep_gol = mac['dep_gol']
    dakika = mac['dakika']
    toplam_gol = ev_gol + dep_gol

    # Puan rengi
    if puan >= 8:
        puan_emoji = "🔥"
    elif puan >= 6:
        puan_emoji = "✅"
    else:
        puan_emoji = "⚠️"

    mesaj = f"""
{puan_emoji} *{mac['ev']} {ev_gol}–{dep_gol} {mac['dep']}*
🏆 {mac['lig']}
⏱ {dakika}. Dakika

📊 *Sinyal Puanı: {puan}/12*
{'█' * puan}{'░' * (12-puan)}

*Aktif Sinyaller:*
{chr(10).join(sinyaller)}

━━━━━━━━━━━━
{karar}
💡 *{tahmin}*
━━━━━━━━━━━━
_Veri bazlı analiz — risk her zaman mevcuttur_
"""

    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=mesaj,
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info(f"Bildirim gönderildi: {mac['ev']} vs {mac['dep']} — {puan} puan")
    except Exception as e:
        logger.error(f"Bildirim hatası: {e}")


async def ana_dongu():
    """Ana kontrol döngüsü"""
    bot = Bot(token=TELEGRAM_TOKEN)

    # Başlangıç mesajı
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"🤖 *MAÇ ANALİZ BOTU AKTİF*\n\n✅ Sistem çalışıyor\n📡 Her {KONTROL_SURESI//60} dakikada kontrol\n🎯 Minimum sinyal: {MIN_PUAN}/12\n\n_Güçlü sinyal bulunca otomatik bildirim gelecek!_",
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info("Bot başladı!")
    except Exception as e:
        logger.error(f"Başlangıç mesajı hatası: {e}")

    while True:
        try:
            logger.info("Maçlar kontrol ediliyor...")
            maclar = await macları_cek()
            logger.info(f"{len(maclar)} canlı maç bulundu")

            for mac in maclar:
                puan, sinyaller = sinyal_hesapla(mac)
                mac_id = mac['id']
                dakika = mac['dakika']

                # Yeterli puan var mı?
                if puan >= MIN_PUAN:
                    # Bu maça daha önce bildirim gönderildi mi?
                    onceki = bildirim_gonderilen.get(mac_id, 0)

                    # Her 15 dakikada bir tekrar bildir (puan artmışsa)
                    if puan > onceki:
                        karar, tahmin = tavsiye_uret(mac, puan)
                        await bildirim_gonder(bot, mac, puan, sinyaller, karar, tahmin)
                        bildirim_gonderilen[mac_id] = puan
                        await asyncio.sleep(1)

            # Biten maçları temizle
            bitmis = [k for k, v in bildirim_gonderilen.items() 
                     if k not in [m['id'] for m in maclar]]
            for k in bitmis:
                del bildirim_gonderilen[k]

        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}")

        await asyncio.sleep(KONTROL_SURESI)


if __name__ == "__main__":
    asyncio.run(ana_dongu())
