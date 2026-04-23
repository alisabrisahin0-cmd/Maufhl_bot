import asyncio
import aiohttp
from telegram import Bot
from telegram.constants import ParseMode
import logging
import os

# =============================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
MIN_PUAN = 6
KONTROL_SURESI = 120
# =============================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}


def sinyal_hesapla(mac):
    puan = 0
    aktif = []

    dakika = mac.get('dakika', 0)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    ev_corner = mac.get('ev_corner', 0)
    dep_corner = mac.get('dep_corner', 0)
    son_gol = mac.get('son_gol', 0)

    toplam_gol = ev_gol + dep_gol
    gol_fark = abs(ev_gol - dep_gol)
    corner_fark = abs(ev_corner - dep_corner)
    kg_var = ev_gol > 0 and dep_gol > 0
    gol_hizi = round(toplam_gol / dakika, 3) if dakika > 0 else 0

    if kg_var:
        puan += 1
        aktif.append("✅ KG VAR")

    if gol_fark >= 2:
        puan += 1
        aktif.append(f"✅ Gol Farkı {gol_fark}")

    if toplam_gol >= 3:
        puan += 1
        aktif.append(f"✅ Toplam Gol {toplam_gol}")

    if toplam_gol >= 4:
        puan += 1
        aktif.append(f"🔥 {toplam_gol} Gol — Yüksek!")

    if gol_hizi >= 0.10:
        puan += 1
        aktif.append(f"✅ Gol Hızı {gol_hizi}/dk")

    if gol_hizi >= 0.15:
        puan += 1
        aktif.append(f"🔥 Çok Yüksek Hız {gol_hizi}/dk")

    if son_gol >= 70:
        puan += 1
        aktif.append(f"✅ Son Gol {son_gol}. dk")

    if corner_fark >= 5:
        puan += 1
        aktif.append(f"✅ Corner Dominant {ev_corner}-{dep_corner}")

    if dakika <= 30 and toplam_gol >= 2:
        puan += 1
        aktif.append(f"✅ Erken + {toplam_gol} Gol ({dakika}. dk)")

    if gol_fark >= 3 and dakika <= 30:
        puan += 1
        aktif.append(f"🔥 Büyük Fark ({gol_fark}) Erken Dakika!")

    return puan, aktif


def tavsiye_uret(mac):
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    gol_fark = ev_gol - dep_gol
    toplam_gol = ev_gol + dep_gol

    if toplam_gol >= 4:
        if gol_fark >= 2:
            return "EV GOL ATACAK (S)"
        elif gol_fark <= -2:
            return "DEP GOL ATACAK (S)"
        else:
            return "GOL OLACAK (S)"
    elif gol_fark >= 2:
        return "EV GOL ATACAK (S)"
    elif gol_fark <= -2:
        return "DEP GOL ATACAK (S)"
    else:
        return "GOL OLACAK (S)"


