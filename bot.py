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
MIN_PUAN = int(os.getenv("MIN_PUAN", "6"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
db_pool = None


# ================================================
# ZAMAN YÖNETİMİ
# ================================================
def kontrol_suresi_al():
    saat = datetime.now().hour
    dk = datetime.now().minute
    if saat < 11 or (saat == 11 and dk < 30):
        return None
    elif (saat == 11 and dk >= 30) or (12 <= saat < 15):
        return 480   # 8 dk
    elif 15 <= saat < 19:
        return 420   # 7 dk
    elif 19 <= saat < 23:
        return 360   # 6 dk
    else:
        return None


# ================================================
# VERİTABANI
# ================================================
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
                strateji TEXT,
                tahmin TEXT,
                ai_yorum TEXT,
                kasa_yuzde REAL,
                bildirim_zamani TIMESTAMP DEFAULT NOW(),
                sonuc TEXT DEFAULT 'BEKLIYOR',
                final_ev_gol INTEGER DEFAULT 0,
                final_dep_gol INTEGER DEFAULT 0
            )
        """)
        for kolon, tip in [
            ("ai_yorum", "TEXT"),
            ("kasa_yuzde", "REAL"),
            ("strateji", "TEXT")
        ]:
            try:
                await db_pool.execute(
                    f"ALTER TABLE sinyaller ADD COLUMN IF NOT EXISTS {kolon} {tip}"
                )
            except:
                pass
        logger.info("Veritabani baglandi!")
    except Exception as e:
        logger.error(f"DB hatasi: {e}")


async def sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa_yuzde):
    try:
        if db_pool:
            await db_pool.execute("""
                INSERT INTO sinyaller
                (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol, puan, strateji, tahmin, ai_yorum, kasa_yuzde)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """, mac['id'], mac['ev'], mac['dep'], mac['lig'],
                mac['dakika'], mac['ev_gol'], mac['dep_gol'],
                puan, strateji, tahmin, ai_yorum, kasa_yuzde)
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


# ================================================
# GEMİNİ AI ANALİZ
# ================================================
async def gemini_analiz(mac, puan, strateji, tahmin):
    if not GEMINI_KEY:
        return "AI analiz aktif degil.", 1.5

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"

    prompt = f"""Sen profesyonel bir canli bahis analistsin. Su maci elestirisel gozle degerlendir:

MAC: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}
Lig: {mac['lig']}
Dakika: {mac['dakika']}
Toplam gol: {mac['ev_gol'] + mac['dep_gol']}
Son gol: {mac['son_gol']}. dk
Isabetli sut (ev/dep): {mac['shots_on_target_ev']}/{mac['shots_on_target_dep']}
Top hakimiyeti: {mac['possession_ev']}% / {mac['possession_dep']}%
Tehlikeli atak: {mac['dangerous_attacks_ev']} / {mac['dangerous_attacks_dep']}
Sari kart: {mac['sari_kart_ev']} / {mac['sari_kart_dep']}
Strateji: {strateji}
Tahmin: {tahmin}
Puan: {puan}/12

GOREV:
1. Bu maci 2 cumle ile elestirisel analiz et
2. Risk seviyesi: DUSUK / ORTA / YUKSEK
3. Kasa onerisi: DUSUK=%4 / ORTA=%1.5 / YUKSEK=%0

