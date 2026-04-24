import asyncio
import aiohttp
from telegram import Bot
from telegram.constants import ParseMode
import logging
import os
import asyncpg
from datetime import datetime, timedelta

# =============================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "7"))
# =============================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
db_pool = None


def kontrol_suresi_al():
    """Saate göre kontrol süresini belirle (saniye)"""
    saat = datetime.now().hour
    dakika = datetime.now().minute

    # 00:00 - 11:30 Uyku
    if saat < 11 or (saat == 11 and dakika < 30):
        return None, "uyku"

    # 11:30 - 15:00 Öğle (8 dakika)
    elif (saat == 11 and dakika >= 30) or (12 <= saat < 15):
        return 480, "öğle"

    # 15:00 - 19:00 Erken Akşam (7 dakika)
    elif 15 <= saat < 19:
        return 420, "erken_aksam"

    # 19:00 - 23:00 Ana Program (6 dakika)
    elif 19 <= saat < 23:
        return 360, "ana_program"

    # 23:00 - 00:00 Uyku
    else:
        return None, "uyku"


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
        logger.info("✅ Veritabanı bağlandı!")
    except Exception as e:
        logger.error(f"DB hatası: {e}")


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
        logger.error(f"Kayıt hatası: {e}")


async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        if db_pool:
            await db_pool.execute("""
                UPDATE sinyaller 
                SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3
                WHERE mac_id=$4 AND sonuc='BEKLIYOR'
            """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e:
        logger.error(f"Güncelleme hatası: {e}")


async def haftalik_rapor_gonder(bot):
    try:
        bir_hafta_once = datetime.now() - timedelta(days=7)
        rows = await db_pool.fetch("""
            SELECT * FROM sinyaller 
            WHERE bildirim_zamani > $1 AND sonuc != 'BEKLIYOR'
        """, bir_hafta_once)

        toplam = len(rows)
        if toplam == 0:
            return

        kazanan = len([r for r in rows if r['sonuc'] == 'TUTTU'])
        kaybeden = len([r for r in rows if r['sonuc'] == 'DSTU'])
        oran = round(kazanan / toplam * 100, 1)

        lig_stats = {}
        for r in rows:
            lig = r['lig']
            if lig not in lig_stats:
                lig_stats[lig] = {'k': 0, 't': 0}
            lig_stats[lig]['t'] += 1
            if r['sonuc'] == 'TUTTU':
                lig_stats[lig]['k'] += 1

        en_iyi_ligs = [(k, v) for k, v in lig_stats.items() if v['t'] >= 2]
        en_iyi_lig = max(en_iyi_ligs, key=lambda x: x[1]['k']/x[1]['t'], default=None)
        lig_str = f"{en_iyi_lig[0]} (%{round(en_iyi_lig[1]['k']/en_iyi_lig[1]['t']*100,1)})" if en_iyi_lig else "Yeterli veri yok"

        puan_7_8 = [r for r in rows if 7 <= r['puan'] <= 8]
        puan_9_plus = [r for r in rows if r['puan'] >= 9]
        p78_oran = round(len([r for r in puan_7_8 if r['sonuc']=='TUTTU'])/len(puan_7_8)*100, 1) if puan_7_8 else 0
        p9_oran = round(len([r for r in puan_9_plus if r['sonuc']=='TUTTU'])/len(puan_9_plus)*100, 1) if puan_9_plus else 0

        mesaj = f"""📊 *HAFTALIK RAPOR*
━━━━━━━━━━━━
📅 Son 7 Gün

📈 Toplam Sinyal: {toplam}
✅ Kazanan: {kazanan}
❌ Kaybeden: {kaybeden}
🎯 Başarı Oranı: *%{oran}*

📊 Puan 7-8: %{p78_oran} ({len(puan_7_8)} sinyal)
🔥 Puan 9+: %{p9_oran} ({len(puan_9_plus)} sinyal)
🏆 En İyi Lig: {lig_str}
━━━━━━━━━━━━
_Veriye dayalı analiz sistemi_"""

        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode=ParseMode.MARKDOWN)
        logger.info("Haftalık rapor gönderildi!")
    except Exception as e:
        logger.error(f"Haftalık rapor hatası: {e}")


