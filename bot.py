"""
MAC ANALIZ BOTU - OPTIMIZE SISTEM
Zamanlama:
- Hafta ici: 19:00 - 00:00 (Pazartesi-Cuma)
- Hafta sonu: 19:00 - 23:00 (Cumartesi-Pazar)
- Diger saatler: Uyku (API hak tasarrufu)

API Optimizasyonu:
- Odds sadece sinyal bulunan maclara sorulur
- Corner verisini ana endpoint'ten alir (ayri istek yok)

Winning Code Sistemi:
- VU/TUM/MA/DIYI hard filter
- Altin pencere bonuslari
- Beraberlik bonusu
- Cooling Off korumasi
- Gemini AI derin analiz
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta
import json

# Ortam değişkenlerini alıp, gizli \n karakterlerini temizliyoruz (strip)
# Bu kısım hatayı düzelten kritik kısımdır.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
GEMINI_KEY = os.getenv("GEMINI_KEY", "").strip()
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
# ZAMAN YÖNETİMİ — OPTİMİZE
# ================================================
def aktif_mi():
    """
    Hafta ici (0=Pzt, 4=Cuma): 19:00 - 00:00
    Hafta sonu (5=Cmt, 6=Pzr): 19:00 - 23:00
    """
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday()  # 0=Pazartesi, 6=Pazar

    hafta_ici = gun <= 4  # Pzt-Cuma
    hafta_sonu = gun >= 5  # Cmt-Pzr

    if hafta_ici:
        return 19 <= saat or saat < 0  # 19:00 - 00:00
    elif hafta_sonu:
        return 19 <= saat <= 22  # 19:00 - 23:00

    return False


def kontrol_suresi_al():
    """Aktif saatlerde 7 dakika, uyku modunda 30 dakika"""
    if aktif_mi():
        return 420  # 7 dakika
    return None  # Uyku


def sonraki_aktif_saat():
    simdi = datetime.now()
    gun = simdi.weekday()
    saat = simdi.hour

    if gun <= 4:
        return "19:00 (Hafta ici)"
    else:
        return "19:00 (Hafta sonu)"


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
        logger.error(f"Kayit: {e}")


async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        if db_pool:
            await db_pool.execute("""
                UPDATE sinyaller SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3
                WHERE mac_id=$4 AND sonuc='BEKLIYOR'
            """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e:
        logger.error(f"Guncelleme: {e}")


# ================================================
# WINNING CODE — VU/TUM/MA/DİYİ HARD FILTER
# ================================================
def winning_code_kontrol(mac):
    """
    VU=1: Ev sahibi yeterli baski yapıyor
    TUM=1: Mac aktif, her iki taraf hucumda
    MA=0: Momentum durmus degil (taze)
    DIYI=0: Deplasman savunmada kalmis
    
    Hepsi gecmezse sinyal URETILMEZ.
    """
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    son_gol = mac.get('son_gol', 0)
    dakika = mac.get('dakika', 0)
    corner_ev = mac.get('corner_ev', 0)
    corner_dep = mac.get('corner_dep', 0)

    # VU=1: Ev sahibi aktif baskida
    VU = (
        shots_ev >= 2 and
        possession_ev >= 42 and
        dangerous_ev >= 15
    )

    # TUM=1: Mac genel olarak aktif
    TUM = (dangerous_ev + dangerous_dep) >= 25

    # MA=0: Momentum hala devam ediyor
    if son_gol > 0:
        son_golden_beri = dakika - son_gol
        toplam_atak = dangerous_ev + dangerous_dep
        MA = not (son_golden_beri > 8 and toplam_atak < 20)
    else:
        MA = not (dakika > 15 and dangerous_ev < 8)

    # DIYI=0: Deplasman savunmaya cekilmis
    DIYI = (
        dangerous_dep <= dangerous_ev * 0.65 and
        shots_dep <= shots_ev + 3
    )

    return {
        'VU': VU,
        'TUM': TUM,
        'MA': MA,
        'DIYI': DIYI,
        'gecti': VU and TUM and MA and DIYI,
        'detay': f"VU:{1 if VU else 0} TUM:{1 if TUM else 0} MA:{0 if not MA else 1} DIYI:{0 if DIYI else 1}"
    }


# ================================================
# ALTIN PENCERE BONUSLARI
# ================================================
def zaman_bonusu_hesapla(dakika):
    if 54 <= dakika <= 60:
        return 3, "POWER WINDOW 54-60dk"
    elif 24 <= dakika <= 36:
        return 2, "ERKEN BASKISI 24-36dk"
    elif 45 <= dakika <= 49:
        return 2, "UZATMA 45-49dk"
    elif 7 <= dakika <= 15:
        return 1, "ERKEN ACILIS 7-15dk"
    return 0, ""


# ================================================
# COOLING OFF KORUMASI
# ================================================
def cooling_off_kontrol(mac):
    dakika = mac.get('dakika', 0)
    son_gol = mac.get('son_gol', 0)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    corner_ev = mac.get('corner_ev', 0)
    corner_dep = mac.get('corner_dep', 0)
    gol_fark = abs(mac.get('ev_gol', 0) - mac.get('dep_gol', 0))
    dangerous_toplam = dangerous_ev + dangerous_dep
    corner_toplam = corner_ev + corner_dep

    # Skor 3+ fark + gec dakika + dusuk aktivite
    if gol_fark >= 3 and dakika >= 62 and dangerous_toplam < 20:
        return True, f"Skor netlesmis ({mac['ev_gol']}-{mac['dep_gol']}) + gec donem + dusuk atak"

    # Son golden cok zaman gecti + aktivite dusuk
    if son_gol > 0:
        gecen = dakika - son_gol
        if gecen > 7 and dangerous_toplam < 20 and corner_toplam < 3:
            return True, f"Son gol {gecen}dk once, atak ve corner dusuk"

    return False, ""


# ================================================
# ANA SİNYAL SİSTEMİ
# ================================================
def sinyal_hesapla(mac):
    """
    1. Winning Code hard filter
    2. Istatistik bazli puanlama
    3. Altin pencere bonusu
    4. Beraberlik bonusu
    Returns: (puan, puan_detay, strateji) veya (0, [], "") eger WC gecmezse
    """
    # --- STEP 1: WINNING CODE HARD FILTER ---
    wc = winning_code_kontrol(mac)
    if not wc['gecti']:
        return 0, [], "", wc  # Sinyal uretilmez

    puan = 0
    puan_detay = []
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

    # WC gecti, temel 4 puan ver
    puan += 4
    puan_detay.append(f"+4: Winning Code gecti ({wc['detay']})")

    # --- STEP 2: GOL BAZLI ---
    if toplam_gol >= 4:
        puan += 2
        puan_detay.append(f"+2: {toplam_gol} gol (yuksek tempo)")
        stratejiler.append("GOL_PATLAMASI")
    elif toplam_gol >= 3:
        puan += 1
        puan_detay.append(f"+1: {toplam_gol} gol")

    if kg_var:
        puan += 1
        puan_detay.append("+1: KG var")

    if gol_fark >= 3:
        puan += 2
        puan_detay.append(f"+2: {gol_fark} gol fark (dominant)")
        stratejiler.append("BUYUK_FARK")
    elif gol_fark >= 2:
        puan += 1
        puan_detay.append(f"+1: {gol_fark} gol fark")

    if gol_hizi >= 0.15:
        puan += 1
        puan_detay.append(f"+1: Hiz {gol_hizi}/dk (cok yuksek)")
    elif gol_hizi >= 0.10:
        puan += 1
        puan_detay.append(f"+1: Hiz {gol_hizi}/dk")

    # --- STEP 3: BERABERLİK BONUSU ---
    if esit_skor:
        puan += 2
        puan_detay.append(f"+2: Beraberlik bonusu ({ev_gol}-{dep_gol})")
        stratejiler.append("BERABERLIK")

    # Value: Favori geri ama dominant
    if dep_gol > ev_gol and possession_ev >= 55 and shots_ev > shots_dep:
        puan += 2
        puan_detay.append(f"+2: VALUE - Ev sahibi geride ama dominant!")
        stratejiler.append("VALUE_GIRISI")

    # --- STEP 4: SHOTS ---
    if shots_toplam >= 12:
        puan += 2
        puan_detay.append(f"+2: {shots_toplam} isabetli sut (cok yuksek)")
        stratejiler.append("YUKSEK_SUT")
    elif shots_toplam >= 8:
        puan += 1
        puan_detay.append(f"+1: {shots_toplam} isabetli sut")

    if abs(shots_ev - shots_dep) >= 5:
        puan += 1
        d = mac['ev'] if shots_ev > shots_dep else mac['dep']
        puan_detay.append(f"+1: {d[:12]} sut dominant ({shots_ev}/{shots_dep})")
        stratejiler.append("SUT_DOMINANT")

    # --- STEP 5: POSSESSİON ---
    poss_fark = abs(possession_ev - possession_dep)
    if poss_fark >= 25:
        puan += 2
        d = mac['ev'] if possession_ev > possession_dep else mac['dep']
        puan_detay.append(f"+2: {d[:12]} top dominant (%{max(possession_ev,possession_dep)})")
        stratejiler.append("POSSESSION_DOM")
    elif poss_fark >= 15:
        puan += 1
        d = mac['ev'] if possession_ev > possession_dep else mac['dep']
        puan_detay.append(f"+1: {d[:12]} top ustunlugu (%{max(possession_ev,possession_dep)})")

    # --- STEP 6: DANGEROUS ATTACKS ---
    if dangerous_toplam >= 100:
        puan += 2
        puan_detay.append(f"+2: {dangerous_toplam} tehlikeli atak")
        stratejiler.append("YUKSEK_ATAK")
    elif dangerous_toplam >= 60:
        puan += 1
        puan_detay.append(f"+1: {dangerous_toplam} tehlikeli atak")

    # --- STEP 7: CORNER ---
    if corner_toplam >= 12:
        puan += 2
        puan_detay.append(f"+2: {corner_toplam} corner (elite tempo)")
        stratejiler.append("YUKSEK_CORNER")
    elif corner_toplam >= 8:
        puan += 1
        puan_detay.append(f"+1: {corner_toplam} corner")

    if abs(corner_ev - corner_dep) >= 5:
        puan += 1
        d = mac['ev'] if corner_ev > corner_dep else mac['dep']
        puan_detay.append(f"+1: {d[:12]} corner dominant ({corner_ev}/{corner_dep})")

    # --- STEP 8: ASIAN HANDICAP ---
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
            puan_detay.append(f"+1: AH {ah_deger} - Dengeli mac, gol bekle")
            stratejiler.append("AH_DENGELI")

    # --- STEP 9: KARTLAR ---
    if kirmizi >= 1:
        puan += 1
        puan_detay.append("+1: Kirmizi kart - acik alan!")
        stratejiler.append("KIRMIZI_KART")

    # --- STEP 10: ÖZEL ---
    if toplam_gol == 0 and shots_toplam >= 8 and dangerous_toplam >= 50:
        puan += 2
        puan_detay.append(f"+2: 0-0 COK AKTIF - VALUE (sut:{shots_toplam})")
        stratejiler.append("GOLSUZ_AKTIF")

    if 45 <= dakika <= 60 and shots_toplam >= 6:
        puan += 1
        puan_detay.append(f"+1: 2.yari basladi + {shots_toplam} sut")
        stratejiler.append("2Y_BASLANGIC")

    if son_gol >= 70:
        puan += 1
        puan_detay.append(f"+1: Son gol {son_gol}dk (taze)")

    # --- STEP 11: ALTIN PENCERE ---
    zaman_bonus, zaman_label = zaman_bonusu_hesapla(dakika)
    if zaman_bonus > 0:
        puan += zaman_bonus
        puan_detay.append(f"+{zaman_bonus}: {zaman_label}")

    strateji_adi = stratejiler[0] if stratejiler else "GENEL"
    return puan, puan_detay, strateji_adi, wc


# ================================================
# NET TAHMİN — NEDEN AÇIKLAMALI
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
    corner_ev = mac.get('corner_ev', 0)
    corner_dep = mac.get('corner_dep', 0)

    if strateji == "VALUE_GIRISI":
        return (
            "EV KAZANIR VEYA BERABERE",
            f"Ev sahibi {abs(gol_fark)} gol geride ama sahaya hakim "
            f"(%{possession_ev} top, {shots_ev} isabetli sut)"
        )

    elif strateji == "GOLSUZ_AKTIF":
        if shots_ev > shots_dep and possession_ev >= 55:
            return (
                "EV GOL ATACAK (S)",
                f"0-0 skoru ama ev sahibi baskida "
                f"({shots_ev} sut, %{possession_ev} top, {dangerous_ev} atak)"
            )
        elif shots_dep > shots_ev:
            return (
                "DEP GOL ATACAK (S)",
                f"0-0 ama deplasman daha aktif ({shots_dep} sut, {dangerous_dep} atak)"
            )
        return (
            "GOL OLACAK (S)",
            f"0-0 ama mac cok aktif — toplam {shots_ev+shots_dep} isabetli sut"
        )

    elif strateji == "BERABERLIK":
        if possession_ev >= 60 and dangerous_ev > dangerous_dep:
            return (
                "EV GOL ATACAK (S)",
                f"Beraberlik ama ev sahibi cok dominant "
                f"(%{possession_ev} top, {dangerous_ev} atak)"
            )
        elif possession_ev < 42:
            return (
                "DEP GOL ATACAK (S)",
                f"Beraberlik ama deplasman sahayı kontrolünde "
                f"(%{100-possession_ev} top)"
            )
        return (
            "GOL OLACAK (S)",
            f"Beraberlik ({ev_gol}-{dep_gol}) — her iki takim gol pesinde, "
            f"toplam {shots_ev+shots_dep} sut"
        )

    elif strateji == "AH_FAVORI_EV":
        return (
            "EV GOL ATACAK (S)",
            f"Piyasa ev sahibini guclu favori goruyor (AH {ah_deger}) — "
            f"istatistikler de destekliyor"
        )

    elif strateji == "AH_DENGELI":
        return (
            "GOL OLACAK (S)",
            f"Dengeli mac (AH {ah_deger}) — karsilikli gol beklentisi yuksek"
        )

    elif strateji == "SUT_DOMINANT":
        if shots_ev > shots_dep:
            return (
                "EV GOL ATACAK (S)",
                f"Ev sahibi sut ustunlugu: {shots_ev} vs {shots_dep} isabetli sut"
            )
        return (
            "DEP GOL ATACAK (S)",
            f"Deplasman sut ustunlugu: {shots_dep} vs {shots_ev} isabetli sut"
        )

    elif strateji == "POSSESSION_DOM":
        if possession_ev >= 60:
            return (
                "EV GOL ATACAK (S)",
                f"Ev sahibi topu domine ediyor (%{possession_ev}) — "
                f"gol baskisi artacak"
            )
        return (
            "DEP GOL ATACAK (S)",
            f"Deplasman top hakimiyetinde (%{100-possession_ev})"
        )

    elif strateji == "GOL_PATLAMASI":
        if gol_fark >= 2:
            return (
                "EV GOL ATACAK (S)",
                f"Ev sahibi {gol_fark} gol farkla onude ve tempo dusmemis"
            )
        elif gol_fark <= -2:
            return (
                "DEP GOL ATACAK (S)",
                f"Deplasman {abs(gol_fark)} gol farkla onude"
            )
        return (
            "GOL OLACAK (S)",
            f"Toplam {toplam_gol} gol, mac cok acik"
        )

    elif strateji == "KIRMIZI_KART":
        return (
            "GOL OLACAK (S)",
            "Kirmizi kart sonrasi duzensizlik ve acik alan — gol beklentisi yuksek"
        )

    elif strateji == "YUKSEK_CORNER":
        if corner_ev > corner_dep:
            return (
                "EV GOL ATACAK (S)",
                f"Ev sahibi corner dominant ({corner_ev}-{corner_dep}) — "
                f"kanat baskisi golde sonuclanabilir"
            )
        return (
            "GOL OLACAK (S)",
            f"Toplam {corner_ev+corner_dep} corner — mac cok aktif"
        )

    # GENEL
    if gol_fark >= 2 and possession_ev >= 50:
        return (
            "EV GOL ATACAK (S)",
            f"Ev sahibi {gol_fark} gol farkla onude, sahaya hakim"
        )
    elif gol_fark <= -2:
        return (
            "DEP GOL ATACAK (S)",
            f"Deplasman {abs(gol_fark)} gol farkla onude"
        )
    elif toplam_gol >= 3 and ev_gol > 0 and dep_gol > 0:
        return (
            "GOL OLACAK (S)",
            f"Mac cok acik, {toplam_gol} gol ve tempo yuksek"
        )
    elif possession_ev >= 65:
        return (
            "EV GOL ATACAK (S)",
            f"Ev sahibi topu hukmedıyor (%{possession_ev}) — "
            f"baskinin gole donusmesi bekleniyor"
        )
    return (
        "GOL OLACAK (S)",
        f"Mac aktif — {shots_ev+shots_dep} isabetli sut, "
        f"{dangerous_ev+dangerous_dep} tehlikeli atak"
    )


# ================================================
# KASA YÖNETİMİ
# ================================================
def kasa_hesapla(puan, dakika, ah_deger):
    ah_bonus = 0.5 if -1.5 <= ah_deger <= -0.75 else 0

    if puan >= 12 and 54 <= dakika <= 60:
        return 4.0
    elif puan >= 10:
        return 3.0 + ah_bonus
    elif puan >= 8:
        return 2.0 + ah_bonus
    elif puan >= 6:
        return 1.5
    return 1.0


# ================================================
# GEMİNİ AI — SADECE FİLTREDEN GEÇEN MAÇLAR
# ================================================
async def gemini_analiz(mac, puan, strateji, tahmin, neden, wc):
    if not GEMINI_KEY:
        return "AI aktif degil.", None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"

    son_gol = mac.get('son_gol', 0)
    dakika = mac.get('dakika', 0)
    gecen = dakika - son_gol if son_gol > 0 else dakika

    prompt = f"""Sen deneyimli bir canli futbol bahis analistsin.