async def macları_cek():
    url = "https://free-api-live-football-data.p.rapidapi.com/football-current-live"
    headers = {
        "x-rapidapi-host": "free-api-live-football-data.p.rapidapi.com",
        "x-rapidapi-key": RAPIDAPI_KEY
    }

    maclar = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    live = data.get('response', {}).get('live', [])
                    logger.info(f"{len(live)} canlı maç bulundu")

                    for event in live:
                        try:
                            # Takımlar
                            ev = event.get('homeTeam', {}).get('name', '?')
                            dep = event.get('awayTeam', {}).get('name', '?')
                            lig = event.get('league', {}).get('name', '?')
                            mac_id = str(event.get('id', ''))

                            # Dakika
                            dakika = int(event.get('minute', 0) or 0)

                            # Skor
                            score = event.get('score', {})
                            ev_gol = int(score.get('home', 0) or 0)
                            dep_gol = int(score.get('away', 0) or 0)

                            # Corner
                            stats = event.get('stats', [])
                            ev_corner = 0
                            dep_corner = 0
                            son_gol = 0

                            for stat in stats:
                                if stat.get('type', '').lower() == 'corner kicks':
                                    ev_corner = int(stat.get('home', 0) or 0)
                                    dep_corner = int(stat.get('away', 0) or 0)

                            # Son gol dakikası
                            goals = event.get('goals', [])
                            if goals:
                                son_gol_dk = 0
                                for g in goals:
                                    gdk = int(g.get('minute', 0) or 0)
                                    if gdk > son_gol_dk:
                                        son_gol_dk = gdk
                                son_gol = son_gol_dk

                            # Sadece aktif maçlar
                            if dakika < 5 or dakika > 85:
                                continue

                            mac = {
                                'id': mac_id,
                                'ev': ev,
                                'dep': dep,
                                'lig': lig,
                                'dakika': dakika,
                                'ev_gol': ev_gol,
                                'dep_gol': dep_gol,
                                'ev_corner': ev_corner,
                                'dep_corner': dep_corner,
                                'son_gol': son_gol,
                            }
                            maclar.append(mac)

                        except Exception as e:
                            logger.error(f"Maç parse hatası: {e}")
                            continue
                else:
                    logger.error(f"API hatası: {resp.status}")

    except Exception as e:
        logger.error(f"Veri çekme hatası: {e}")

    return maclar


async def bildirim_gonder(bot, mac, puan, sinyaller, tahmin):
    ev_gol = mac['ev_gol']
    dep_gol = mac['dep_gol']
    dakika = mac['dakika']

    if puan >= 8:
        karar = "🔥 KESİN GİR"
        emoji = "🔥"
    elif puan >= 6:
        karar = "✅ GİREBİLİRSİN"
        emoji = "✅"
    else:
        karar = "⚠️ DİKKATLİ OL"
        emoji = "⚠️"

    bar = "█" * puan + "░" * (12 - puan)

    mesaj = f"""{emoji} *{mac['ev']} {ev_gol}–{dep_gol} {mac['dep']}*
🏆 {mac['lig']}
⏱ *{dakika}. Dakika*

📊 *Sinyal: {puan}/12*
`{bar}`

*Aktif Sinyaller:*
{chr(10).join(sinyaller)}

━━━━━━━━━━━━
{karar}
💡 *{tahmin}*
━━━━━━━━━━━━
_Veri bazlı analiz — risk mevcuttur_"""

    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=mesaj,
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info(f"✅ Bildirim: {mac['ev']} vs {mac['dep']} — {puan} puan")
    except Exception as e:
        logger.error(f"Bildirim hatası: {e}")


async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)

    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text="🤖 *MAÇ ANALİZ BOTU AKTİF*\n\n✅ RapidAPI bağlandı\n📡 Her 2 dakikada kontrol\n🎯 Minimum sinyal: 6/12\n🔍 Corner + Skor + Gol hızı analizi\n\n_Güçlü sinyal bulunca otomatik bildirim gelecek!_",
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info("Bot başladı!")
    except Exception as e:
        logger.error(f"Başlangıç hatası: {e}")

    while True:
        try:
            maclar = await macları_cek()

            for mac in maclar:
                puan, sinyaller = sinyal_hesapla(mac)
                mac_id = mac['id']

                if puan >= MIN_PUAN:
                    onceki = bildirim_gonderilen.get(mac_id, 0)
                    if puan > onceki:
                        tahmin = tavsiye_uret(mac)
                        await bildirim_gonder(bot, mac, puan, sinyaller, tahmin)
                        bildirim_gonderilen[mac_id] = puan
                        await asyncio.sleep(1)

            # Biten maçları temizle
            aktif_idler = [m['id'] for m in maclar]
            bitmis = [k for k in bildirim_gonderilen if k not in aktif_idler]
            for k in bitmis:
                del bildirim_gonderilen[k]

        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}")

        await asyncio.sleep(KONTROL_SURESI)


if __name__ == "__main__":
    asyncio.run(ana_dongu())