async def aylik_rapor_gonder(bot):
    try:
        bir_ay_once = datetime.now() - timedelta(days=30)
        rows = await db_pool.fetch("""
            SELECT * FROM sinyaller 
            WHERE bildirim_zamani > $1 AND sonuc != 'BEKLIYOR'
        """, bir_ay_once)

        toplam = len(rows)
        if toplam == 0:
            await bot.send_message(
                chat_id=CHAT_ID,
                text="📊 *AYLIK RAPOR*\n\nHenüz yeterli veri yok.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        kazanan = len([r for r in rows if r['sonuc'] == 'TUTTU'])
        kaybeden = len([r for r in rows if r['sonuc'] == 'DSTU'])
        oran = round(kazanan / toplam * 100, 1)

        lig_stats = {}
        tahmin_stats = {}
        for r in rows:
            lig = r['lig']
            t = r['tahmin']
            if lig not in lig_stats:
                lig_stats[lig] = {'k': 0, 't': 0}
            if t not in tahmin_stats:
                tahmin_stats[t] = {'k': 0, 't': 0}
            lig_stats[lig]['t'] += 1
            tahmin_stats[t]['t'] += 1
            if r['sonuc'] == 'TUTTU':
                lig_stats[lig]['k'] += 1
                tahmin_stats[t]['k'] += 1

        en_iyi_ligs = [(k, v) for k, v in lig_stats.items() if v['t'] >= 3]
        en_iyi_lig = max(en_iyi_ligs, key=lambda x: x[1]['k']/x[1]['t'], default=None)
        en_iyi_tahminler = [(k, v) for k, v in tahmin_stats.items() if v['t'] >= 3]
        en_iyi_tahmin = max(en_iyi_tahminler, key=lambda x: x[1]['k']/x[1]['t'], default=None)

        lig_str = f"{en_iyi_lig[0]} (%{round(en_iyi_lig[1]['k']/en_iyi_lig[1]['t']*100,1)})" if en_iyi_lig else "Yeterli veri yok"
        tahmin_str = f"{en_iyi_tahmin[0]} (%{round(en_iyi_tahmin[1]['k']/en_iyi_tahmin[1]['t']*100,1)})" if en_iyi_tahmin else "Yeterli veri yok"

        mesaj = f"""📊 *AYLIK RAPOR*
━━━━━━━━━━━━
📅 Son 30 Gün

📈 Toplam Sinyal: {toplam}
✅ Kazanan: {kazanan}
❌ Kaybeden: {kaybeden}
🎯 Başarı Oranı: *%{oran}*

🏆 En İyi Lig: {lig_str}
💡 En İyi Tahmin: {tahmin_str}
━━━━━━━━━━━━
_Veriye dayalı analiz sistemi_"""

        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode=ParseMode.MARKDOWN)
        logger.info("Aylık rapor gönderildi!")
    except Exception as e:
        logger.error(f"Aylık rapor hatası: {e}")


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
        aktif.append(f"🔥 {toplam_gol} Gol!")
    if gol_hizi >= 0.10:
        puan += 1
        aktif.append(f"✅ Gol Hızı {gol_hizi}/dk")
    if gol_hizi >= 0.15:
        puan += 1
        aktif.append(f"🔥 Yüksek Hız {gol_hizi}/dk")
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
        aktif.append(f"🔥 Büyük Fark Erken!")

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


def sonuc_kontrol(tahmin, baslangic_ev, baslangic_dep, final_ev, final_dep):
    yeni_ev = final_ev - baslangic_ev
    yeni_dep = final_dep - baslangic_dep
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
                    logger.info(f"{len(fixtures)} canlı maç bulundu")

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
                            for stat in f.get('statistics', []):
                                if 'corner' in stat.get('type', '').lower():
                                    val = int(stat.get('value', 0) or 0)
                                    if stat.get('team', {}).get('id') == teams.get('home', {}).get('id'):
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
                    logger.warning("⚠️ API limit!")
                else:
                    logger.error(f"API hata: {resp.status}")
    except Exception as e:
        logger.error(f"API bağlantı hatası: {e}")
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
_Veri bazlı analiz — risk mevcuttur_"""

    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode=ParseMode.MARKDOWN)
        await sinyal_kaydet(mac, puan, tahmin)
        logger.info(f"✅ Bildirim: {mac['ev']} vs {mac['dep']} — {puan} puan")
    except Exception as e:
        logger.error(f"Bildirim hatası: {e}")


async def sonuc_bildirimi_gonder(bot, mac_id, ev, dep, tahmin, sonuc, final_ev, final_dep):
    emoji = "✅ TUTTU!" if sonuc == "TUTTU" else "❌ DÜŞTÜ!"
    mesaj = f"""📊 *SONUÇ: {ev} {final_ev}–{final_dep} {dep}*

{emoji}
💡 Tahmin: *{tahmin}*