Su maci SADECE bu maca ozgu, somut bicimde degerlendir. Genel laflar etme.

MAC: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}
Lig: {mac['lig']} | Dakika: {dakika}
Isabetli Sut: Ev={mac['shots_on_target_ev']} Dep={mac['shots_on_target_dep']}
Top Hakimiyeti: Ev=%{mac['possession_ev']} Dep=%{mac.get('possession_dep',50)}
Tehlikeli Atak: Ev={mac['dangerous_attacks_ev']} Dep={mac['dangerous_attacks_dep']}
Corner: Ev={mac.get('corner_ev',0)} Dep={mac.get('corner_dep',0)}
Son gol: {son_gol}. dakikada atildi ({gecen} dk once)
Asian Handicap: {mac.get('ah_deger',0) or 'Veri yok'}
Winning Code: {wc['detay']}

Bot karari: {tahmin}
Neden: {neden}
Sinyal puani: {puan}/12

Simdi su sorulari dusun ve SADECE BU MACA OZGU yorum yap:
- Son gol {gecen} dk once atildi. Momentum hala guclu mu yoksa mac soguyor mu?
- {mac['ev_gol']}-{mac['dep_gol']} skoru ve istatistikler {tahmin} tahminini destekliyor mu?
- Kirmizi bayrak var mi? (ornegin: gol atan takim savunmaya cekildi mi?)
- Bot kararini onayli yor musun?

