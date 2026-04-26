"""
MAC ANALIZ BOTU - DUZELTILMIS SISTEM
- Net tahmin ve aciklama
- Gercek AI analiz (deep thinking)
- Puan aciklamali
- WC filtresi esnek
- Corner + Odds + Gemini
"""

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

API_HEADERS = {
    "x-apisports-key": APISPORTS_KEY,
    "x-apisports-host": "v3.football.api-sports.io"
}
BASE_URL = "https://v3.football.api-sports.io"


# ================================================
# ZAMAN YÖNETİMİ
# ================================================
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
            ("ai_yorum", "TEXT"), ("kasa_yuzde", "REAL"), ("strateji", "TEXT")
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
                (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol,
                 puan, strateji, tahmin, ai_yorum, kasa_yuzde)
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
                UPDATE sinyaller SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3
                WHERE mac_id=$4 AND sonuc='BEKLIYOR'
            """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e:
        logger.error(f"Guncelleme hatasi: {e}")


# ================================================
# COOLING OFF — sadece çok açık durumlarda
# ================================================
def cooling_off_kontrol(mac):
    dakika = mac.get('dakika', 0)
    son_gol = mac.get('son_gol', 0)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    gol_fark = abs(mac.get('ev_gol', 0) - mac.get('dep_gol', 0))
    dangerous_toplam = dangerous_ev + dangerous_dep

    # Sadece 3+ fark VE 65+ dakika VE düşük atak
    if gol_fark >= 3 and dakika >= 65 and dangerous_toplam < 20:
        return True, f"Skor {mac['ev_gol']}-{mac['dep_gol']} netlesmis + dusuk atak"

    return False, ""


# ================================================
# ALTIN PENCERE
# ================================================
def zaman_bonusu_hesapla(dakika):
    if 54 <= dakika <= 60:
        return 3, "POWER WINDOW (54-60dk) +3"
    elif 24 <= dakika <= 36:
        return 2, "ERKEN BASKISI (24-36dk) +2"
    elif 45 <= dakika <= 49:
        return 2, "UZATMA (45-49dk) +2"
    elif 7 <= dakika <= 15:
        return 1, "ERKEN ACILIS (7-15dk) +1"
    return 0, ""


# ================================================
# SİNYAL SİSTEMİ — PUAN AÇIKLAMALI
# ================================================
def sinyal_hesapla(mac):
    puan = 0
    puan_detay = []  # Her puanın neden verildiği
    stratejiler = []

    dakika = mac.get('dakika', 0)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    son_gol = mac.get('son_gol', 0)
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    possession_dep = mac.get('possession_dep', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    sari_ev = mac.get('sari_kart_ev', 0)
    sari_dep = mac.get('sari_kart_dep', 0)
    kirmizi = mac.get('kirmizi_kart', 0)
    corner_ev = mac.get('corner_ev', 0)
    corner_dep = mac.get('corner_dep', 0)
    corner_toplam = mac.get('corner_toplam', 0)
    ah_deger = mac.get('ah_deger', 0.0)

    toplam_gol = ev_gol + dep_gol
    gol_fark = abs(ev_gol - dep_gol)
    kg_var = ev_gol > 0 and dep_gol > 0
    esit_skor = ev_gol == dep_gol
    gol_hizi = round(toplam_gol / dakika, 3) if dakika > 0 else 0
    shots_toplam = shots_ev + shots_dep
    dangerous_toplam = dangerous_ev + dangerous_dep

    # ---- GOL BAZLI (max 5 puan) ----
    if toplam_gol >= 4:
        puan += 2
        puan_detay.append(f"+2: {toplam_gol} gol var (yuksek tempo)")
        stratejiler.append("GOL_PATLAMASI")
    elif toplam_gol >= 3:
        puan += 1
        puan_detay.append(f"+1: {toplam_gol} gol var")

    if kg_var:
        puan += 1
        puan_detay.append("+1: KG var (her iki takim gol buldu)")

    if gol_fark >= 3:
        puan += 2
        puan_detay.append(f"+2: {gol_fark} gol fark (dominant takim)")
        stratejiler.append("BUYUK_FARK")
    elif gol_fark >= 2:
        puan += 1
        puan_detay.append(f"+1: {gol_fark} gol fark")

    if gol_hizi >= 0.15:
        puan += 1
        puan_detay.append(f"+1: Gol hizi cok yuksek ({gol_hizi}/dk)")
    elif gol_hizi >= 0.10:
        puan += 1
        puan_detay.append(f"+1: Gol hizi yuksek ({gol_hizi}/dk)")

    # ---- BERABERLIK BONUSU ----
    if esit_skor:
        puan += 2
        puan_detay.append(f"+2: Beraberlik ({ev_gol}-{dep_gol}) - her iki takim gol arayacak")
        stratejiler.append("BERABERLIK")

    # ---- SHOTS ON TARGET ----
    if shots_toplam >= 12:
        puan += 2
        puan_detay.append(f"+2: {shots_toplam} isabetli sut (COK yuksek)")
        stratejiler.append("YUKSEK_SUT")
    elif shots_toplam >= 8:
        puan += 1
        puan_detay.append(f"+1: {shots_toplam} isabetli sut")

    shots_fark = abs(shots_ev - shots_dep)
    if shots_fark >= 5:
        puan += 1
        d = mac['ev'] if shots_ev > shots_dep else mac['dep']
        puan_detay.append(f"+1: {d[:15]} sut dominant ({shots_ev}-{shots_dep})")
        stratejiler.append("SUT_DOMINANT")

    # ---- POSSESSİON ----
    poss_fark = abs(possession_ev - possession_dep)
    if poss_fark >= 25:
        puan += 2
        d = mac['ev'] if possession_ev > possession_dep else mac['dep']
        puan_detay.append(f"+2: {d[:15]} top dominant (%{max(possession_ev,possession_dep)})")
        stratejiler.append("POSSESSION_DOM")
    elif poss_fark >= 15:
        puan += 1
        d = mac['ev'] if possession_ev > possession_dep else mac['dep']
        puan_detay.append(f"+1: {d[:15]} top ustunlugu (%{max(possession_ev,possession_dep)})")

    # ---- DANGEROUS ATTACKS ----
    if dangerous_toplam >= 100:
        puan += 2
        puan_detay.append(f"+2: {dangerous_toplam} tehlikeli atak (cok aktif mac)")
        stratejiler.append("YUKSEK_ATAK")
    elif dangerous_toplam >= 60:
        puan += 1
        puan_detay.append(f"+1: {dangerous_toplam} tehlikeli atak")

    # ---- CORNER ----
    if corner_toplam >= 12:
        puan += 2
        puan_detay.append(f"+2: {corner_toplam} corner (elite tempo)")
        stratejiler.append("YUKSEK_CORNER")
    elif corner_toplam >= 8:
        puan += 1
        puan_detay.append(f"+1: {corner_toplam} corner")

    corner_fark = abs(corner_ev - corner_dep)
    if corner_fark >= 5:
        puan += 1
        d = mac['ev'] if corner_ev > corner_dep else mac['dep']
        puan_detay.append(f"+1: {d[:15]} corner dominant ({corner_ev}-{corner_dep})")

    # ---- ASIAN HANDICAP ----
    if ah_deger != 0:
        if -1.5 <= ah_deger <= -0.75:
            puan += 2
            puan_detay.append(f"+2: AH {ah_deger} - Ev sahibi guclu favori")
            stratejiler.append("AH_FAVORI_EV")
        elif -0.75 < ah_deger < 0:
            puan += 1
            puan_detay.append(f"+1: AH {ah_deger} - Hafif ev favorisi")
        elif 0 < ah_deger <= 1.25:
            puan += 1
            puan_detay.append(f"+1: AH {ah_deger} - Dengeli mac")
            stratejiler.append("AH_DENGELI")

    # ---- KARTLAR ----
    if kirmizi >= 1:
        puan += 1
        puan_detay.append(f"+1: Kirmizi kart var - acik alan!")
        stratejiler.append("KIRMIZI_KART")

    # ---- ÖZEL SENARYOLAR ----
    if toplam_gol == 0 and shots_toplam >= 8 and dangerous_toplam >= 50:
        puan += 2
        puan_detay.append(f"+2: 0-0 ama cok aktif (sut:{shots_toplam} atak:{dangerous_toplam}) - VALUE!")
        stratejiler.append("GOLSUZ_AKTIF")

    if dep_gol > ev_gol and possession_ev >= 55 and shots_ev >= shots_dep:
        puan += 2
        puan_detay.append(f"+2: Ev sahibi geride ama dominant - geri donus bekleniyor!")
        stratejiler.append("VALUE_GIRISI")

    if 45 <= dakika <= 60 and shots_toplam >= 6:
        puan += 1
        puan_detay.append(f"+1: 2.yari basladi, {shots_toplam} sut var")
        stratejiler.append("2Y_BASLANGIC")

    if son_gol >= 70:
        puan += 1
        puan_detay.append(f"+1: Son gol {son_gol}. dakikada (taze momentum)")

    # ---- ALTIN PENCERE ----
    zaman_bonus, zaman_label = zaman_bonusu_hesapla(dakika)
    if zaman_bonus > 0:
        puan += zaman_bonus
        puan_detay.append(f"+{zaman_bonus}: {zaman_label}")
    elif zaman_bonus < 0:
        puan += zaman_bonus
        puan_detay.append(f"{zaman_bonus}: Gec dakika riski")

    strateji_adi = stratejiler[0] if stratejiler else "GENEL"
    return puan, puan_detay, strateji_adi


# ================================================
# NET TAHMİN ÜRETİCİ — NEDEN AÇIKLAMALI
# ================================================
def tavsiye_uret(mac, strateji):
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    gol_fark = ev_gol - dep_gol
    toplam_gol = ev_gol + dep_gol
    possession_ev = mac.get('possession_ev', 50)
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    ah_deger = mac.get('ah_deger', 0.0)
    dakika = mac.get('dakika', 0)

    tahmin = ""
    neden = ""

    # Strateji bazlı net karar
    if strateji == "VALUE_GIRISI":
        tahmin = "EV KAZANIR VEYA BERABERE"
        neden = f"Ev sahibi {dep_gol-ev_gol} gol geride ama sahayı domine ediyor (%{possession_ev} top, {shots_ev} sut)"

    elif strateji == "GOLSUZ_AKTIF":
        if shots_ev > shots_dep and possession_ev >= 55:
            tahmin = "EV GOL ATACAK (S)"
            neden = f"0-0 skoru ama ev sahibi baski yapiyor ({shots_ev} sut, %{possession_ev} top)"
        elif shots_dep > shots_ev:
            tahmin = "DEP GOL ATACAK (S)"
            neden = f"0-0 skoru ama deplasman daha aktif ({shots_dep} sut)"
        else:
            tahmin = "GOL OLACAK (S)"
            neden = f"0-0 ama her iki takim cok aktif (toplam {shots_ev+shots_dep} sut)"

    elif strateji == "GOL_PATLAMASI":
        if gol_fark >= 2:
            tahmin = "EV GOL ATACAK (S)"
            neden = f"Ev sahibi {gol_fark} gol farkla onunde ve baskiyi surduruyor"
        elif gol_fark <= -2:
            tahmin = "DEP GOL ATACAK (S)"
            neden = f"Deplasman {abs(gol_fark)} gol farkla onunde"
        else:
            tahmin = "GOL OLACAK (S)"
            neden = f"Toplam {toplam_gol} gol var, mac cok acik"

    elif strateji == "BERABERLIK":
        if possession_ev >= 60 and dangerous_ev > dangerous_dep:
            tahmin = "EV GOL ATACAK (S)"
            neden = f"Beraberlik ama ev sahibi dominant (%{possession_ev} top, {dangerous_ev} atak)"
        elif possession_ev < 40 and dangerous_dep > dangerous_ev:
            tahmin = "DEP GOL ATACAK (S)"
            neden = f"Beraberlik ama deplasman dominant (%{possession_ev} topla oynuyor sadece)"
        else:
            tahmin = "GOL OLACAK (S)"
            neden = f"Beraberlik ({ev_gol}-{dep_gol}), her iki takim gol pesinde"

    elif strateji == "AH_FAVORI_EV":
        tahmin = "EV GOL ATACAK (S)"
        neden = f"AH {ah_deger} - Piyasa ev sahibini guclu favori goruyor"

    elif strateji == "SUT_DOMINANT":
        if shots_ev > shots_dep:
            tahmin = "EV GOL ATACAK (S)"
            neden = f"Ev sahibi sut ustunlugu ({shots_ev} vs {shots_dep} isabetli sut)"
        else:
            tahmin = "DEP GOL ATACAK (S)"
            neden = f"Deplasman sut ustunlugu ({shots_dep} vs {shots_ev} isabetli sut)"

    elif strateji == "KIRMIZI_KART":
        tahmin = "GOL OLACAK (S)"
        neden = "Kirmizi kart sonrasi duzensizlik ve acik alan - gol bekleniyor"

    elif strateji == "BUYUK_FARK":
        if gol_fark > 0:
            tahmin = "EV GOL ATACAK (S)"
            neden = f"Ev sahibi {gol_fark} farkla onude ve tempo dusmemis"
        else:
            tahmin = "DEP GOL ATACAK (S)"
            neden = f"Deplasman {abs(gol_fark)} farkla onunde"

    else:
        # Genel karar — net mantıkla
        if gol_fark >= 2 and possession_ev >= 50:
            tahmin = "EV GOL ATACAK (S)"
            neden = f"Ev sahibi {gol_fark} farkla onude ve sahayı kontrol ediyor"
        elif gol_fark <= -2:
            tahmin = "DEP GOL ATACAK (S)"
            neden = f"Deplasman {abs(gol_fark)} gol farkla onunde"
        # Hata buradaydı: Walrus operatörü (:=) and/or içinde parantezsiz kullanılınca hata verir.
        elif toplam_gol >= 3 and (kg_var := (ev_gol > 0 and dep_gol > 0)):
            tahmin = "GOL OLACAK (S)"
            neden = f"Mac cok acik, {toplam_gol} gol var ve tempo yuksek"
        elif possession_ev >= 65:
            tahmin = "EV GOL ATACAK (S)"
            neden = f"Ev sahibi topu domine ediyor (%{possession_ev})"
        elif dangerous_ev >= 50 and dangerous_ev > dangerous_dep * 1.5:
            tahmin = "EV GOL ATACAK (S)"
            neden = f"Ev sahibi cok fazla tehlikeli atak uреtiyor ({dangerous_ev})"
        else:
            tahmin = "GOL OLACAK (S)"
            neden = f"Mac aktif, gol beklentisi yuksek ({shots_ev+shots_dep} toplam isabetli sut)"

    return tahmin, neden


# ================================================
# GEMİNİ AI — GERÇEK DERİN ANALİZ
# ================================================
async def gemini_analiz(mac, puan, strateji, tahmin, neden):
    if not GEMINI_KEY:
        return "AI aktif degil."

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"

    # Maçın durumunu detaylı ver
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    dakika = mac.get('dakika', 0)
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    corner_ev = mac.get('corner_ev', 0)
    corner_dep = mac.get('corner_dep', 0)
    son_gol = mac.get('son_gol', 0)
    ah = mac.get('ah_deger', 0)
    son_golden_beri = dakika - son_gol if son_gol > 0 else dakika

    prompt = f"""Sen deneyimli bir canli bahis analistsin. Asagidaki maci DERINLEMESINE analiz et.

