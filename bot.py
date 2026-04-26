"""
MAC ANALIZ BOTU - TAM SISTEM
- Winning Code (VU/TUM/MA/DIYI)
- Corner verisi (ayri endpoint)
- Canli Odds / Asian Handicap
- Gemini AI (sadece filtreden gecen maclara)
- Altin Pencere bonuslari
- Cooling Off korumasi
- Dinamik kasa yonetimi
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
                ah_deger REAL,
                bildirim_zamani TIMESTAMP DEFAULT NOW(),
                sonuc TEXT DEFAULT 'BEKLIYOR',
                final_ev_gol INTEGER DEFAULT 0,
                final_dep_gol INTEGER DEFAULT 0
            )
        """)
        for kolon, tip in [
            ("ai_yorum", "TEXT"), ("kasa_yuzde", "REAL"),
            ("strateji", "TEXT"), ("ah_deger", "REAL")
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
                 puan, strateji, tahmin, ai_yorum, kasa_yuzde, ah_deger)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            """, mac['id'], mac['ev'], mac['dep'], mac['lig'],
                mac['dakika'], mac['ev_gol'], mac['dep_gol'],
                puan, strateji, tahmin, ai_yorum, kasa_yuzde,
                mac.get('ah_deger', 0.0))
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
# CORNER VERİSİ ÇEK (Ayrı Endpoint)
# ================================================
async def corner_cek(fixture_ids: list):
    """
    Corner verisini /fixtures/statistics endpoint'inden çek.
    fixture_ids: Maç ID listesi
    """
    if not fixture_ids:
        return {}

    corner_data = {}
    try:
        async with aiohttp.ClientSession() as session:
            for fid in fixture_ids[:10]:  # Max 10 maç
                url = f"{BASE_URL}/fixtures/statistics?fixture={fid}"
                async with session.get(
                    url, headers=API_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        stats = data.get('response', [])
                        corner_ev = corner_dep = 0
                        for team_stat in stats:
                            is_home = team_stat.get('team', {}).get('id') == team_stat.get('team', {}).get('id')
                            for s in team_stat.get('statistics', []):
                                if 'corner' in s.get('type', '').lower():
                                    val = int(s.get('value', 0) or 0)
                                    # İlk takım ev, ikinci dep
                                    if stats.index(team_stat) == 0:
                                        corner_ev = val
                                    else:
                                        corner_dep = val
                        corner_data[str(fid)] = {
                            'corner_ev': corner_ev,
                            'corner_dep': corner_dep,
                            'corner_toplam': corner_ev + corner_dep,
                            'corner_fark': abs(corner_ev - corner_dep)
                        }
                await asyncio.sleep(0.3)  # Rate limit
    except Exception as e:
        logger.error(f"Corner cekme hatasi: {e}")

    return corner_data


# ================================================
# CANLI ODDS / ASIAN HANDICAP ÇEK
# ================================================
async def odds_cek(fixture_ids: list):
    """
    Canlı Asian Handicap oranlarını çek.
    /odds/live endpoint
    """
    if not fixture_ids:
        return {}

    odds_data = {}
    try:
        async with aiohttp.ClientSession() as session:
            # Tüm canlı odds'ları tek seferde çek
            url = f"{BASE_URL}/odds/live"
            async with session.get(
                url, headers=API_HEADERS,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get('response', [])
                    logger.info(f"{len(results)} odds verisi bulundu")

                    for item in results:
                        fid = str(item.get('fixture', {}).get('id', ''))
                        if fid not in [str(x) for x in fixture_ids]:
                            continue

                        ah_deger = 0.0
                        ev_oran = 0.0
                        dep_oran = 0.0

                        for bet in item.get('bets', []):
                            bet_name = bet.get('name', '').lower()

                            # Asian Handicap
                            if 'asian handicap' in bet_name:
                                values = bet.get('values', [])
                                for v in values:
                                    if 'home' in v.get('value', '').lower():
                                        try:
                                            # "Home -1.5" formatından sayıyı çek
                                            parts = v.get('value', '').split()
                                            for p in parts:
                                                try:
                                                    ah_deger = float(p)
                                                    break
                                                except:
                                                    pass
                                            ev_oran = float(v.get('odd', 0) or 0)
                                        except:
                                            pass
                                    elif 'away' in v.get('value', '').lower():
                                        try:
                                            dep_oran = float(v.get('odd', 0) or 0)
                                        except:
                                            pass

                        odds_data[fid] = {
                            'ah_deger': ah_deger,
                            'ev_oran': ev_oran,
                            'dep_oran': dep_oran,
                        }
                else:
                    logger.error(f"Odds API hata: {resp.status}")
    except Exception as e:
        logger.error(f"Odds cekme hatasi: {e}")

    return odds_data


# ================================================
# WINNING CODE: VU/TÜM/MA/DİYİ SİMÜLASYONU
# ================================================
def winning_code_kontrol(mac):
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    son_gol = mac.get('son_gol', 0)
    dakika = mac.get('dakika', 0)

    # VU=1: Ev sahibi yeterli baskı
    VU = shots_ev >= 2 and possession_ev >= 45 and dangerous_ev >= 20

    # TÜM=1: Maç aktif
    TUM = (dangerous_ev + dangerous_dep) >= 30

    # MA=0: Momentum taze
    if son_gol > 0 and dakika > 0:
        son_golden_beri = dakika - son_gol
        MA = not (son_golden_beri > 8 and dangerous_ev < 15)
    else:
        MA = not (dakika > 20 and dangerous_ev < 10)

    # DİYİ=0: Deplasman savunmada
    DIYI = (
        dangerous_dep <= dangerous_ev * 0.6 and
        shots_dep <= shots_ev + 2
    )

    return {
        'VU': VU, 'TUM': TUM, 'MA': MA, 'DIYI': DIYI,
        'gecti': VU and TUM and MA and DIYI
    }


# ================================================
# ASIAN HANDICAP ANALİZİ
# ================================================
def ah_analiz(ah_deger, ev_gol, dep_gol, possession_ev):
    """
    Rapor:
    - AH -1.5 ile -0.75: Favori ev sahibi, "Ev Gol Atacak" için güçlü
    - AH 0.50 ile 1.25: Dengeli, "Gol Olacak" için uygun
    """
    puan = 0
    yorum = ""
    tahmin_oneri = ""

    if ah_deger == 0:
        return 0, "AH verisi yok", ""

    if -1.5 <= ah_deger <= -0.75:
        puan = 2
        yorum = f"AH {ah_deger} (Favori Ev Sahibi)"
        tahmin_oneri = "EV GOL ATACAK (S)"
    elif -0.75 < ah_deger <= 0:
        puan = 1
        yorum = f"AH {ah_deger} (Hafif Favori)"
        tahmin_oneri = "EV GOL ATACAK (S)"
    elif 0 < ah_deger <= 1.25:
        puan = 1
        yorum = f"AH {ah_deger} (Dengeli)"
        tahmin_oneri = "GOL OLACAK (S)"
    elif ah_deger > 1.25:
        puan = 1
        yorum = f"AH {ah_deger} (Dep Favori)"
        tahmin_oneri = "DEP GOL ATACAK (S)"

    return puan, yorum, tahmin_oneri


# ================================================
# CORNER ANALİZİ
# ================================================
def corner_analiz(corner_ev, corner_dep, corner_toplam):
    """
    Rapor:
    - Corner oranı >= 11.5: Elite tempo, gol beklentisi yüksek
    - Corner oranı 6-8: Merkezi oyun, yüksek şut gerekli
    """
    puan = 0
    yorum = ""

    if corner_toplam == 0:
        return 0, "Corner verisi yok"

    corner_fark = abs(corner_ev - corner_dep)

    if corner_toplam >= 12:
        puan = 3
        yorum = f"ELITE TEMPO Corner {corner_ev}-{corner_dep}"
    elif corner_toplam >= 9:
        puan = 2
        yorum = f"Yuksek Corner {corner_ev}-{corner_dep}"
    elif corner_toplam >= 6:
        puan = 1
        yorum = f"Orta Corner {corner_ev}-{corner_dep}"

    if corner_fark >= 5:
        puan += 1
        dominant = "Ev" if corner_ev > corner_dep else "Dep"
        yorum += f" | {dominant} Dominant"

    return puan, yorum


# ================================================
# ALTIN PENCERE ZAMAN BONUSLARI
# ================================================
def zaman_bonusu_hesapla(dakika):
    if 54 <= dakika <= 60:
        return 3, "POWER WINDOW 54-60dk"
    elif 24 <= dakika <= 36:
        return 2, "ERKEN BASKISI 24-36dk"
    elif 45 <= dakika <= 49:
        return 2, "UZATMA VOLATiLiTE 45-49dk"
    elif 7 <= dakika <= 15:
        return 1, "ERKEN ACILIS 7-15dk"
    elif dakika >= 62:
        return -2, "COOLiNG OFF 62+dk"
    return 0, ""


# ================================================
# COOLING OFF KORUMASI
# ================================================
def cooling_off_kontrol(mac):
    dakika = mac.get('dakika', 0)
    son_gol = mac.get('son_gol', 0)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    gol_fark = abs(mac.get('ev_gol', 0) - mac.get('dep_gol', 0))

    if son_gol > 0:
        son_golden_beri = dakika - son_gol
        if son_golden_beri > 7 and (dangerous_ev + dangerous_dep) < 25:
            return True, f"Son gol {son_golden_beri}dk once, dusuk atak"

    if gol_fark >= 3 and dakika >= 62:
        return True, "Skor netlesmis + Gec donem"

    return False, ""


# ================================================
# ANA SİNYAL SİSTEMİ
# ================================================
def sinyal_hesapla(mac):
    puan = 0
    aktif = []
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

    # ---- WINNING CODE ----
    wc = winning_code_kontrol(mac)
    if wc['VU']:
        puan += 1
        aktif.append("VU=1")
    if wc['TUM']:
        puan += 1
        aktif.append("TUM=1")
    if wc['MA']:
        puan += 1
        aktif.append("MA=0 (Taze)")
    if wc['DIYI']:
        puan += 1
        aktif.append("DIYI=0")

    # ---- GOL SİNYALLERİ ----
    if kg_var:
        puan += 1
        aktif.append("KG VAR")
    if gol_fark >= 2:
        puan += 1
        aktif.append(f"Fark {gol_fark}")
        stratejiler.append("BUYUK_FARK")
    if toplam_gol >= 3:
        puan += 1
        aktif.append(f"{toplam_gol} Gol")
    if toplam_gol >= 4:
        puan += 1
        aktif.append(f"{toplam_gol} GOL PATLAMA!")
        stratejiler.append("GOL_PATLAMASI")
    if gol_hizi >= 0.10:
        puan += 1
        aktif.append(f"Hiz {gol_hizi}/dk")

    # ---- BERABERLIK BONUSU ----
    if esit_skor and toplam_gol <= 2:
        puan += 2
        aktif.append(f"BERABERLIK BONUS ({ev_gol}-{dep_gol})")
        stratejiler.append("BERABERLIK")

    # ---- SHOTS ----
    if shots_toplam >= 8:
        puan += 1
        aktif.append(f"Sut {shots_toplam}")
        stratejiler.append("YUKSEK_SUT")
    if shots_toplam >= 12:
        puan += 1
        aktif.append(f"COK Sut {shots_toplam}!")
    if abs(shots_ev - shots_dep) >= 5:
        puan += 1
        d = mac['ev'] if shots_ev > shots_dep else mac['dep']
        aktif.append(f"Sut Dom: {d[:12]}")
        stratejiler.append("SUT_DOMINANT")

    # ---- POSSESSİON ----
    poss_fark = abs(possession_ev - possession_dep)
    if poss_fark >= 20:
        puan += 1
        d = mac['ev'] if possession_ev > possession_dep else mac['dep']
        aktif.append(f"Top Dom: %{max(possession_ev,possession_dep)}")
        stratejiler.append("POSSESSION_DOM")

    # ---- DANGEROUS ATTACKS ----
    if dangerous_toplam >= 60:
        puan += 1
        aktif.append(f"Atak {dangerous_toplam}")
        stratejiler.append("YUKSEK_ATAK")
    if dangerous_toplam >= 100:
        puan += 1
        aktif.append(f"COK Atak {dangerous_toplam}!")

    # ---- CORNER (YENİ) ----
    corner_puan, corner_yorum = corner_analiz(corner_ev, corner_dep, corner_toplam)
    if corner_puan > 0:
        puan += corner_puan
        aktif.append(f"CORNER: {corner_yorum}")
        if corner_puan >= 2:
            stratejiler.append("YUKSEK_CORNER")

    # ---- ASIAN HANDICAP (YENİ) ----
    ah_puan, ah_yorum, ah_tahmin = ah_analiz(ah_deger, ev_gol, dep_gol, possession_ev)
    if ah_puan > 0:
        puan += ah_puan
        aktif.append(f"AH: {ah_yorum}")
        if ah_puan >= 2:
            stratejiler.append("AH_FAVORI")

    # ---- KARTLAR ----
    if kirmizi >= 1:
        puan += 1
        aktif.append("KIRMIZI KART!")
        stratejiler.append("KIRMIZI_KART")
    if (sari_ev + sari_dep) >= 4:
        puan += 1
        aktif.append(f"Kart Tansiyon {sari_ev+sari_dep}")

    # ---- ÖZEL SENARYOLAR ----
    if toplam_gol == 0 and shots_toplam >= 8 and dangerous_toplam >= 50:
        puan += 2
        aktif.append("0-0 COK AKTIF - VALUE!")
        stratejiler.append("GOLSUZ_AKTIF")

    if dep_gol > ev_gol and possession_ev >= 55 and shots_ev >= shots_dep:
        puan += 2
        aktif.append("FAVORi GERiDE ama DOMINANT - VALUE!")
        stratejiler.append("VALUE_GIRISI")

    if 45 <= dakika <= 60 and shots_toplam >= 6:
        puan += 1
        aktif.append(f"2.Yari+{shots_toplam}Sut")
        stratejiler.append("IY_BASKISI_2Y")

    if son_gol >= 70:
        puan += 1
        aktif.append(f"Son Gol {son_gol}dk")

    # ---- ALTIN PENCERE ----
    zaman_bonus, zaman_label = zaman_bonusu_hesapla(dakika)
    if zaman_bonus != 0:
        puan += zaman_bonus
        if zaman_label:
            aktif.append(f"ZAMAN: {zaman_label} ({'+' if zaman_bonus > 0 else ''}{zaman_bonus}p)")

    strateji_adi = stratejiler[0] if stratejiler else "GENEL"
    return puan, aktif, strateji_adi, wc, ah_tahmin


# ================================================
# TAHMİN ÜRETİCİ
# ================================================
def tavsiye_uret(mac, strateji, wc, ah_tahmin=""):
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    gol_fark = ev_gol - dep_gol
    toplam_gol = ev_gol + dep_gol
    possession_ev = mac.get('possession_ev', 50)
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)

    # AH tavsiyesi varsa öncelikli kullan
    if ah_tahmin and strateji in ["AH_FAVORI", "BERABERLIK", "GOLSUZ_AKTIF"]:
        return ah_tahmin

    if strateji == "VALUE_GIRISI":
        return "EV KAZANIR VEYA BERABERE"
    elif strateji == "GOLSUZ_AKTIF":
        if possession_ev >= 60:
            return "EV GOL ATACAK (S)"
        return "GOL OLACAK (S)"
    elif strateji == "SUT_DOMINANT":
        return "EV GOL ATACAK (S)" if shots_ev > shots_dep else "DEP GOL ATACAK (S)"
    elif strateji == "GOL_PATLAMASI":
        if gol_fark >= 2:
            return "EV GOL ATACAK (S)"
        elif gol_fark <= -2:
            return "DEP GOL ATACAK (S)"
        return "GOL OLACAK (S)"
    elif strateji in ["IY_BASKISI_2Y", "YUKSEK_CORNER", "YUKSEK_ATAK"]:
        return "GOL OLACAK (S)"

    if gol_fark >= 2:
        return "EV GOL ATACAK (S)"
    elif gol_fark <= -2:
        return "DEP GOL ATACAK (S)"
    elif toplam_gol >= 3:
        return "GOL OLACAK (S)"
    elif possession_ev >= 60 and wc.get('DIYI'):
        return "EV GOL ATACAK (S)"
    return "GOL OLACAK (S)"


# ================================================
# KASA YÖNETİMİ
# ================================================
def kasa_hesapla(puan, strateji, dakika, wc, ah_deger):
    wc_tam = wc.get('gecti', False)

    if not wc_tam:
        return 0.0

    # AH faktörü — rapordaki Kelly kriteri
    ah_bonus = 0
    if -1.5 <= ah_deger <= -0.75:
        ah_bonus = 1.0  # Güçlü favori
    elif 0 < ah_deger <= 1.25:
        ah_bonus = 0.5  # Dengeli

    if puan >= 9 and 54 <= dakika <= 60:
        return min(4.0 + ah_bonus, 5.0)
    elif puan >= 9:
        return 3.0 + ah_bonus
    elif puan >= 7:
        return 1.5 + ah_bonus
    elif puan >= 6:
        return 1.0
    return 0.0


# ================================================
# GEMİNİ AI — SADECE FİLTREDEN GEÇEN MAÇLAR
# ================================================
async def gemini_analiz(mac, puan, strateji, tahmin, wc):
    if not GEMINI_KEY or puan < MIN_PUAN:
        return "AI atildi.", None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"

    wc_str = f"VU:{1 if wc['VU'] else 0} TUM:{1 if wc['TUM'] else 0} MA:{0 if wc['MA'] else 1} DIYI:{0 if wc['DIYI'] else 1}"
    ah = mac.get('ah_deger', 0)
    corner_t = mac.get('corner_toplam', 0)

    prompt = f"""Canli bahis analisti: Su maci elestirisel degerlendir.