Sadece JSON don:
{{"yorum": "analiz metni", "risk": "DUSUK", "kasa_yuzde": 4.0, "girilmeli": true}}"""

    try:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 250}
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text'].strip()
                    if "```" in text:
                        text = text.split("```")[1].replace("json", "").strip()
                    result = json.loads(text)
                    yorum = result.get('yorum', 'Analiz yapılamadı')
                    kasa = float(result.get('kasa_yuzde', 1.5))
                    if not result.get('girilmeli', True):
                        kasa = 0.0
                    logger.info(f"Gemini OK: {yorum[:50]}")
                    return yorum, kasa
                else:
                    logger.error(f"Gemini hata {resp.status}")
                    return "AI analiz yapilamadi.", 1.5
    except Exception as e:
        logger.error(f"Gemini hatasi: {e}")
        return "AI analiz yapilamadi.", 1.5


# ================================================
# RAPORLAR
# ================================================
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

        # Strateji bazlı analiz
        strateji_stats = {}
        for r in rows:
            s = r.get('strateji', 'Diger')
            if s not in strateji_stats:
                strateji_stats[s] = {'k': 0, 't': 0}
            strateji_stats[s]['t'] += 1
            if r['sonuc'] == 'TUTTU':
                strateji_stats[s]['k'] += 1

        en_iyi = max(
            [(k, v) for k, v in strateji_stats.items() if v['t'] >= 2],
            key=lambda x: x[1]['k'] / x[1]['t'],
            default=('Yok', {'k': 0, 't': 1})
        )
        en_iyi_oran = round(en_iyi[1]['k'] / en_iyi[1]['t'] * 100, 1)

        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"HAFTALIK RAPOR - Son 7 Gun\n\n"
                f"Toplam: {toplam}\n"
                f"Kazanan: {kazanan}\n"
                f"Kaybeden: {kaybeden}\n"
                f"Basari: %{oran}\n\n"
                f"En iyi strateji: {en_iyi[0]} (%{en_iyi_oran})"
            )
        )
    except Exception as e:
        logger.error(f"Haftalik rapor: {e}")


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
            text=(
                f"AYLIK RAPOR - Son 30 Gun\n\n"
                f"Toplam: {toplam}\n"
                f"Kazanan: {kazanan}\n"
                f"Kaybeden: {kaybeden}\n"
                f"Basari: %{oran}"
            )
        )
    except Exception as e:
        logger.error(f"Aylik rapor: {e}")


# ================================================
# GELİŞMİŞ SİNYAL SİSTEMİ
# ================================================
def sinyal_hesapla(mac):
    """
    Yeni sinyal sistemi — gol + istatistik bazlı
    Corner yok ama: shots, possession, dangerous attacks var
    """
    puan = 0
    aktif = []
    stratejiler = []

    dakika = mac.get('dakika', 0)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    son_gol = mac.get('son_gol', 0)

    # İstatistikler
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    shots_toplam = shots_ev + shots_dep
    possession_ev = mac.get('possession_ev', 50)
    possession_dep = mac.get('possession_dep', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    dangerous_toplam = dangerous_ev + dangerous_dep
    sari_ev = mac.get('sari_kart_ev', 0)
    sari_dep = mac.get('sari_kart_dep', 0)
    kirmizi = mac.get('kirmizi_kart', 0)

    toplam_gol = ev_gol + dep_gol
    gol_fark = abs(ev_gol - dep_gol)
    kg_var = ev_gol > 0 and dep_gol > 0
    gol_hizi = round(toplam_gol / dakika, 3) if dakika > 0 else 0

    # ---- GOL BAZLI SİNYALLER ----
    if kg_var:
        puan += 1
        aktif.append("KG VAR")

    if gol_fark >= 2:
        puan += 1
        aktif.append(f"Gol Farki {gol_fark}")

    if toplam_gol >= 3:
        puan += 1
        aktif.append(f"{toplam_gol} Gol")

    if toplam_gol >= 4:
        puan += 1
        aktif.append(f"{toplam_gol} Gol YUKSEK!")
        stratejiler.append("GOL_PATLAMASI")

    if gol_hizi >= 0.10:
        puan += 1
        aktif.append(f"Hiz {gol_hizi}/dk")

    if son_gol >= 70:
        puan += 1
        aktif.append(f"Son Gol {son_gol}dk")

    if gol_fark >= 3 and dakika <= 30:
        puan += 1
        aktif.append("Buyuk Fark Erken!")
        stratejiler.append("BUYUK_FARK_ERKEN")

    # ---- YENİ: SHOTS ON TARGET SİNYALLERİ ----
    if shots_toplam >= 8:
        puan += 1
        aktif.append(f"Isabetli Sut {shots_toplam}")
        stratejiler.append("YUKSEK_SOOT")

    if shots_toplam >= 12:
        puan += 1
        aktif.append(f"COK Isabetli Sut {shots_toplam}!")

    # Bir takım dominant shots yapıyor
    shots_fark = abs(shots_ev - shots_dep)
    if shots_fark >= 5:
        puan += 1
        dominant = mac['ev'] if shots_ev > shots_dep else mac['dep']
        aktif.append(f"Sut Dominant: {dominant[:15]}")
        stratejiler.append("SUT_DOMINANT")

    # ---- YENİ: POSSESSİON SİNYALLERİ ----
    possession_fark = abs(possession_ev - possession_dep)
    if possession_fark >= 20:
        puan += 1
        dominant = mac['ev'] if possession_ev > possession_dep else mac['dep']
        aktif.append(f"Top Hakimiyeti: {dominant[:15]}")
        stratejiler.append("POSSESSION_DOMINANT")

    # ---- YENİ: DANGEROUS ATTACKS SİNYALLERİ ----
    if dangerous_toplam >= 60:
        puan += 1
        aktif.append(f"Tehlikeli Atak {dangerous_toplam}")
        stratejiler.append("YUKSEK_ATAK")

    if dangerous_toplam >= 100:
        puan += 1
        aktif.append(f"COK Tehlikeli! {dangerous_toplam}")

    # ---- YENİ: KART SİNYALLERİ ----
    toplam_sari = sari_ev + sari_dep
    if toplam_sari >= 4:
        puan += 1
        aktif.append(f"Kart Yagmuru {toplam_sari}")
        stratejiler.append("KART_OYUNU")

    if kirmizi >= 1:
        puan += 1
        aktif.append(f"Kirmizi Kart VAR!")
        stratejiler.append("KIRMIZI_KART")

    # ---- YENİ: ÖZEL SENARYO SİNYALLERİ ----

    # Senaryo 1: 0-0 ama yüksek aktivite → gol gelecek
    if toplam_gol == 0 and shots_toplam >= 8 and dangerous_toplam >= 50 and dakika >= 30:
        puan += 2
        aktif.append("0-0 Ama COK Aktif! GOL BEKLENIYOR")
        stratejiler.append("GOLSUZ_AKTIF")

    # Senaryo 2: Eşit skor + dominant takım + geç dakika
    if toplam_gol >= 1 and gol_fark == 0 and possession_fark >= 15 and dakika >= 60:
        puan += 1
        aktif.append("Esit + Dominant + Gec Dakika")
        stratejiler.append("DONME_SENARYOSU")

    # Senaryo 3: İlk yarı baskı ikinci yarı gol
    if dakika >= 45 and dakika <= 60 and shots_toplam >= 6:
        puan += 1
        aktif.append(f"2.Yari Basladi + {shots_toplam} Sut")
        stratejiler.append("IY_BASKISI_2Y")

    # Senaryo 4: Kırmızı kart var → açık alan
    if kirmizi >= 1 and dakika <= 70:
        puan += 1
        aktif.append("Kirmizi + Erken → Acik Alan")
        stratejiler.append("KIRMIZI_ERKEN")

    # Senaryo 5: Gol farkı 1, geç dakika, kaybeden dominant
    if gol_fark == 1 and dakika >= 65:
        if (ev_gol < dep_gol and possession_ev >= 55) or (dep_gol < ev_gol and possession_dep >= 55):
            puan += 1
            aktif.append("Kaybeden Dominant + Son 25dk")
            stratejiler.append("GERI_DONME")

    strateji_adi = stratejiler[0] if stratejiler else "GENEL"
    return puan, aktif, strateji_adi


def tavsiye_uret(mac, strateji):
    """Stratejiye göre en iyi tahmin"""
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    gol_fark = ev_gol - dep_gol
    toplam_gol = ev_gol + dep_gol
    possession_ev = mac.get('possession_ev', 50)
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)

    if strateji == "GOL_PATLAMASI":
        if gol_fark >= 2:
            return "EV GOL ATACAK (S)"
        elif gol_fark <= -2:
            return "DEP GOL ATACAK (S)"
        return "GOL OLACAK (S)"

    elif strateji == "GOLSUZ_AKTIF":
        if shots_ev > shots_dep and possession_ev >= 55:
            return "EV GOL ATACAK (S)"
        elif shots_dep > shots_ev:
            return "DEP GOL ATACAK (S)"
        return "GOL OLACAK (S)"

    elif strateji == "SUT_DOMINANT":
        if shots_ev > shots_dep:
            return "EV GOL ATACAK (S)"
        return "DEP GOL ATACAK (S)"

    elif strateji == "POSSESSION_DOMINANT":
        if possession_ev > 60:
            return "EV GOL ATACAK (S)"
        return "DEP GOL ATACAK (S)"

    elif strateji == "DONME_SENARYOSU":
        if possession_ev >= 55 and dep_gol > ev_gol:
            return "EV KAZANIR VEYA BERABERE"
        elif possession_ev < 45 and ev_gol > dep_gol:
            return "DEP KAZANIR VEYA BERABERE"
        return "GOL OLACAK (S)"

    elif strateji == "GERI_DONME":
        if ev_gol < dep_gol:
            return "EV KAZANIR VEYA BERABERE"
        return "DEP KAZANIR VEYA BERABERE"

    elif strateji == "KIRMIZI_KART" or strateji == "KIRMIZI_ERKEN":
        return "GOL OLACAK (S)"

    elif strateji == "IY_BASKISI_2Y":
        return "GOL OLACAK (S)"

    # Genel
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
    if "GOL OLACAK" in tahmin:
        return "TUTTU" if yeni_toplam >= 1 else "DSTU"
    elif "EV GOL" in tahmin:
        return "TUTTU" if yeni_ev >= 1 else "DSTU"
    elif "DEP GOL" in tahmin:
        return "TUTTU" if yeni_dep >= 1 else "DSTU"
    elif "EV KAZANIR" in tahmin:
        return "TUTTU" if fin_ev >= fin_dep else "DSTU"
    elif "DEP KAZANIR" in tahmin:
        return "TUTTU" if fin_dep >= fin_ev else "DSTU"
    return "BELIRSIZ"


# ================================================
# VERİ ÇEKME — TÜM İSTATİSTİKLERLE
# ================================================
async def macları_cek():
    url = "https://v3.football.api-sports.io/fixtures?live=all"
    headers = {
        "x-apisports-key": APISPORTS_KEY,
        "x-apisports-host": "v3.football.api-sports.io"
    }
    maclar = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
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

                            if dakika < 5 or dakika > 88:
                                continue

                            ev_gol = int(goals.get('home', 0) or 0)
                            dep_gol = int(goals.get('away', 0) or 0)

                            # Tüm istatistikleri çek
                            home_id = teams.get('home', {}).get('id')
                            stats = {
                                'shots_on_target_ev': 0,
                                'shots_on_target_dep': 0,
                                'shots_total_ev': 0,
                                'shots_total_dep': 0,
                                'possession_ev': 50,
                                'possession_dep': 50,
                                'dangerous_attacks_ev': 0,
                                'dangerous_attacks_dep': 0,
                                'sari_kart_ev': 0,
                                'sari_kart_dep': 0,
                                'kirmizi_kart': 0,
                                'son_gol': 0,
                            }

                            for stat_group in f.get('statistics', []):
                                team_id = stat_group.get('team', {}).get('id')
                                is_home = (team_id == home_id)
                                for s in stat_group.get('statistics', []):
                                    tip = s.get('type', '').lower()
                                    val = s.get('value', 0)
                                    if val is None:
                                        val = 0
                                    if isinstance(val, str) and '%' in val:
                                        val = int(val.replace('%', '').strip())
                                    else:
                                        try:
                                            val = int(val)
                                        except:
                                            val = 0

                                    if 'shots on goal' in tip or 'shots on target' in tip:
                                        if is_home:
                                            stats['shots_on_target_ev'] = val
                                        else:
                                            stats['shots_on_target_dep'] = val
                                    elif 'total shots' in tip:
                                        if is_home:
                                            stats['shots_total_ev'] = val
                                        else:
                                            stats['shots_total_dep'] = val
                                    elif 'ball possession' in tip:
                                        if is_home:
                                            stats['possession_ev'] = val
                                        else:
                                            stats['possession_dep'] = val
                                    elif 'dangerous attacks' in tip:
                                        if is_home:
                                            stats['dangerous_attacks_ev'] = val
                                        else:
                                            stats['dangerous_attacks_dep'] = val
                                    elif 'yellow cards' in tip:
                                        if is_home:
                                            stats['sari_kart_ev'] = val
                                        else:
                                            stats['sari_kart_dep'] = val
                                    elif 'red cards' in tip:
                                        stats['kirmizi_kart'] += val

                            # Son gol dakikası
                            son_gol = 0
                            for event in f.get('events', []):
                                if event.get('type') == 'Goal':
                                    gdk = int(event.get('time', {}).get('elapsed', 0) or 0)
                                    if gdk > son_gol:
                                        son_gol = gdk
                            stats['son_gol'] = son_gol

                            maclar.append({
                                'id': mac_id,
                                'ev': ev,
                                'dep': dep,
                                'lig': lig,
                                'dakika': dakika,
                                'ev_gol': ev_gol,
                                'dep_gol': dep_gol,
                                **stats
                            })
                        except Exception as e:
                            logger.error(f"Mac parse hatasi: {e}")
                            continue
                else:
                    logger.error(f"API hata: {resp.status}")
    except Exception as e:
        logger.error(f"API baglanti: {e}")
    return maclar


# ================================================
# BİLDİRİM
# ================================================
async def bildirim_gonder(bot, mac, puan, sinyaller, strateji, tahmin, ai_yorum, kasa_yuzde):
    if kasa_yuzde == 0:
        mesaj = (
            f"AI UYARISI: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
            f"Sinyal {puan}/12 ama AI riskli buldu!\n"
            f"AI: {ai_yorum}\n"
            f"GIRME - Kasa korunuyor!"
        )
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa_yuzde)
        return

    if puan >= 9 and kasa_yuzde >= 4:
        karar = "KESIN GIR"
        emoji = "🔥"
    elif puan >= 7:
        karar = "GIREBILIRSiN"
        emoji = "✅"
    else:
        karar = "DIKKATLI OL"
        emoji = "⚠️"

    bar = "█" * min(puan, 12) + "░" * max(0, 12 - puan)
    sinyal_metni = "\n".join(sinyaller)

    # İstatistikleri göster
    istat = (
        f"Sut: {mac['shots_on_target_ev']}/{mac['shots_on_target_dep']} (isabetli)\n"
        f"Top: %{mac['possession_ev']}/%{mac['possession_dep']}\n"
        f"Atak: {mac['dangerous_attacks_ev']}/{mac['dangerous_attacks_dep']}"
    )

    mesaj = (
        f"{emoji} {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"Lig: {mac['lig']}\n"
        f"Dakika: {mac['dakika']}\n\n"
        f"Sinyal: {puan}/12\n"
        f"{bar}\n\n"
        f"Sinyaller:\n{sinyal_metni}\n\n"
        f"Istatistikler:\n{istat}\n\n"
        f"Strateji: {strateji}\n\n"
        f"AI ANALiZ:\n{ai_yorum}\n\n"
        f"KASA: %{kasa_yuzde} kullan\n\n"
        f"---\n"
        f"{karar} | {tahmin}\n"
        f"---"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa_yuzde)
        logger.info(f"Bildirim: {mac['ev']} vs {mac['dep']} | {strateji} | {puan}p | %{kasa_yuzde}")
    except Exception as e:
        logger.error(f"Bildirim hatasi: {e}")


async def sonuc_bildir(bot, mac_id, ev, dep, tahmin, sonuc, fin_ev, fin_dep):
    emoji = "TUTTU!" if sonuc == "TUTTU" else "DUSTU!"
    mesaj = f"SONUC: {ev} {fin_ev}-{fin_dep} {dep}\n{emoji}\nTahmin: {tahmin}"
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sonuc_guncelle(mac_id, sonuc, fin_ev, fin_dep)
    except Exception as e:
        logger.error(f"Sonuc hatasi: {e}")


# ================================================
# ANA DÖNGÜ
# ================================================
async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()

    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "MAC ANALIZ BOTU - YENI SISTEM\n\n"
                "Yeni strateji motoru aktif!\n"
                "Gol + Sut + Possession + Atak + Kart\n"
                "Gemini AI analiz aktif\n"
                "Dinamik kasa yonetimi aktif\n\n"
                f"Min sinyal: {MIN_PUAN}/12\n\n"
                "Stratejiler:\n"
                "- Gol Patlamasi\n"
                "- Golsuz Aktif Mac\n"
                "- Sut Dominant\n"
                "- Top Hakimiyeti\n"
                "- Donme Senaryosu\n"
                "- Geri Donme\n"
                "- Kirmizi Kart\n"
                "- IY Baskisi\n\n"
                "Zamanlama:\n"
                "00:00-11:30 Uyku\n"
                "11:30-15:00 (8dk)\n"
                "15:00-19:00 (7dk)\n"
                "19:00-23:00 (6dk)\n"
                "23:00-00:00 Uyku"
            )
        )
        logger.info("Bot basladi - Yeni sistem!")
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
                        text="UYKU MODU - 11:30'da uyanacagim!"
                    )
                    uyku_bildirimi = True
                await asyncio.sleep(1800)
                continue
            else:
                if uyku_bildirimi:
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"UYANDIM! Yeni sistem aktif - Kontrol: {sure//60}dk"
                    )
                    uyku_bildirimi = False

            maclar = await macları_cek()
            aktif_idler = [m['id'] for m in maclar]

            # Biten maçların sonuçları
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

            # Aktif maçları analiz et
            for mac in maclar:
                puan, sinyaller, strateji = sinyal_hesapla(mac)
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
                        tahmin = tavsiye_uret(mac, strateji)
                        ai_yorum, kasa_yuzde = await gemini_analiz(
                            mac, puan, strateji, tahmin
                        )
                        await bildirim_gonder(
                            bot, mac, puan, sinyaller,
                            strateji, tahmin, ai_yorum, kasa_yuzde
                        )
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
