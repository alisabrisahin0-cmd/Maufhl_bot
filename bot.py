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
KONTROL_SURESI = 120
# =============================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
db_pool = None


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
        await db_pool.execute("""
            UPDATE sinyaller 
            SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3
            WHERE mac_id=$4 AND sonuc='BEKLIYOR'
        """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e:
        logger.error(f"Güncelleme hatası: {e}")


async def aylik_rapor_gonder(bot):
    try:
        bir_ay_once = datetime.now() - timedelta(days=30)
        rows = await db_pool.fetch("""
            SELECT * FROM sinyaller 
            WHERE bildirim_zamani > $1 AND sonuc != 'BEKLIYOR'
        """, bir_ay_once)

        if not rows:
            await bot.send_message(
                chat_id=CHAT_ID,
                text="📊 *AYLIK RAPOR*\n\nHenüz yeterli veri yok.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        toplam = len(rows)
        kazanan = len([r for r in rows if r['sonuc'] == 'TUTTU'])
        kaybeden = len([r for r in rows if r['sonuc'] == 'DSTU'])
        oran = round(kazanan / toplam * 100, 1) if toplam > 0 else 0

        # En iyi lig
        lig_stats = {}
        for r in rows:
            lig = r['lig']
            if lig not in lig_stats:
                lig_stats[lig] = {'k': 0, 't': 0}
            lig_stats[lig]['t'] += 1
            if r['sonuc'] == 'TUTTU':
                lig_stats[lig]['k'] += 1

        en_iyi_lig = max(
            [(k, v) for k, v in lig_stats.items() if v['t'] >= 3],
            key=lambda x: x[1]['k'] / x[1]['t'],
            default=('Yeterli veri yok', {'k': 0, 't': 0})
        )

        # En iyi tahmin
        tahmin_stats = {}
        for r in rows:
            t = r['tahmin']
            if t not in tahmin_stats:
                tahmin_stats[t] = {'k': 0, 't': 0}
            tahmin_stats[t]['t'] += 1
            if r['sonuc'] == 'TUTTU':
                tahmin_stats[t]['k'] += 1

        en_iyi_tahmin = max(
            [(k, v) for k, v in tahmin_stats.items() if v['t'] >= 3],
            key=lambda x: x[1]['k'] / x[1]['t'],
            default=('Yeterli veri yok', {'k': 0, 't': 0})
        )

        mesaj = f"""📊 *AYLIK RAPOR*
━━━━━━━━━━━━
📅 Son 30 Gün

📈 Toplam Sinyal: {toplam}
✅ Kazanan: {kazanan}
❌ Kaybeden: {kaybeden}
🎯 Başarı Oranı: *%{oran}*

🏆 En İyi Lig: {en_iyi_lig[0]}
💡 En İyi Tahmin: {en_iyi_tahmin[0]}
━━━━━━━━━━━━
_Veriye dayalı analiz sistemi_"""

        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode=ParseMode.MARKDOWN)
        logger.info("Aylık rapor gönderildi!")

    except Exception as e:
        logger.error(f"Rapor hatası: {e}")


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
                            ev = event.get('homeTeam', {}).get('name', '?')
                            dep = event.get('awayTeam', {}).get('name', '?')
                            lig = event.get('league', {}).get('name', '?')
                            mac_id = str(event.get('id', ''))
                            dakika = int(event.get('minute', 0) or 0)
                            score = event.get('score', {})
                            ev_gol = int(score.get('home', 0) or 0)
                            dep_gol = int(score.get('away', 0) or 0)
                            ev_corner = dep_corner = son_gol = 0
                            for stat in event.get('stats', []):
                                if stat.get('type', '').lower() == 'corner kicks':
                                    ev_corner = int(stat.get('home', 0) or 0)
                                    dep_corner = int(stat.get('away', 0) or 0)
                            goals = event.get('goals', [])
                            if goals:
                                son_gol = max([int(g.get('minute', 0) or 0) for g in goals])
                            if dakika < 5 or dakika > 85:
                                continue
                            maclar.append({
                                'id': mac_id, 'ev': ev, 'dep': dep, 'lig': lig,
                                'dakika': dakika, 'ev_gol': ev_gol, 'dep_gol': dep_gol,
                                'ev_corner': ev_corner, 'dep_corner': dep_corner,
                                'son_gol': son_gol
                            })
                        except:
                            continue
    except Exception as e:
        logger.error(f"API hatası: {e}")
    return maclar


async def bildirim_gonder(bot, mac, puan, sinyaller, tahmin):
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
        logger.info(f"Sonuç bildirimi: {ev} vs {dep} — {sonuc}")
    except Exception as e:
        logger.error(f"Sonuç hatası: {e}")


async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()

    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text="🤖 *MAÇ ANALİZ BOTU AKTİF*\n\n✅ RapidAPI bağlandı\n✅ Veritabanı bağlandı\n📡 Her 2 dakikada kontrol\n🎯 Minimum sinyal: 7/12\n📊 Sonuç takibi aktif\n📈 Aylık rapor aktif\n\n_Güçlü sinyal bulunca otomatik bildirim gelecek!_",
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info("Bot başladı!")
    except Exception as e:
        logger.error(f"Başlangıç hatası: {e}")

    son_rapor_gunu = datetime.now().day

    while True:
        try:
            maclar = await macları_cek()
            aktif_idler = [m['id'] for m in maclar]

            # Biten maçları kontrol et
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

            # Aktif maçları işle
            for mac in maclar:
                puan, sinyaller = sinyal_hesapla(mac)
                mac_id = mac['id']

                # Takip listesini güncelle
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

            # Aylık rapor — her ayın 1'inde
            bugun = datetime.now().day
            if bugun == 1 and son_rapor_gunu != 1:
                await aylik_rapor_gonder(bot)
            son_rapor_gunu = bugun

        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}")

        await asyncio.sleep(KONTROL_SURESI)


if __name__ == "__main__":
    asyncio.run(ana_dongu())