MAC: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}
Lig: {mac['lig']} | Dakika: {mac['dakika']}
Sut(ev/dep): {mac['shots_on_target_ev']}/{mac['shots_on_target_dep']}
Top: %{mac['possession_ev']}/%{mac['possession_dep']}
Atak: {mac['dangerous_attacks_ev']}/{mac['dangerous_attacks_dep']}
Corner: {mac.get('corner_ev',0)}-{mac.get('corner_dep',0)} (Toplam:{corner_t})
Asian Handicap: {ah}
Son gol: {mac['son_gol']}dk
Winning Code: {wc_str}
Strateji: {strateji} | Tahmin: {tahmin} | Puan: {puan}/12

Cooling off var mi? Momentum taze mi? AH piyasayi destekliyor mu?
2 cumle analiz et.

JSON: {{"yorum": "analiz", "risk": "DUSUK/ORTA/YUKSEK", "kasa_yuzde": 1.5, "girilmeli": true}}"""

    try:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 200}
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
                    yorum = result.get('yorum', 'OK')
                    kasa = float(result.get('kasa_yuzde', 1.5))
                    if not result.get('girilmeli', True):
                        kasa = 0.0
                    logger.info(f"Gemini: risk={result.get('risk')} kasa={kasa}")
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
# ANA VERİ ÇEKME
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
                                idx = 0
                                for sg in f.get('statistics', []):
                                    if sg.get('team', {}).get('id') == home_id:
                                        idx = 0
                                    else:
                                        idx = 1
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
                                    elif 'corner' in tip:
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
    """Odds verisini mevcut maçlara ekle"""
    if not maclar:
        return maclar

    fixture_ids = [m['id'] for m in maclar]
    odds_data = await odds_cek(fixture_ids)

    for mac in maclar:
        if mac['id'] in odds_data:
            mac['ah_deger'] = odds_data[mac['id']].get('ah_deger', 0.0)
            mac['ev_oran'] = odds_data[mac['id']].get('ev_oran', 0.0)
            mac['dep_oran'] = odds_data[mac['id']].get('dep_oran', 0.0)

    return maclar


# ================================================
# BİLDİRİM
# ================================================
async def bildirim_gonder(bot, mac, puan, sinyaller, strateji, tahmin, ai_yorum, kasa_yuzde, uyari=""):
    if uyari:
        mesaj = (
            f"UYARI: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
            f"Puan {puan} ama: {uyari}\n"
            f"GIRME!"
        )
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        return

    if kasa_yuzde == 0:
        if ai_yorum and ai_yorum != "AI atildi.":
            mesaj = (
                f"AI UYARISI: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
                f"Puan: {puan} | AI: {ai_yorum}\nGIRME!"
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

    ah = mac.get('ah_deger', 0)
    ah_str = f"AH: {ah}" if ah != 0 else "AH: -"
    corner_str = f"Corner: {mac.get('corner_ev',0)}-{mac.get('corner_dep',0)}" if mac.get('corner_toplam', 0) > 0 else "Corner: -"

    istat = (
        f"Sut: {mac['shots_on_target_ev']}/{mac['shots_on_target_dep']} "
        f"| Top: %{mac['possession_ev']}/%{mac['possession_dep']}\n"
        f"Atak: {mac['dangerous_attacks_ev']}/{mac['dangerous_attacks_dep']} "
        f"| {corner_str} | {ah_str}"
    )

    mesaj = (
        f"{emoji} {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"Lig: {mac['lig']} | {mac['dakika']}dk\n\n"
        f"Sinyal: {puan}/12\n{bar}\n\n"
        f"{sinyal_metni}\n\n"
        f"{istat}\n\n"
        f"Strateji: {strateji}\n"
        f"AI: {ai_yorum}\n\n"
        f"KASA: %{kasa_yuzde}\n\n"
        f"{'='*20}\n"
        f"{karar} | {tahmin}\n"
        f"{'='*20}"
    )

    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa_yuzde)
        logger.info(f"Bildirim: {mac['ev']} vs {mac['dep']} | {strateji} | {puan}p | %{kasa_yuzde}")
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
                "MAC ANALIZ BOTU - TAM SISTEM\n\n"
                "Aktif modüller:\n"
                "Winning Code (VU/TUM/MA/DIYI)\n"
                "Corner verisi\n"
                "Asian Handicap / Odds\n"
                "Gemini AI (filtreli)\n"
                "Altin Pencere bonuslari\n"
                "Cooling Off korumasi\n"
                "Dinamik kasa yonetimi\n\n"
                f"Min sinyal: {MIN_PUAN}/12\n\n"
                "00:00-11:30 Uyku\n"
                "11:30-15:00 (8dk)\n"
                "15:00-19:00 (7dk)\n"
                "19:00-23:00 (6dk)\n"
                "23:00-00:00 Uyku"
            )
        )
        logger.info("Bot basladi - Tam Sistem!")
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
                        text=f"UYANDIM! Tam sistem aktif - {sure//60}dk kontrol"
                    )
                    uyku_bildirimi = False

            # Maçları çek
            maclar = await macları_cek()

            # Odds ekle (ayrı API çağrısı)
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
                puan, sinyaller, strateji, wc, ah_tahmin = sinyal_hesapla(mac)
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
                            if puan >= 8:
                                await bildirim_gonder(
                                    bot, mac, puan, sinyaller, strateji,
                                    "N/A", "", 0, cooling_msg
                                )
                            continue

                        tahmin = tavsiye_uret(mac, strateji, wc, ah_tahmin)
                        kasa_yuzde = kasa_hesapla(
                            puan, strateji, mac['dakika'], wc,
                            mac.get('ah_deger', 0)
                        )

                        if kasa_yuzde == 0 and not wc['gecti']:
                            continue

                        # Gemini AI
                        ai_yorum, ai_kasa = await gemini_analiz(
                            mac, puan, strateji, tahmin, wc
                        )
                        if ai_kasa is not None:
                            kasa_yuzde = ai_kasa
                        if ai_yorum is None:
                            ai_yorum = "AI analiz atildi."

                        await bildirim_gonder(
                            bot, mac, puan, sinyaller, strateji,
                            tahmin, ai_yorum, kasa_yuzde
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
