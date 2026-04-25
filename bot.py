import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta
import json

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
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
        return 480
    elif 15 <= saat < 19:
        return 420
    elif 19 <= saat < 23:
        return 360
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
                ai_yorum TEXT,
                kasa_yuzde REAL,
                bildirim_zamani TIMESTAMP DEFAULT NOW(),
                sonuc TEXT DEFAULT 'BEKLIYOR',
                final_ev_gol INTEGER DEFAULT 0,
                final_dep_gol INTEGER DEFAULT 0
            )
        """)
        logger.info("Veritabani baglandi!")
    except Exception as e:
        logger.error(f"DB hatasi: {e}")


async def sinyal_kaydet(mac, puan, tahmin, ai_yorum, kasa_yuzde):
    try:
        if db_pool:
            await db_pool.execute("""
                INSERT INTO sinyaller
                (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol, puan, tahmin, ai_yorum, kasa_yuzde)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            """, mac['id'], mac['ev'], mac['dep'], mac['lig'],
                mac['dakika'], mac['ev_gol'], mac['dep_gol'],
                puan, tahmin, ai_yorum, kasa_yuzde)
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


async def gemini_analiz(mac, puan, tahmin):
    """Gemini AI ile eleştirel analiz yap"""
    if not GEMINI_KEY:
        return "AI analiz aktif degil.", 1.5

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"

    prompt = f"""Sen profesyonel bir canlı bahis analistsin. Aşağıdaki maçı eleştirel gözle değerlendir:

MAÇ BİLGİLERİ:
- Maç: {mac['ev']} vs {mac['dep']}
- Lig: {mac['lig']}
- Dakika: {mac['dakika']}
- Skor: {mac['ev_gol']}-{mac['dep_gol']}
- Corner: {mac['ev_corner']}-{mac['dep_corner']}
- Son gol dakikası: {mac['son_gol']}
- İstatistik puanı: {puan}/12
- Önerilen tahmin: {tahmin}

GÖREVIN:
1. Bu maçı 2-3 cümle ile eleştirel analiz et. Sadece istatistiklere değil, mantıksal çıkarıma da bak.
2. Riski değerlendir: DUSUK, ORTA veya YUKSEK
3. Kasa önerisi: puan 9-12 ve risk DUSUK ise %4, puan 7-8 ve risk DUSUK/ORTA ise %1.5, risk YUKSEK ise %0 (girme)

