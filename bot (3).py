import asyncio
import aiohttp
from telegram import Bot
from telegram.constants import ParseMode
import logging
import os
import asyncpg
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "7"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
db_pool = None


def kontrol_suresi_al():
    saat = datetime.now().hour
    dk = datetime.now().minute
    if saat < 11 or (saat == 11 and dk < 30):
        return None
    elif (saat == 11 and dk >= 30) or (12 <= saat < 15):
        return 480   # 8 dakika
    elif 15 <= saat < 19:
        return 420   # 7 dakika
    elif 19 <= saat < 23:
        return 360   # 6 dakika
    else:
        return None


async def db_baglant():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        await db_pool.execute("""
            CREATE TABLE IF NOT EXISTS sinyaller (
                id SERIAL PRIMARY KEY,
                mac_id TEXT,
                ev TEXT,
                dep TEXT,
                lig TEXT,
                dakika INTEGER,
                ev_gol INTEGER,
                dep_gol INTEGER,
                puan INTEGER,
                tahmin TEXT,
                bildirim_zamani TIMESTAMP DEFAULT NOW(),
                sonuc TEXT DEFAULT 'BEKLIYOR',
                final_ev_gol INTEGER DEFAULT 0,
                final_dep_gol INTEGER DEFAULT 0
            )
        """)
        logger.info("Veritabani baglandi!")
    except Exception as e:
        logger.error(f"DB hatasi: {e}")


async def sinyal_kaydet(mac, puan, tahmin):
    try:
        if db_pool:
            await db_pool.execute("""
                INSERT INTO sinyaller
                (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol, puan, tahmin)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """, mac['id'], mac['ev'], mac['dep'], mac['lig'],
                mac['dakika'], mac['ev_gol'], mac['dep_gol'], puan, tahmin)
    except Exception as e:
        logger.error(f"Kayit hatasi: {e}")