Maksimum 2 cumle, net, somut, bu maca ozgu.

JSON don: {{"yorum": "analiz", "gir": true/false, "kasa": 1.5}}"""

    try:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 250}
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload,
                timeout=aiohttp.ClientTimeout(total=12)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text'].strip()
                    if "```" in text:
                        text = text.split("```")[1].replace("json", "").strip()
                    result = json.loads(text)
                    yorum = result.get('yorum', '')
                    kasa = float(result.get('kasa', 1.5))
                    if not result.get('gir', True):
                        kasa = 0.0
                    logger.info(f"Gemini: gir={result.get('gir')} kasa={kasa}")
                    return yorum, kasa
                else:
                    logger.error(f"Gemini {resp.status}")
                    return None, None
    except Exception as e:
        logger.error(f"Gemini: {e}")
        return None, None


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
                f"En iyi: {en_iyi[0]}"
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
# VERİ ÇEKME — OPTİMİZE (Tek endpoint)
# ================================================
async def macları_cek():
    """
    Tum istatistikler tek endpoint'ten geliyor.
    Ayri corner/odds istegi yok = API hak tasarrufu.
    """
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

                            # Odds (fixture icinde varsa)
                            for bet in f.get('odds', {}).get('bets', []):
                                if 'asian handicap' in bet.get('name', '').lower():
                                    for v in bet.get('values', []):
                                        if 'home' in v.get('value', '').lower():
                                            parts = v.get('value', '').split()
                                            for p in parts:
                                                try:
                                                    stats['ah_deger'] = float(p)
                                                    break
                                                except:
                                                    pass

                            # Son gol
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
        logger.error(f"API: {e}")
    return maclar


# ================================================
# BİLDİRİM — NET VE AÇIKLAMALI
# ================================================
async def bildirim_gonder(bot, mac, puan, puan_detay, strateji, tahmin, neden, ai_yorum, kasa):
    # AI iptal mi?
    if kasa == 0 and ai_yorum:
        mesaj = (
            f"AI UYARISI - GIRME!\n"
            f"{mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
            f"{mac['dakika']}dk | {mac['lig']}\n\n"
            f"Puan yeterli ama AI riskli buldu:\n{ai_yorum}"
        )
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, 0)
        return

    if puan >= 12:
        karar = "KESIN GIR"
        emoji = "🔥🔥"
    elif puan >= 9:
        karar = "KESIN GIR"
        emoji = "🔥"
    elif puan >= 7:
        karar = "GIREBILIRSiN"
        emoji = "✅"
    else:
        karar = "DIKKATLI OL"
        emoji = "⚠️"

    bar = "█" * min(puan, 12) + "░" * max(0, 12 - puan)
    puan_str = "\n".join(puan_detay[:6])

    ah = mac.get('ah_deger', 0)
    corner_toplam = mac.get('corner_toplam', 0)

    istat = (
        f"Sut: {mac['shots_on_target_ev']}/{mac['shots_on_target_dep']} "
        f"| Top: %{mac['possession_ev']}/%{mac.get('possession_dep',50)}\n"
        f"Atak: {mac['dangerous_attacks_ev']}/{mac['dangerous_attacks_dep']} "
        f"| Corner: {mac.get('corner_ev',0)}/{mac.get('corner_dep',0)}"
        + (f" | AH: {ah}" if ah != 0 else "")
    )

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
        f"AI ANALIZ: {ai_yorum if ai_yorum else '-'}\n\n"
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
    # Bu kısım hatayı düzelten kritik kısımdır.
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()

    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "MAC ANALIZ BOTU - OPTIMIZE SISTEM\n\n"
                "Zamanlama:\n"
                "Hafta ici (Pzt-Cuma): 19:00-00:00\n"
                "Hafta sonu (Cmt-Pzr): 19:00-23:00\n"
                "Diger saatler: Uyku (API koruma)\n\n"
                "Ozellikler:\n"
                "Winning Code hard filter (VU/TUM/MA/DIYI)\n"
                "Altin pencere bonuslari\n"
                "Beraberlik + Value bonuslari\n"
                "Net tahmin + neden aciklamasi\n"
                "Puan detayli gosterim\n"
                "Gemini AI maca ozgu derin analiz\n"
                "Cooling Off korumasi\n"
                "Tek API istegi (hak tasarrufu)\n\n"
                f"Min sinyal: {MIN_PUAN}/12\n"
                "Hazir — 19:00'dan itibaren aktif!"
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

            # Haftalik rapor - Pazartesi 09:00
            if simdi.weekday() == 0 and simdi.hour == 9 and son_haftalik != bugun:
                await haftalik_rapor(bot)
                son_haftalik = bugun

            # Aylik rapor - Ayin 1'i 09:00
            if simdi.day == 1 and simdi.hour == 9 and son_aylik != bugun:
                await aylik_rapor(bot)
                son_aylik = bugun

            sure = kontrol_suresi_al()

            # UYKU MODU
            if sure is None:
                if not uyku_bildirimi:
                    sonraki = sonraki_aktif_saat()
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"UYKU MODU\nAPI hak tasarrufu aktif.\nSonraki aktif: {sonraki}"
                    )
                    uyku_bildirimi = True
                    logger.info("Uyku moduna gecildi")
                await asyncio.sleep(1800)  # 30 dk
                continue
            else:
                if uyku_bildirimi:
                    gun = simdi.weekday()
                    gun_str = "Hafta sonu" if gun >= 5 else "Hafta ici"
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"UYANDIM! {gun_str} modu aktif\nKontrol: {sure//60}dk\nWinning Code sistemi hazir!"
                    )
                    uyku_bildirimi = False

            # MAC TARAMA
            maclar = await macları_cek()
            aktif_idler = [m['id'] for m in maclar]

            # Biten maclar
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

            # Aktif maclar
            for mac in maclar:
                puan, puan_detay, strateji, wc = sinyal_hesapla(mac)
                mac_id = mac['id']

                # Takip listesi
                if mac_id in bildirim_gonderilen:
                    biten_maclar[mac_id] = {
                        'ev': mac['ev'], 'dep': mac['dep'],
                        'tahmin': bildirim_gonderilen[mac_id]['tahmin'],
                        'bas_ev': bildirim_gonderilen[mac_id]['ev_gol'],
                        'bas_dep': bildirim_gonderilen[mac_id]['dep_gol'],
                        'son_ev': mac['ev_gol'],
                        'son_dep': mac['dep_gol'],
                    }

                # WC gecmedi = sinyal yok
                if puan == 0:
                    continue

                if puan >= MIN_PUAN:
                    onceki = bildirim_gonderilen.get(mac_id, {}).get('puan', 0)
                    if puan > onceki:

                        # Cooling Off
                        cooling, cooling_msg = cooling_off_kontrol(mac)
                        if cooling:
                            logger.info(f"Cooling Off: {mac['ev']} - {cooling_msg}")
                            continue

                        tahmin, neden = tavsiye_uret(mac, strateji)
                        kasa = kasa_hesapla(puan, mac['dakika'], mac.get('ah_deger', 0))

                        # Gemini AI
                        ai_yorum, ai_kasa = await gemini_analiz(
                            mac, puan, strateji, tahmin, neden, wc
                        )
                        if ai_kasa is not None:
                            kasa = ai_kasa
                        if ai_yorum is None:
                            ai_yorum = "AI analiz atildi."

                        await bildirim_gonder(
                            bot, mac, puan, puan_detay, strateji,
                            tahmin, neden, ai_yorum, kasa
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