=== MAC VERILERI ===
Mac: {mac['ev']} {ev_gol}-{dep_gol} {mac['dep']}
Lig: {mac['lig']} | {dakika}. Dakika
Isabetli Sut: Ev {shots_ev} / Dep {shots_dep}
Top Hakimiyeti: Ev %{possession_ev} / Dep %{possession_ev}
Tehlikeli Atak: Ev {dangerous_ev} / Dep {dangerous_dep}
Corner: Ev {corner_ev} / Dep {corner_dep}
Son Gol: {son_gol}. dk ({son_golden_beri} dk once)
Asian Handicap: {ah if ah != 0 else 'Veri yok'}

=== BOT KARARI ===
Strateji: {strateji}
Tahmin: {tahmin}
Neden: {neden}
Sinyal Puani: {puan}/12

=== GOREV ===
Bu maci derin analiz et. Botu onaylamali MISIN?

Sunlari dusun:
1. Son golden bu yana {son_golden_beri} dk gecmis. Momentum hala taze mi?
2. {mac['ev']} ev sahibi mi yoksa deplasman mi dominant gozukuyor? Neden?
3. Bu lig ve bu skor kombinasyonunda {tahmin} tahmini mantikli mi?
4. Istatistikler birbirini destekliyor mu yoksa celiski var mi?
5. Girmemek icin bir neden var mi?