_Maç tamamlandı_"""

    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode=ParseMode.MARKDOWN)
        await sonuc_guncelle(mac_id, sonuc, final_ev, final_dep)
        logger.info(f"Sonuç: {ev} vs {dep} — {sonuc}")
    except Exception as e:
        logger.error(f"Sonuç hatası: {e}")


async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()

    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"""🤖 *MAÇ ANALİZ BOTU AKTİF*

✅ API-Football bağlandı
✅ Veritabanı bağlandı
🎯 Minimum sinyal: {MIN_PUAN}/12

⏰ *Zamanlama:*
😴 00:00 - 11:30 → Uyku
⚽ 11:30 - 15:00 → 8 dk kontrol
🔥 15:00 - 19:00 → 7 dk kontrol
🔥 19:00 - 23:00 → 6 dk kontrol
😴 23:00 - 00:00 → Uyku

📊 Sonuç takibi aktif
📈 Haftalık + Aylık rapor aktif

_Güçlü sinyal bulunca otomatik bildirim gelecek!_""",
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info("Bot başladı!")
    except Exception as e:
        logger.error(f"Başlangıç hatası: {e}")

    uyku_bildirimi = False
    son_haftalik = None
    son_aylik = None

    while True:
        try:
            simdi = datetime.now()
            bugun = simdi.date()

            # Haftalık rapor — Pazartesi 09:00
            if simdi.weekday() == 0 and simdi.hour == 9 and son_haftalik != bugun:
                await haftalik_rapor_gonder(bot)
                son_haftalik = bugun

            # Aylık rapor — Ayın 1'i 09:00
            if simdi.day == 1 and simdi.hour == 9 and son_aylik != bugun:
                await aylik_rapor_gonder(bot)
                son_aylik = bugun

            # Kontrol süresini al
            sure, mod = kontrol_suresi_al()

            # Uyku modu
            if sure is None:
                if not uyku_bildirimi:
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text="😴 *UYKU MODU AKTİF*\n\n🕐 11:30'da uyanacağım!\n_API kotası korunuyor..._",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    uyku_bildirimi = True
                    logger.info("Uyku moduna geçildi")
                await asyncio.sleep(1800)
                continue
            else:
                if uyku_bildirimi:
                    mod_emoji = "⚽" if mod == "öğle" else "🔥"
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"{mod_emoji} *UYANDIM!*\n\n🔍 Maç takibi başlıyor...\n⏱ Kontrol süresi: {sure//60} dakika",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    uyku_bildirimi = False
                    logger.info(f"Uyku modundan çıkıldı — mod: {mod}")

            # Maçları çek ve analiz et
            maclar = await macları_cek()
            aktif_idler = [m['id'] for m in maclar]

            # Biten maçların sonuçlarını gönder
            for mac_id, bilgi in list(biten_maclar.items()):
                if mac_id not in aktif_idler:
                    sonuc = sonuc_kontrol(
                        bilgi['tahmin'],
                        bilgi['baslangic_ev'],
                        bilgi['baslangic_dep'],
                        bilgi['son_ev'],
                        bilgi['son_dep']
                    )
                    await sonuc_bildirimi_gonder(
                        bot, mac_id,
                        bilgi['ev'], bilgi['dep'],
                        bilgi['tahmin'], sonuc,
                        bilgi['son_ev'], bilgi['son_dep']
                    )
                    del biten_maclar[mac_id]
                    await asyncio.sleep(1)

            # Aktif maçları analiz et
            for mac in maclar:
                puan, sinyaller = sinyal_hesapla(mac)
                mac_id = mac['id']

                if mac_id in bildirim_gonderilen:
                    biten_maclar[mac_id] = {
                        'ev': mac['ev'],
                        'dep': mac['dep'],
                        'tahmin': bildirim_gonderilen[mac_id]['tahmin'],
                        'baslangic_ev': bildirim_gonderilen[mac_id]['ev_gol'],
                        'baslangic_dep': bildirim_gonderilen[mac_id]['dep_gol'],
                        'son_ev': mac['ev_gol'],
                        'son_dep': mac['dep_gol'],
                    }

                if puan >= MIN_PUAN:
                    onceki_puan = bildirim_gonderilen.get(mac_id, {}).get('puan', 0)
                    if puan > onceki_puan:
                        tahmin = tavsiye_uret(mac)
                        await bildirim_gonder(bot, mac, puan, sinyaller, tahmin)
                        bildirim_gonderilen[mac_id] = {
                            'puan': puan,
                            'tahmin': tahmin,
                            'ev_gol': mac['ev_gol'],
                            'dep_gol': mac['dep_gol']
                        }
                        await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}")

        await asyncio.sleep(sure or 1800)


if __name__ == "__main__":
    asyncio.run(ana_dongu())