async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        if db_pool:
            await db_pool.execute("""
                UPDATE sinyaller
                SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3
                WHERE mac_id=$4 AND sonuc='BEKLIYOR'
            """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e:
        logger.error(f"Guncelleme hatasi: {e}")


async def haftalik_rapor(bot):
    try:
        bir_hafta = datetime.now() - timedelta(days=7)
        rows = await db_pool.fetch(
            "SELECT * FROM sinyaller WHERE bildirim_zamani > $1 AND sonuc != 'BEKLIYOR'",
            bir_hafta
        )
        toplam = len(rows)
        if toplam == 0:
            return
        kazanan = len([r for r in rows if r['sonuc'] == 'TUTTU'])
        kaybeden = toplam - kazanan
        oran = round(kazanan / toplam * 100, 1)
        mesaj = f"""📊 *HAFTALIK RAPOR*
━━━━━━━━━━━━
📅 Son 7 Gün
📈 Toplam: {toplam}
✅ Kazanan: {kazanan}
❌ Kaybeden: {kaybeden}
🎯 Başarı: *%{oran}*
━━━━━━━━━━━━"""
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Haftalik rapor hatasi: {e}")


async def aylik_rapor(bot):
    try:
        bir_ay = datetime.now() - timedelta(days=30)
        rows = await db_pool.fetch(
            "SELECT * FROM sinyaller WHERE bildirim_zamani > $1 AND sonuc != 'BEKLIYOR'",
            bir_ay
        )
        toplam = len(rows)
        if toplam == 0:
            return
        kazanan = len([r for r in rows if r['sonuc'] == 'TUTTU'])
        kaybeden = toplam - kazanan
        oran = round(kazanan / toplam * 100, 1)
        mesaj = f"""📊 *AYLIK RAPOR*
━━━━━━━━━━━━
📅 Son 30 Gün
📈 Toplam: {toplam}
✅ Kazanan: {kazanan}
❌ Kaybeden: {kaybeden}
🎯 Başarı: *%{oran}*
━━━━━━━━━━━━"""
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Aylik rapor hatasi: {e}")


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
        aktif.append(f"✅ Gol Farki {gol_fark}")
    if toplam_gol >= 3:
        puan += 1
        aktif.append(f"✅ Toplam Gol {toplam_gol}")
    if toplam_gol >= 4:
        puan += 1
        aktif.append(f"🔥 {toplam_gol} Gol!")
    if gol_hizi >= 0.10:
        puan += 1
        aktif.append(f"✅ Gol Hizi {gol_hizi}/dk")
    if gol_hizi >= 0.15:
        puan += 1
        aktif.append(f"🔥 Yuksek Hiz {gol_hizi}/dk")
    if son_gol >= 70:
        puan += 1
        aktif.append(f"✅ Son Gol {son_gol}. dk")
    if corner_fark >= 5:
        puan += 1
        aktif.append(f"✅ Corner {ev_corner}-{dep_corner}")
    if dakika <= 30 and toplam_gol >= 2:
        puan += 1
        aktif.append(f"✅ Erken+Gol ({dakika}dk)")
    if gol_fark >= 3 and dakika <= 30:
        puan += 1
        aktif.append(f"🔥 Buyuk Fark Erken!")
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
        return "GOL OLACAK (S)"
    elif gol_fark >= 2:
        return "EV GOL ATACAK (S)"
    elif gol_fark <= -2:
        return "DEP GOL ATACAK (S)"
    return "GOL OLACAK (S)"


def sonuc_kontrol(tahmin, bas_ev, bas_dep, fin_ev, fin_dep):
    yeni_ev = fin_ev - bas_ev
    yeni_dep = fin_dep - bas_dep
    yeni_toplam = yeni_ev + yeni_dep
    if tahmin == "GOL OLACAK (S)":
        return "TUTTU" if yeni_toplam >= 1 else "DSTU"
    elif tahmin == "EV GOL ATACAK (S)":
        return "TUTTU" if yeni_ev >= 1 else "DSTU"
    elif tahmin == "DEP GOL ATACAK (S)":
        return "TUTTU" if yeni_dep >= 1 else "DSTU"
    return "BELIRSIZ"


async def macları_cek():
    url = "https://api-football-v1.p.rapidapi.com/v3/fixtures?live=all"
    headers = {
        "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
        "x-rapidapi-key": RAPIDAPI_KEY
    }
    maclar = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    fixtures = data.get('response', [])
                    logger.info(f"{len(fixtures)} canli mac bulundu")
                    for f in fixtures:
                        try:
                            fixture = f.get('fixture', {})
                            teams = f.get('teams', {})
                            goals = f.get('goals', {})
                            league = f.get('league', {})
                            mac_id = str(fixture.get('id', ''))
                            ev = teams.get('home', {}).get('name', '?')
                            dep = teams.get('away', {}).get('name', '?')
                            lig = league.get('name', '?')
                            dakika = int(fixture.get('status', {}).get('elapsed', 0) or 0)
                            if dakika < 5 or dakika > 85:
                                continue
                            ev_gol = int(goals.get('home', 0) or 0)
                            dep_gol = int(goals.get('away', 0) or 0)
                            ev_corner = dep_corner = son_gol = 0
                            home_id = teams.get('home', {}).get('id')
                            for stat in f.get('statistics', []):
                                if 'corner' in stat.get('type', '').lower():
                                    val = int(stat.get('value', 0) or 0)
                                    if stat.get('team', {}).get('id') == home_id:
                                        ev_corner = val
                                    else:
                                        dep_corner = val
                            for event in f.get('events', []):
                                if event.get('type') == 'Goal':
                                    gdk = int(event.get('time', {}).get('elapsed', 0) or 0)
                                    if gdk > son_gol:
                                        son_gol = gdk
                            maclar.append({
                                'id': mac_id, 'ev': ev, 'dep': dep, 'lig': lig,
                                'dakika': dakika, 'ev_gol': ev_gol, 'dep_gol': dep_gol,
                                'ev_corner': ev_corner, 'dep_corner': dep_corner,
                                'son_gol': son_gol
                            })
                        except:
                            continue
                elif resp.status == 429:
                    logger.warning("API limit!")
                else:
                    logger.error(f"API hata: {resp.status}")
    except Exception as e:
        logger.error(f"API baglanti hatasi: {e}")
    return maclar


async def bildirim_gonder(bot, mac, puan, sinyaller, tahmin):
    if puan >= 9:
        karar = "🔥 KESİN GİR"
        emoji = "🔥"
    elif puan >= 7:
        karar = "✅ GİREBİLİRSİN"
        emoji = "✅"
    else:
        karar = "⚠️ DİKKATLİ OL"
        emoji = "⚠️"
    bar = "█" * puan + "░" * (12 - puan)
    mesaj = f"""{emoji} *{mac['ev']} {mac['ev_gol']}–{mac['dep_gol']} {mac['dep']}*
🏆 {mac['lig']}
⏱ *{mac['dakika']}. Dakika*

📊 *Sinyal: {puan}/12*
`{bar}`

*Aktif Sinyaller:*
{chr(10).join(sinyaller)}

━━━━━━━━━━━━
{karar}
💡 *{tahmin}*
━━━━━━━━━━━━
_Veri bazli analiz — risk mevcuttur_"""
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode=ParseMode.MARKDOWN)
        await sinyal_kaydet(mac, puan, tahmin)
        logger.info(f"Bildirim: {mac['ev']} vs {mac['dep']} — {puan} puan")
    except Exception as e:
        logger.error(f"Bildirim hatasi: {e}")