ONEMLI: Genel laflar etme. Sadece BU maca ozgu, somut ve ozgun yorum yap.
Maksimum 3 cumle. Net konusur.

JSON: {{"yorum": "somut analiz", "gir": true/false, "kasa": 1.5}}"""

    try:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 300,
                "thinkingConfig": {"thinkingBudget": 1024}
            }
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text'].strip()
                    if "```" in text:
                        text = text.split("```")[1].replace("json", "").strip()
                    result = json.loads(text)
                    yorum = result.get('yorum', '')
                    gir = result.get('gir', True)
                    kasa = float(result.get('kasa', 1.5))
                    if not gir:
                        kasa = 0.0
                    logger.info(f"Gemini: gir={gir} kasa={kasa}")
                    return yorum, kasa
                else:
                    logger.error(f"Gemini {resp.status}")
                    return None, None
    except Exception as e:
        logger.error(f"Gemini: {e}")
        return None, None


# ================================================
# KASA YÖNETİMİ
# ================================================
def kasa_hesapla(puan, dakika, ah_deger):
    if puan >= 10 and 54 <= dakika <= 60:
        return 4.0
    elif puan >= 9:
        return 3.0
    elif puan >= 7:
        kasa = 1.5
        if -1.5 <= ah_deger <= -0.75:
            kasa += 0.5
        return kasa
    elif puan >= 6:
        return 1.0
    return 0.5


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
        if not rows:
            return
        toplam = len(rows)
        kazanan = len([r for r in rows if r['sonuc'] == 'TUTTU'])
        oran = round(kazanan / toplam * 100, 1)

        strateji_stats = {}
        for r in rows:
            s = r.get('strateji') or 'Diger'
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

        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"HAFTALIK RAPOR\n\n"
                f"Toplam: {toplam} | Kazanan: {kazanan}\n"
                f"Basari: %{oran}\n"
                f"En iyi strateji: {en_iyi[0]}"
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
        if not rows:
            return
        toplam = len(rows)
        kazanan = len([r for r in rows if r['sonuc'] == 'TUTTU'])
        oran = round(kazanan / toplam * 100, 1)
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"AYLIK RAPOR\nToplam: {toplam} | Kazanan: {kazanan}\nBasari: %{oran}"
        )
    except Exception as e:
        logger.error(f"Aylik rapor: {e}")


# ================================================
# SONUÇ KONTROLÜ
# ================================================
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
# VERİ ÇEKME
# ================================================
async def macları_cek():
    url = f"{BASE_URL}/fixtures?live=all"
    maclar = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=API_HEADERS,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('errors'):
                        logger.error(f"API errors: {data['errors']}")
                        return maclar
                    fixtures = data.get('response', [])
                    logger.info(f"{len(fixtures)} canli mac")

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

                            home_id = teams.get('home', {}).get('id')
                            stats = {
                                'shots_on_target_ev': 0, 'shots_on_target_dep': 0,
                                'possession_ev': 50, 'possession_dep': 50,
                                'dangerous_attacks_ev': 0, 'dangerous_attacks_dep': 0,
                                'sari_kart_ev': 0, 'sari_kart_dep': 0,
                                'kirmizi_kart': 0, 'son_gol': 0,
                                'corner_ev': 0, 'corner_dep': 0, 'corner_toplam': 0,
                                'ah_deger': 0.0,
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
                                        try:
                                            val = int(val.replace('%', '').strip())
                                        except:
                                            val = 0
                                    else:
                                        try:
                                            val = int(val)
                                        except:
                                            val = 0

                                    if 'shots on goal' in tip or 'on target' in tip:
                                        if is_home:
                                            stats['shots_on_target_ev'] = val
                                        else:
                                            stats['shots_on_target_dep'] = val
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
                                    elif 'corner kicks' in tip or 'corners' in tip:
                                        if is_home:
                                            stats['corner_ev'] = val
                                        else:
                                            stats['corner_dep'] = val
                                        stats['corner_toplam'] = stats['corner_ev'] + stats['corner_dep']

                            son_gol = 0
                            for event in f.get('events', []):
                                if event.get('type') == 'Goal':
                                    gdk = int(event.get('time', {}).get('elapsed', 0) or 0)
                                    if gdk > son_gol:
                                        son_gol = gdk
                            stats['son_gol'] = son_gol

                            maclar.append({
                                'id': mac_id, 'ev': ev, 'dep': dep, 'lig': lig,
                                'dakika': dakika, 'ev_gol': ev_gol, 'dep_gol': dep_gol,
                                **stats
                            })
                        except Exception as e:
                            logger.error(f"Mac parse: {e}")
                            continue
                else:
                    logger.error(f"API hata: {resp.status}")
    except Exception as e:
        logger.error(f"API baglanti: {e}")
    return maclar


async def odds_ekle(maclar):
    """Canlı odds ekle"""
    if not maclar:
        return maclar
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{BASE_URL}/odds/live"
            async with session.get(
                url, headers=API_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get('response', [])
                    odds_map = {}
                    for item in results:
                        fid = str(item.get('fixture', {}).get('id', ''))
                        for bet in item.get('bets', []):
                            if 'asian handicap' in bet.get('name', '').lower():
                                for v in bet.get('values', []):
                                    if 'home' in v.get('value', '').lower():
                                        parts = v.get('value', '').split()
                                        for p in parts:
                                            try:
                                                odds_map[fid] = float(p)
                                                break
                                            except:
                                                pass
                    for mac in maclar:
                        if mac['id'] in odds_map:
                            mac['ah_deger'] = odds_map[mac['id']]
    except Exception as e:
        logger.error(f"Odds: {e}")
    return maclar


# ================================================
# BİLDİRİM — NET VE AÇIKLAMALI
# ================================================
async def bildirim_gonder(bot, mac, puan, puan_detay, strateji, tahmin, neden, ai_yorum, ai_kasa):
    # Kasa belirle
    kasa = ai_kasa if ai_kasa is not None else kasa_hesapla(
        puan, mac['dakika'], mac.get('ah_deger', 0)
    )

    # AI iptal mi dedi?
    if kasa == 0 and ai_yorum:
        mesaj = (
            f"AI UYARISI - GIRME!\n"
            f"{mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
            f"{mac['dakika']}dk | {mac['lig']}\n\n"
            f"Sinyal puani yeterli ama AI'ye gore riskli:\n"
            f"{ai_yorum}"
        )
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, 0)
        return

    # Karar emojisi
    if puan >= 9 and kasa >= 3:
        karar = "KESIN GIR"
        emoji = "🔥"
    elif puan >= 7:
        karar = "GIREBILIRSiN"
        emoji = "✅"
    else:
        karar = "DIKKATLI OL"
        emoji = "⚠️"

    # Puan bar
    bar = "█" * min(puan, 12) + "░" * max(0, 12 - puan)

    # Puan detayları (sadece ilk 5 tane)
    puan_str = "\n".join(puan_detay[:5])
    if len(puan_detay) > 5:
        puan_str += f"\n... ve {len(puan_detay)-5} sinyal daha"

    # İstatistik özeti
    ah = mac.get('ah_deger', 0)
    ah_str = f"AH:{ah}" if ah != 0 else ""
    corner_str = f"Corner:{mac.get('corner_ev',0)}-{mac.get('corner_dep',0)}" if mac.get('corner_toplam',0) > 0 else ""

    istat = (
        f"Sut:{mac['shots_on_target_ev']}/{mac['shots_on_target_dep']} "
        f"Top:%{mac['possession_ev']}/%{mac.get('possession_dep',50)} "
        f"Atak:{mac['dangerous_attacks_ev']}/{mac['dangerous_attacks_dep']}"
    )
    if corner_str:
        istat += f" {corner_str}"
    if ah_str:
        istat += f" {ah_str}"

    mesaj = (
        f"{emoji} {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"{mac['lig']} | {mac['dakika']}. Dakika\n\n"
        f"SINYAL: {puan}/12\n"
        f"{bar}\n\n"
        f"NEDEN BU PUAN:\n{puan_str}\n\n"
        f"ISTATISTIK:\n{istat}\n\n"
        f"STRATEJI: {strateji}\n\n"
        f"TAHMIN: {tahmin}\n"
        f"NEDEN: {neden}\n\n"
        f"AI: {ai_yorum if ai_yorum else 'Analiz yapilmadi'}\n\n"
        f"KASA: %{kasa} kullan\n\n"
        f"{'='*25}\n"
        f"{karar}\n"
        f"{'='*25}"
    )

    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum or "", kasa)
        logger.info(f"Bildirim: {mac['ev']} vs {mac['dep']} | {puan}p | {tahmin} | %{kasa}")
    except Exception as e:
        logger.error(f"Bildirim: {e}")


async def sonuc_bildir(bot, mac_id, ev, dep, tahmin, sonuc, fin_ev, fin_dep):
    emoji = "TUTTU!" if sonuc == "TUTTU" else "DUSTU!"
    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"SONUC: {ev} {fin_ev}-{fin_dep} {dep}\n{emoji}\nTahmin: {tahmin}"
    )
    await sonuc_guncelle(mac_id, sonuc, fin_ev, fin_dep)


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
                "Duzeltmeler:\n"
                "Net tahmin + neden aciklamasi\n"
                "Puan neye gore verildi gosteriliyor\n"
                "AI maca ozel derin analiz yapiyor\n"
                "Corner + Odds entegre\n"
                "Cooling Off sadece net durumlarda\n\n"
                f"Min sinyal: {MIN_PUAN}/12\n\n"
                "00:00-11:30 Uyku\n"
                "11:30-15:00 (8dk)\n"
                "15:00-19:00 (7dk)\n"
                "19:00-23:00 (6dk)\n"
                "23:00-00:00 Uyku"
            )
        )
        logger.info("Bot basladi!")
    except Exception as e:
        logger.error(f"Baslangic: {e}")

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
                    await bot.send_message(chat_id=CHAT_ID, text="UYKU MODU - 11:30'da uyanacagim!")
                    uyku_bildirimi = True
                await asyncio.sleep(1800)
                continue
            else:
                if uyku_bildirimi:
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"UYANDIM! Yeni sistem aktif - {sure//60}dk kontrol"
                    )
                    uyku_bildirimi = False

            maclar = await macları_cek()
            maclar = await odds_ekle(maclar)
            aktif_idler = [m['id'] for m in maclar]

            # Biten maçlar
            for mac_id, bilgi in list(biten_maclar.items()):
                if mac_id not in aktif_idler:
                    sonuc = sonuc_kontrol(
                        bilgi['tahmin'],
                        bilgi['bas_ev'], bilgi['bas_dep'],
                        bilgi['son_ev'], bilgi['son_dep']
                    )
                    await sonuc_bildir(
                        bot, mac_id, bilgi['ev'], bilgi['dep'],
                        bilgi['tahmin'], sonuc,
                        bilgi['son_ev'], bilgi['son_dep']
                    )
                    del biten_maclar[mac_id]
                    await asyncio.sleep(1)

            # Aktif maçlar
            for mac in maclar:
                puan, puan_detay, strateji = sinyal_hesapla(mac)
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

                        # Cooling Off
                        cooling, cooling_msg = cooling_off_kontrol(mac)
                        if cooling:
                            logger.info(f"Cooling Off atildi: {mac['ev']} - {cooling_msg}")
                            continue

                        tahmin, neden = tavsiye_uret(mac, strateji)

                        # Gemini AI
                        ai_yorum, ai_kasa = await gemini_analiz(
                            mac, puan, strateji, tahmin, neden
                        )

                        await bildirim_gonder(
                            bot, mac, puan, puan_detay, strateji,
                            tahmin, neden, ai_yorum, ai_kasa
                        )

                        bildirim_gonderilen[mac_id] = {
                            'puan': puan, 'tahmin': tahmin,
                            'ev_gol': mac['ev_gol'], 'dep_gol': mac['dep_gol']
                        }
                        await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Ana dongu: {e}")

        await asyncio.sleep(sure or 1800)


if __name__ == "__main__":
    logger.info("BOT STARTED")
    asyncio.run(ana_dongu())