CEVAP FORMATI (sadece JSON):
{{"yorum": "kısa analiz", "risk": "DUSUK/ORTA/YUKSEK", "kasa_yuzde": 0.0, "girilmeli": true/false}}"""

    try:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 300}
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    # JSON parse
                    text = text.strip()
                    if "```" in text:
                        text = text.split("```")[1].replace("json", "").strip()
                    result = json.loads(text)
                    yorum = result.get('yorum', 'Analiz yapılamadı')
                    kasa = float(result.get('kasa_yuzde', 1.5))
                    girilmeli = result.get('girilmeli', True)
                    if not girilmeli:
                        kasa = 0.0
                    return yorum, kasa
                else:
                    logger.error(f"Gemini hata: {resp.status}")
                    return "AI analiz yapılamadı.", 1.5
    except Exception as e:
        logger.error(f"Gemini hatasi: {e}")
        return "AI analiz yapılamadı.", 1.5


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
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"HAFTALIK RAPOR\nSon 7 Gun\n\nToplam: {toplam}\nKazanan: {kazanan}\nKaybeden: {kaybeden}\nBasari: %{oran}"
        )
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
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"AYLIK RAPOR\nSon 30 Gun\n\nToplam: {toplam}\nKazanan: {kazanan}\nKaybeden: {kaybeden}\nBasari: %{oran}"
        )
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
        aktif.append("KG VAR")
    if gol_fark >= 2:
        puan += 1
        aktif.append(f"Gol Farki {gol_fark}")
    if toplam_gol >= 3:
        puan += 1
        aktif.append(f"Toplam Gol {toplam_gol}")
    if toplam_gol >= 4:
        puan += 1
        aktif.append(f"{toplam_gol} Gol!")
    if gol_hizi >= 0.10:
        puan += 1
        aktif.append(f"Gol Hizi {gol_hizi}/dk")
    if gol_hizi >= 0.15:
        puan += 1
        aktif.append(f"Yuksek Hiz {gol_hizi}/dk")
    if son_gol >= 70:
        puan += 1
        aktif.append(f"Son Gol {son_gol}dk")
    if corner_fark >= 5:
        puan += 1
        aktif.append(f"Corner {ev_corner}-{dep_corner}")
    if dakika <= 30 and toplam_gol >= 2:
        puan += 1
        aktif.append(f"Erken+Gol ({dakika}dk)")
    if gol_fark >= 3 and dakika <= 30:
        puan += 1
        aktif.append("Buyuk Fark Erken!")
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
    url = "https://v3.football.api-sports.io/fixtures?live=all"
    headers = {
        "x-apisports-key": APISPORTS_KEY,
        "x-apisports-host": "v3.football.api-sports.io"
    }
    maclar = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    errors = data.get('errors', {})
                    if errors:
                        logger.error(f"API errors: {errors}")
                        return maclar
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
                else:
                    logger.error(f"API hata kodu: {resp.status}")
    except Exception as e:
        logger.error(f"API baglanti hatasi: {e}")
    return maclar


async def bildirim_gonder(bot, mac, puan, sinyaller, tahmin, ai_yorum, kasa_yuzde):
    # Kasa yönetimi
    if kasa_yuzde == 0:
        # AI girme dedi — sadece uyarı gönder
        mesaj = (
            f"⚠️ AI UYARISI: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
            f"Sinyal: {puan}/12 - Yüksek puan ama AI riskli buldu!\n\n"
            f"🧠 AI Analiz: {ai_yorum}\n\n"
            f"❌ GIRME - Kasa korunuyor!"
        )
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, tahmin, ai_yorum, kasa_yuzde)
        return

    # Karar emojisi
    if puan >= 9 and kasa_yuzde >= 4:
        karar = "KESIN GIR"
        emoji = "🔥"
    elif puan >= 7 and kasa_yuzde >= 1.5:
        karar = "GIREBILIRSiN"
        emoji = "✅"
    else:
        karar = "DIKKATLI OL"
        emoji = "⚠️"

    bar = "█" * puan + "░" * (12 - puan)
    sinyal_metni = "\n".join(sinyaller)

    mesaj = (
        f"{emoji} {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"Lig: {mac['lig']}\n"
        f"Dakika: {mac['dakika']}\n\n"
        f"Sinyal: {puan}/12\n"
        f"{bar}\n\n"
        f"Sinyaller:\n{sinyal_metni}\n\n"
        f"🧠 AI ANALiZ:\n{ai_yorum}\n\n"
        f"💰 KASA YONETiMi:\n"
        f"Kasanin %{kasa_yuzde}'ini kullan\n\n"
        f"---\n"
        f"{karar}\n"
        f"Tahmin: {tahmin}\n"
        f"---\n"
        f"Istatistikler yaniltici olabilir, disiplinli kal!"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, tahmin, ai_yorum, kasa_yuzde)
        logger.info(f"Bildirim: {mac['ev']} vs {mac['dep']} - {puan} puan - %{kasa_yuzde} kasa")
    except Exception as e:
        logger.error(f"Bildirim hatasi: {e}")


async def sonuc_bildir(bot, mac_id, ev, dep, tahmin, sonuc, fin_ev, fin_dep):
    emoji = "TUTTU!" if sonuc == "TUTTU" else "DUSTU!"
    mesaj = (
        f"SONUC: {ev} {fin_ev}-{fin_dep} {dep}\n"
        f"{emoji}\n"
        f"Tahmin: {tahmin}"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sonuc_guncelle(mac_id, sonuc, fin_ev, fin_dep)
        logger.info(f"Sonuc: {ev} vs {dep} - {sonuc}")
    except Exception as e:
        logger.error(f"Sonuc hatasi: {e}")


async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()

    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "MAC ANALIZ BOTU AKTIF\n\n"
                "API-Football baglandi\n"
                "Veritabani baglandi\n"
                "Gemini AI baglandi\n"
                f"Min sinyal: {MIN_PUAN}/12\n\n"
                "Zamanlama:\n"
                "00:00-11:30 Uyku\n"
                "11:30-15:00 (8 dk)\n"
                "15:00-19:00 (7 dk)\n"
                "19:00-23:00 (6 dk)\n"
                "23:00-00:00 Uyku\n\n"
                "AI Analiz + Kasa Yonetimi aktif!\n"
                "Guclu sinyal bulunca bildirim gelecek!"
            )
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
                        text="UYKU MODU\n11:30'da uyanacagim!\nAPI kotasi korunuyor."
                    )
                    uyku_bildirimi = True
                    logger.info("Uyku moduna gecildi")
                await asyncio.sleep(1800)
                continue
            else:
                if uyku_bildirimi:
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"UYANDIM!\nMac takibi basliyor\nKontrol: {sure//60} dakika"
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
                        # Gemini AI analiz
                        ai_yorum, kasa_yuzde = await gemini_analiz(mac, puan, tahmin)
                        await bildirim_gonder(bot, mac, puan, sinyaller, tahmin, ai_yorum, kasa_yuzde)
                        bildirim_gonderilen[mac_id] = {
                            'puan': puan, 'tahmin': tahmin,
                            'ev_gol': mac['ev_gol'], 'dep_gol': mac['dep_gol']
                        }
                        await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Ana dongu hatasi: {e}")

        await asyncio.sleep(sure or 1800)


if __name__ == "__main__":
    logger.info("BOT STARTED")
    asyncio.run(ana_dongu())