async def sonuc_bildir(bot, mac_id, ev, dep, tahmin, sonuc, fin_ev, fin_dep):
    emoji = "✅ TUTTU!" if sonuc == "TUTTU" else "❌ DÜŞTÜ!"
    mesaj = f"""📊 *SONUÇ: {ev} {fin_ev}–{fin_dep} {dep}*
{emoji}
💡 Tahmin: *{tahmin}*"""
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode=ParseMode.MARKDOWN)
        await sonuc_guncelle(mac_id, sonuc, fin_ev, fin_dep)
    except Exception as e:
        logger.error(f"Sonuc hatasi: {e}")


async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text="""🤖 *MAÇ ANALİZ BOTU AKTİF*

✅ API\-Football baglandi
✅ Veritabani baglandi
🎯 Min sinyal: 7/12

⏰ *Zamanlama:*
😴 00:00 \- 11:30 → Uyku
⚽ 11:30 \- 15:00 → 8 dk
🔥 15:00 \- 19:00 → 7 dk
🔥 19:00 \- 23:00 → 6 dk
😴 23:00 \- 00:00 → Uyku

📊 Sonuc \+ Rapor takibi aktif""",
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info("Bot basladi!")
    except Exception as e:
        logger.error(f"Baslangic hatasi: {e}")

    uyku_bildirimi = False
    son_haftalik = None
    son_aylik = None

    while True:
        try:
            simdi = datetime.now()
            bugun = simdi.date()

            if simdi.weekday() == 0 and simdi.hour == 9 and son_haftalik != bugun:
                await haftalik_rapor(bot)
                son_haftalik = bugun

            if simdi.day == 1 and simdi.hour == 9 and son_aylik != bugun:
                await aylik_rapor(bot)
                son_aylik = bugun

            sure = kontrol_suresi_al()

            if sure is None:
                if not uyku_bildirimi:
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text="😴 *UYKU MODU*\n\n🕐 11:30'da uyanacagim!\n_API kotasi korunuyor_",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    uyku_bildirimi = True
                    logger.info("Uyku moduna gecildi")
                await asyncio.sleep(1800)
                continue
            else:
                if uyku_bildirimi:
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"⚡ *UYANDIM!*\n🔍 Mac takibi basliyor\n⏱ Kontrol: {sure//60} dakika",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    uyku_bildirimi = False

            maclar = await macları_cek()
            aktif_idler = [m['id'] for m in maclar]

            for mac_id, bilgi in list(biten_maclar.items()):
                if mac_id not in aktif_idler:
                    sonuc = sonuc_kontrol(
                        bilgi['tahmin'],
                        bilgi['bas_ev'], bilgi['bas_dep'],
                        bilgi['son_ev'], bilgi['son_dep']
                    )
                    await sonuc_bildir(
                        bot, mac_id,
                        bilgi['ev'], bilgi['dep'],
                        bilgi['tahmin'], sonuc,
                        bilgi['son_ev'], bilgi['son_dep']
                    )
                    del biten_maclar[mac_id]
                    await asyncio.sleep(1)

            for mac in maclar:
                puan, sinyaller = sinyal_hesapla(mac)
                mac_id = mac['id']

                if mac_id in bildirim_gonderilen:
                    biten_maclar[mac_id] = {
                        'ev': mac['ev'], 'dep': mac['dep'],
                        'tahmin': bildirim_gonderilen[mac_id]['tahmin'],
                        'bas_ev': bildirim_gonderilen[mac_id]['ev_gol'],
                        'bas_dep': bildirim_gonderilen[mac_id]['dep_gol'],
                        'son_ev': mac['ev_gol'],
                        'son_dep': mac['dep_gol'],
                    }

                if puan >= MIN_PUAN:
                    onceki = bildirim_gonderilen.get(mac_id, {}).get('puan', 0)
                    if puan > onceki:
                        tahmin = tavsiye_uret(mac)
                        await bildirim_gonder(bot, mac, puan, sinyaller, tahmin)
                        bildirim_gonderilen[mac_id] = {
                            'puan': puan, 'tahmin': tahmin,
                            'ev_gol': mac['ev_gol'], 'dep_gol': mac['dep_gol']
                        }
                        await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Ana dongu hatasi: {e}")

        await asyncio.sleep(sure or 1800)


if __name__ == "__main__":
    asyncio.run(ana_dongu())
