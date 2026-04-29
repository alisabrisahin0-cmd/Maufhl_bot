"""
MAC ANALIZ BOTU - MUKEMMEL SISTEM v2.0
Zamanlama:
- Hafta ici (Pzt-Cuma): 19:00 - 00:00
- Hafta sonu (Cmt-Pzr): 19:00 - 23:00
Format: Istenen gorunum + Derin Gemini analizi
Hata Düzeltmeleri ve API v3 Optimizasyonları Yapıldı.
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta
import json

# --- Yapılandırma ve Ortam Değişkenleri ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "6")) # Sinyal puanı alt sınırı

# --- Günlükleme (Logging) ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("AnalizBotu")

# --- Global Değişkenler ---
bildirim_gonderilen = {} # Hangi maça, kaç puanla sinyal atıldı
biten_maclar = {}         # Sonuç takibi için aktif maçların veritabanı
db_pool = None

# --- API-Sports v3 Başlıkları ---
API_HEADERS = {
    "x-apisports-key": APISPORTS_KEY,
    "x-apisports-host": "v3.football.api-sports.io"
}
BASE_URL = "https://v3.football.api-sports.io"


# ================================================
# ZAMAN YÖNETİMİ
# ================================================
def aktif_mi():
    """Botun çalışma saatlerinde olup olmadığını kontrol eder."""
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday()  # 0=Pzt, 6=Pzr
    if gun <= 4:  # Hafta ici Pzt-Cuma
        return 19 <= saat <= 23  # 19:00 - 23:59
    else:  # Hafta sonu Cmt-Pzr
        return 19 <= saat <= 22  # 19:00 - 22:59
    return False


def sonraki_aktif():
    """Bir sonraki aktifleşme zamanını metin olarak döner."""
    gun = datetime.now().weekday()
    return "Bugün 19:00 (Hafta ici)" if gun <= 4 else "Bugün 19:00 (Hafta sonu)"


# ================================================
# VERİTABANI İŞLEMLERİ
# ================================================
async def db_baglanti_kur():
    """PostgreSQL veritabanına bağlanır ve tabloları oluşturur."""
    global db_pool
    if db_pool: return

    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        # Sinyaller Tablosu
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
                puan REAL,
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
        # Gerekli sütunların varlığını kontrol et ve gerekirse ekle (v1.0'dan yükseltme için)
        async with db_pool.acquire() as conn:
            for kolon, tip in [
                ("ai_yorum", "TEXT"), ("kasa_yuzde", "REAL"), ("strateji", "TEXT")
            ]:
                check_sql = f"SELECT column_name FROM information_schema.columns WHERE table_name='sinyaller' AND column_name='{kolon}';"
                exists = await conn.fetchval(check_sql)
                if not exists:
                    await conn.execute(f"ALTER TABLE sinyaller ADD COLUMN {kolon} {tip}")
                    logger.info(f"DB: Sütun eklendi: {kolon}")

        logger.info("Veritabani baglantisi kuruldu!")
    except Exception as e:
        logger.error(f"DB Baglanti Hatasi: {e}")


async def sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa):
    """Gönderilen bir sinyali veritabanına kaydeder."""
    try:
        if db_pool:
            await db_pool.execute("""
                INSERT INTO sinyaller
                (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol,
                 puan, strateji, tahmin, ai_yorum, kasa_yuzde)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """, mac['id'], mac['ev'], mac['dep'], mac['lig'],
                mac['dakika'], mac['ev_gol'], mac['dep_gol'],
                puan, strateji, tahmin, ai_yorum, kasa)
    except Exception as e:
        logger.error(f"Sinyal Kayit Hatasi: {e}")


async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    """Maç bittiğinde sinyalin sonucunu veritabanında günceller."""
    try:
        if db_pool:
            await db_pool.execute("""
                UPDATE sinyaller SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3
                WHERE mac_id=$4 AND sonuc='BEKLIYOR'
            """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e:
        logger.error(f"Sonuc Guncelleme Hatasi: {e}")


# ================================================
# MANTIK MOTORU - KODLAR VE BONUŞLAR
# ================================================
def winning_code_kontrol(mac):
    """
    VU: Vurucu Güç (Baskı)
    TÜM: Toplam Maç Aktivitesi
    MA: Maçın Akışı (Son gol ve tempo)
    DİYİ: Deplasman İyileşmesi (Underdog kontrolü)
    """
    shots_ev = mac.get('shots_on_target_ev', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    son_gol = mac.get('son_gol', 0)
    dakika = mac.get('dakika', 1) # 0'a bölme hatası önlemi

    # Mantık kriterleri
    VU = shots_ev >= 2 and possession_ev >= 42 and dangerous_ev >= 15
    TUM = (dangerous_ev + dangerous_dep) >= 25

    # MA Hesabı (Dakika-Son Gol farkında tempo kontrolü)
    if son_gol > 0:
        gecen = max(0, dakika - son_gol) # Negatif değer önlemi
        MA = not (gecen > 8 and (dangerous_ev + dangerous_dep) < 20)
    else:
        # Son gol yoksa (0-0) erken tempo kontrolü
        MA = not (dakika > 15 and dangerous_ev < 8)

    # Deplasman takımı güçlü mü, yoksa pasif mi?
    DIYI = dangerous_dep <= dangerous_ev * 0.65 and mac.get('shots_on_target_dep',0) <= shots_ev + 3

    gecti = VU and TUM and MA and DIYI
    return {
        'gecti': gecti,
        'VU': VU, 'TUM': TUM, 'MA': MA, 'DIYI': DIYI,
        'VU_val': 1 if VU else 0,
        'TUM_val': 1 if TUM else 0,
        'MA_val': 1 if MA else 0,
        'DIYI_val': 1 if DIYI else 0,
        'detay': f"(VU:{VU} TUM:{TUM} MA:{MA} DIYI:{DIYI})"
    }


def zaman_bonusu(dakika):
    """Belirli dakika aralıklarında ek puan bonuşu verir."""
    if 54 <= dakika <= 60:
        return 3.5, "Altın Pencere (54-62') +3.5", "POWER_WINDOW"
    elif 24 <= dakika <= 36:
        return 2.0, "Erken Baskı (24-36') +2.0", "ERKEN_BASKISI"
    elif 45 <= dakika <= 49:
        return 2.0, "Uzatma Volatilite (45-49') +2.0", "UZATMA"
    elif 7 <= dakika <= 15:
        return 1.0, "Erken Açılış (7-15') +1.0", "ERKEN_ACILIS"
    return 0, "", ""


def cooling_off_kontrol(mac):
    """
    Maçın son golünden sonraki tempo düşüklüğünü kontrol eder.
    Aktif bir sinyal varsa 'cool down' (bekle) der.
    """
    dakika = mac.get('dakika', 1)
    son_gol = mac.get('son_gol', 0)
    dangerous_toplam = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    corner_toplam = mac.get('corner_ev', 0) + mac.get('corner_dep', 0)
    gol_fark = abs(mac.get('ev_gol', 0) - mac.get('dep_gol', 0))

    # Skor netse ve maçın sonundaysa aktivite düşük olabilir.
    if gol_fark >= 3 and dakika >= 62 and dangerous_toplam < 20:
        return True, f"Skor net ({mac['ev_gol']}-{mac['dep_gol']}), tempoyu düşürdü"

    if son_gol > 0:
        gecen = max(0, dakika - son_gol)
        # Son golün üzerinden süre geçti ve tempo düşük
        if gecen > 7 and dangerous_toplam < 20 and corner_toplam < 3:
            return True, f"Son gol {gecen} dk önce, tempo henüz dönmedi."
    return False, ""


# ================================================
# SİNYAL PUANLAMA SİSTEMİ
# ================================================
def sinyal_hesapla(mac):
    """Tüm maç verilerini alır, puanlar ve bir strateji belirler."""

    # ---- LİG KATSAYILARI (Volatility Index) ----
    LIG_KATSAYISI = {
        # Yüksek gol ligleri
        'Eredivisie': 1.3, 'Bundesliga': 1.2, 'Premier League': 1.15,
        'Champions League': 1.1, 'La Liga': 1.1, 'Ligue 1': 1.1,
        'Serie A': 1.0, 'Super Lig': 1.1,
        # Düşük gol ligleri
        'Serie B': 0.9, 'Ligue 2': 0.9, 'Serie C': 0.85
    }
    lig = mac.get('lig', '')
    lig_katsayisi = 1.0
    for lig_adi, katsayi in LIG_KATSAYISI.items():
        if lig_adi.lower() in lig.lower():
            lig_katsayisi = katsayi
            break

    # -- Winning Code Kontrolü --
    wc = winning_code_kontrol(mac)

    # Değerlerin Hazırlanması
    dakika = max(mac.get('dakika', 1), 1)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    toplam_gol = ev_gol + dep_gol
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    corner_ev = mac.get('corner_ev', 0)
    corner_dep = mac.get('corner_dep', 0)
    ah_deger = mac.get('ah_deger', 0.0)

    puan = 0.0
    detay = []
    stratejiler = []

    # -- Sinyal Başlangıç --
    if not wc['gecti']:
        # Winning Code onay almadıysa sinyal üretilmez (0 puan döner)
        return 0, [], "", wc

    # -- Temel Puan --
    puan += 4.0
    detay.append(
        f"✅ Winning Code Onayı (VU:{wc['VU_val']} TÜM:{wc['TUM_val']} "
        f"MA:{wc['MA_val']} DİYİ:{wc['DIYI_val']})"
    )

    # -- DAPM (Minute Dangerous Attacks) - Tempo Kontrolü --
    dapm_ev = round(dangerous_ev / dakika, 2)
    dapm_dep = round(dangerous_dep / dakika, 2)
    if dapm_ev >= 1.5:
        puan += 2.0; detay.append(f"🌪️ Ev Ağır Baskı ({dapm_ev} Atak/Dk) +2.0")
        stratejiler.append("AGIR_BASKI_EV")
    elif dapm_ev >= 1.2:
        puan += 1.5; detay.append(f"🌪️ Ev Yüksek Baskı ({dapm_ev} Atak/Dk) +1.5")
    if dapm_dep >= 1.5:
        puan += 1.5; detay.append(f"🌪️ Dep Ağır Baskı ({dapm_dep} Atak/Dk) +1.5")

    # -- Şut Hızı (Shot Velocity) --
    spm_toplam = round((shots_ev + shots_dep) / dakika, 3)
    if spm_toplam >= 0.25: # Yaklaşık 4 dk'da 1 şut
        puan += 1.5; detay.append(f"🎯 Yüksek Şut Hızı ({spm_toplam}/Dk) +1.5")

    # -- Kontra Atak Tespiti --
    # Top Deplasman'da ama Ev Şut Atıyor veya tam tersi
    if mac.get('possession_dep',50) >= 60 and shots_ev >= shots_dep + 2 and dangerous_ev >= 10:
        puan += 1.5; detay.append(f"⚡ Ev Kontra Atak! Baskı Alırken Şut Atıyor +1.5")
    if possession_ev >= 60 and shots_dep >= shots_ev + 2 and dangerous_dep >= 10:
        puan += 1.5; detay.append(f"⚡ Dep Kontra Atak! Baskı Alırken Şut Atıyor +1.5")

    # -- Beraberlik Bonusu --
    if ev_gol == dep_gol and toplam_gol >= 2:
        puan += 1.5; detay.append(f"🤝 Beraberlik Bonusu (Aktif Maç) +1.5")
        stratejiler.append("BERABERLIK")

    # -- Gol Temposu --
    gol_hizi = round(toplam_gol / dakika, 3)
    if gol_hizi >= 0.15: # Dakika başına yüksek gol
        puan += 1.0; detay.append(f"⚡ Gol Hızı {gol_hizi}/dk (Tempo) +1.0")

    # -- Şut İstatistikleri --
    shots_toplam = shots_ev + shots_dep
    if shots_toplam >= 12:
        puan += 2.0; detay.append(f"🎯 {shots_toplam} İsabetli Şut +2.0")
    elif shots_toplam >= 8:
        puan += 1.0; detay.append(f"🎯 {shots_toplam} İsabetli Şut +1.0")
    if abs(shots_ev - shots_dep) >= 5:
        puan += 1.0; detay.append(f"🎯 Şut Üstünlüğü +1.0")

    # -- Topla Oynama (Dominasyon) --
    if possession_ev >= 65:
        puan += 2.0; detay.append(f"⚽ Ev Top Dom (%{possession_ev}) +2.0")
    elif possession_ev >= 58:
        puan += 1.0; detay.append(f"⚽ Ev Top Hakim (%{possession_ev}) +1.0")
    if possession_ev <= 35: # Deplasman dominasyonu
        puan += 1.5; detay.append(f"⚽ Deplasman Top Dom (%{100-possession_ev}) +1.5")

    # -- Corner İstatistikleri --
    corner_toplam = corner_ev + corner_dep
    if corner_toplam >= 12:
        puan += 2.0; detay.append(f"🚩 {corner_toplam} Corner (Elite) +2.0")
        stratejiler.append("YUKSEK_CORNER")
    elif corner_toplam >= 8:
        puan += 1.0; detay.append(f"🚩 {corner_toplam} Corner +1.0")

    # -- Asian Handicap (Piyasa Beklentisi) --
    if ah_deger != 0:
        if -1.5 <= ah_deger <= -0.75: # Ev güçlü favori
            puan += 2.0; detay.append(f"📈 AH {ah_deger} Ev Güçlü Favori +2.0")
            stratejiler.append("AH_FAVORI_EV")
        elif -0.75 < ah_deger < 0: # Ev hafif favori
            puan += 1.0; detay.append(f"📈 AH {ah_deger} Hafif Ev Fav +1.0")
        elif ah_deger >= 0.75: # Deplasman favori (göreli)
            puan += 1.5; detay.append(f"📈 AH {ah_deger} Deplasman Fav (Göreceli) +1.5")

    # -- Kırmızı Kart Bonusu (Kaos Mantığı) --
    # Puan artırılır çünkü kırmızı kart maçın kilitlenmesini de, açılmasını da sağlayabilir.
    kirmizi = mac.get('kirmizi_kart', 0)
    if kirmizi >=  red_cards := 1:
        puan += 1.5; detay.append(f"🟥 Kırmızı Kart Kaos Modu ({kirmizi}) +1.5")

    # -- 0-0 ve Aktif Maç (Yüksek Tempo) --
    if toplam_gol == 0 and dakika >= 40 and shots_toplam >= 6 and (dangerous_ev + dangerous_dep) >= 60:
        puan += 2.0; detay.append(f"💥 0-0 ama Çok Aktif (Yüksek Puan) +2.0")
        stratejiler.append("GOLSUZ_AKTIF")

    # -- Zamanlama Bonusu --
    z_bonus, z_label, z_strateji = zaman_bonusu(dakika)
    if z_bonus > 0:
        puan += z_bonus; detay.append(f"🔥 {z_label}")
        if z_strateji: stratejiler.append(z_strateji)

    # ---- LİG KATSAYISI UYGULA (Nihai Puan) ----
    if lig_katsayisi != 1.0:
        onceki_puan = puan
        puan = round(puan * lig_katsayisi, 1)
        detay.append(f"🏆 Lig Katsayısı x{lig_katsayisi} ({onceki_puan}→{puan})")

    strateji_adi = stratejiler[0] if stratejiler else "GENEL"
    return round(puan, 1), detay, strateji_adi, wc


# ================================================
# TAHMİN ÜRETME VE KASA YÖNETİMİ
# ================================================
def tavsiye_uret(mac, strateji):
    """Maçın verilerine ve belirlenen stratejiye göre nihai bir tahminde bulunur."""
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    possession_ev = mac.get('possession_ev', 50)
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)

    # Maçın durumunu yorumla (Kim daha iyi?)
    ev_üstün = (possession_ev >= 55) and (shots_ev >= shots_dep)
    dep_üstün = (possession_ev <= 42) and (shots_dep >= shots_ev)

    if strateji == "AGIR_BASKI_EV":
        return "EV GOL ATACAK (S)", f"Ev sahibi ağır baskıda ({shots_ev} şut, %{possession_ev} top)"
    elif strateji == "GOLSUZ_AKTIF":
        if ev_üstün:
            return "EV GOL ATACAK (S)", f"0-0 ama ev sahibi baskıda ({shots_ev} şut)"
        elif dep_üstün:
            return "DEP GOL ATACAK (S)", f"0-0 ama deplasman daha aktif ({shots_dep} şut)"
        return "GOL OLACAK (S)", f"0-0 ama maç çok aktif, iki taraf da gol istiyor"
    elif strateji == "BERABERLIK":
        if ev_üstün:
            return "EV GOL ATACAK (S)", f"Beraberlik ama ev sahibi dominant, kilidi açabilir"
        elif dep_üstün:
            return "DEP GOL ATACAK (S)", f"Beraberlik ama deplasman tehlikeli ataklar yapıyor"
        return "GOL OLACAK (S)", "Skor dengede, her iki taraf da galibiyet peşinde, maç açık"
    elif strateji == "AH_FAVORI_EV":
        if ev_gol <= dep_gol:
            return "EV GOL ATACAK (S)", "Piyasa favorisi ev geride ama sahada baskıyı kurmuş"
        return "EV GOL ATACAK (S)", "Ev sahibi favori ve tempoyu düşürmüyor"
    elif strateji == "YUKSEK_CORNER":
        return "GOL OLACAK (S)", f"Maç çok aktif ({mac.get('corner_toplam',0)} korner), gol ihtimali güçleniyor"
    elif strateji == "POWER_WINDOW":
        return "GOL OLACAK (S)", "Altın Pencere dakikaları (54-62), en yüksek gol yoğunluğu dönemi"
    elif strateji == "UZATMA":
        return "GOL OLACAK (S)", "İlk yarı uzatma dakikaları, volatilite ve hata riski yüksek"

    # Genel Strateji (Diğerleri)
    if ev_gol > dep_gol and ev_üstün:
        return "EV GOL ATACAK (S)", f"Ev önde ve sahayı domine ediyor (%{possession_ev} top)"
    elif dep_gol > ev_gol and dep_üstün:
        return "DEP GOL ATACAK (S)", f"Deplasman önde ve sahayı kontrol ediyor (%{100-possession_ev} top)"
    return "GOL OLACAK (S)", "Maç aktif ve iki tarafın da istatistikleri gol ihtimalini güçlendiriyor"


def sonraki_gol_tahmini(mac, strateji):
    """Sıradaki golü kimin atacağını tahmin etmeye çalışır."""
    if strateji == "AGIR_BASKI_EV": return f"Sıradaki Gol: {mac['ev'][:15]}"
    if strateji == "AH_FAVORI_EV": return f"Sıradaki Gol: {mac['ev'][:15]}"
    if strateji == "AH_FAVORI_DEP": return f"Sıradaki Gol: {mac['dep'][:15]}"
    if strateji == "GOLSUZ_AKTIF": return "Sıradaki Gol: Her İki Taraf"
    return "Sıradaki Gol: Bekleniyor"


def kasa_hesapla(puan, dakika, ah_deger):
    """Sinyal puanına ve maçın dakikasına göre kasa yüzdesini belirler."""
    if puan >= 12: return 4.0   # Elite Sinyal
    elif puan >= 10: return 3.0  # Güçlü Sinyal
    elif puan >= 8: return 2.0   # Güvenilir Sinyal
    elif puan >= MIN_PUAN: return 1.5
    return 1.0


# ================================================
# GEMİNİ AI — DERİN CANLI ANALİZ
# ================================================
async def gemini_analiz(session, mac, puan, strateji, tahmin, neden, wc):
    """Gemini Flash API'yi kullanarak maçı yorumlar."""
    if not GEMINI_KEY: return "AI aktif değil."

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"

    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    toplam_gol = ev_gol + dep_gol
    dakika = mac.get('dakika', 1)
    gecen = max(0, dakika - mac.get('son_gol', 0))

    prompt = f"""Sen çok deneyimli bir canlı bahis analistsin. Görülmüşsün, çok maç izlemişsin.

MAÇ: {mac['ev']} {ev_gol}-{dep_gol} {mac['dep']}
LİG: {mac['lig']} | DAKİKA: {dakika} | SKOR: {ev_gol}-{dep_gol}

İSTATİSTİKLER:
- Şut (isabetli): {mac['ev']}={mac['shots_on_target_ev']} vs {mac['dep']}={mac['shots_on_target_dep']}
- Topla Oynama: {mac['ev']}=%{mac['possession_ev']} vs {mac['dep']}=%{100-mac['possession_ev']}
- Tehlikeli Atak: {mac['ev']}={mac['dangerous_attacks_ev']} vs {mac['dep']}={mac['dangerous_attacks_dep']}
- Korner: {mac['ev']}={mac['corner_ev']} vs {mac['dep']}={mac['corner_dep']}
- Son gol: {gecen} dk önce
- Kırmızı kart: {mac.get('kirmizi_kart', 0)}
- Asian Handicap: {mac.get('ah_deger', 'yok')}

SİSTEM KARARI: {tahmin} | Sinyal Puanı: {puan}/12 | Strateji: {strateji}

GÖREV — İKİ KATMANLI ANALİZ YAP:

KATMAN 1 — İstatistiklerin Söylediği:
{mac['ev']} {mac['shots_on_target_ev']} şutla daha mı tehlikeli? Son gol {gecen} dk önce — momentum hala var mı?

KATMAN 2 — İstatistiklerin SÖYLEMEDIĞI (en önemli kısım):
Maçın bu dakikasında ve bu skor durumunda sahada ne oluyor olabilir? Oyuncular yorulmuş olabilir mi? Öndeki takımın tempoyu düşürme ihtimali var mı? Deplasman takımı bir kontra atak için mi bekliyor? Bu ligin genel gol karakteristiği nasıl? İstatistikler güçlü görünse de bir 'tuzağa' mı düşüyoruz, yoksa sahada gerçek bir baskı mı var?

KURAL: "Atak sürekliliği", "gol ihtimalini güçlendiriyor" gibi kalıp cümleler YASAK.
Maça özel, somut, keskin gözlem yap. Maksimum 3 Türkçe cümle.

Yalnızca şu JSON formatında yanıt ver: {{"yorum": "iki_katmanli_ozgun_analiz", "gir": true}}"""

    try:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.6, "maxOutputTokens": 200}
        }
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                text = data['candidates'][0]['content']['parts'][0]['text'].strip()
                if "```" in text:
                    text = text.split("```")[1].replace("json", "").strip()
                result = json.loads(text)
                yorum = result.get('yorum', '')
                logger.info(f"Gemini OK: gir={result.get('gir')} yorum={yorum[:20]}")
                return yorum, result.get('gir', True)
            else:
                logger.error(f"Gemini API {resp.status}")
                return None, True
    except Exception as e:
        logger.error(f"Gemini Analiz Hatasi: {e}")
        return None, True # Hata durumunda sinyali atlama


# ================================================
# RAPORLAR VE SONUÇ KONTROLÜ
# ================================================
async def haftalik_rapor(bot):
    """Veritabanından son 7 günlük başarı raporunu oluşturur."""
    try:
        if not db_pool: return
        rows = await db_pool.fetch(
            "SELECT * FROM sinyaller WHERE bildirim_zamani > $1 AND sonuc != 'BEKLIYOR'",
            datetime.now() - timedelta(days=7)
        )
        if not rows:
            await bot.send_message(chat_id=CHAT_ID, text="📊 HAFTALIK RAPOR: Maç bulunamadı.")
            return

        toplam = len(rows)
        kazanan = len([r for r in rows if r['sonuc'] == 'TUTTU'])
        oran = round(kazanan / toplam * 100, 1)

        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"📊 **HAFTALIK BAŞARI RAPORU**\n\n"
                f"Toplam Sinyal: {toplam}\n"
                f"Kazanan: {kazanan}\n"
                f"Kaybeden/DSTU: {toplam-kazanan}\n"
                f"Başarı Oranı: %{oran}"
            )
        )
    except Exception as e:
        logger.error(f"Haftalik Rapor Hatasi: {e}")


async def sonuc_kontrol(tahmin, bas_ev, bas_dep, fin_ev, fin_dep):
    """Maç bittiğindeki nihai skora göre tahminin tutup tutmadığını kontrol eder."""
    yeni_ev = fin_ev - bas_ev
    yeni_dep = fin_dep - bas_dep
    toplam = yeni_ev + yeni_dep
    if "GOL OLACAK" in tahmin or "ÜST" in tahmin:
        return "TUTTU" if toplam >= 1 else "DSTU"
    elif "EV GOL" in tahmin:
        return "TUTTU" if yeni_ev >= 1 else "DSTU"
    elif "DEP GOL" in tahmin:
        return "TUTTU" if yeni_dep >= 1 else "DSTU"
    return "BELIRSIZ"


# ================================================
# VERİ ÇEKME - API-SPORTS v3
# ================================================
async def maclari_temel_cek(session):
    """
    Sadece temel maç bilgilerini (id, skor, dakika) çeker.
    Bu, her döngüde bir kez çağrılır.
    """
    url = f"{BASE_URL}/fixtures?live=all"
    try:
        async with session.get(url, headers=API_HEADERS, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                logger.info(f"API: Canli {len(data.get('response', []))} mac bulundu.")
                return data.get('response', [])
            else:
                logger.error(f"API Temel Hatasi: {resp.status}")
                return []
    except Exception as e:
        logger.error(f"API Temel Veri Hatasi: {e}")
        return []


async def mac_detaylarini_doldur(session, f_data):
    """
    Seçilen potansiyel bir maçın istatistik, olay ve oran verilerini
    ayrı API istekleri ile çeker ve 'mac' objesini doldurur.
    DİKKAT: Bu fonksiyon API limitlerinizi tüketir.
    """
    fixture_id = f_data['fixture']['id']
    ev_takim = f_data['teams']['home']
    dep_takim = f_data['teams']['away']

    # Başlangıç mac objesi
    mac = {
        'id': str(fixture_id),
        'ev': ev_takim['name'],
        'dep': dep_takim['name'],
        'lig': f_data['league']['name'],
        'dakika': int(f_data['fixture']['status']['elapsed'] or 1),
        'ev_gol': int(f_data['goals']['home'] or 0),
        'dep_gol': int(f_data['goals']['away'] or 0),
        'son_gol': 0,
        # Varsayılanlar
        'shots_on_target_ev': 0, 'shots_on_target_dep': 0,
        'possession_ev': 50, 'dangerous_attacks_ev': 0, 'dangerous_attacks_dep': 0,
        'kirmizi_kart': 0, 'corner_ev': 0, 'corner_dep': 0, 'corner_toplam': 0,
        'ah_deger': 0.0
    }

    try:
        # İKİ AYRI İSTEK ATACAK: İstatistikler ve Olaylar
        # (v1.0'daki hata buradaydı, istatistikleri temel istekte arıyorduk)

        # 1. İstatistikler (/fixtures/statistics?fixture={id})
        stats_url = f"{BASE_URL}/fixtures/statistics?fixture={fixture_id}"
        async with session.get(stats_url, headers=API_HEADERS, timeout=10) as stats_resp:
            if stats_resp.status == 200:
                stats_data = await stats_resp.json()
                for stat_group in stats_data.get('response', []):
                    team_id = stat_group.get('team', {}).get('id')
                    is_home = (team_id == ev_takim['id'])
                    for s in stat_group.get('statistics', []):
                        tip = s.get('type', '').lower()
                        val = s.get('value', 0)
                        if val is None: val = 0
                        # Yüzde parse et (%58 → 58)
                        if isinstance(val, str) and '%' in val:
                            try: val = int(val.replace('%', '').strip())
                            except: val = 50
                        else:
                            try: val = int(val)
                            except: val = 0

                        # İstatistikleri ata
                        if 'on target' in tip or 'shots on goal' in tip:
                            if is_home: mac['shots_on_target_ev'] = val
                            else: mac['shots_on_target_dep'] = val
                        elif 'possession' in tip:
                            if is_home: mac['possession_ev'] = val
                        elif 'dangerous attacks' in tip:
                            if is_home: mac['dangerous_attacks_ev'] = val
                            else: mac['dangerous_attacks_dep'] = val
                        elif 'red cards' in tip: mac['kirmizi_kart'] += val
                        elif 'corners' in tip:
                            if is_home: mac['corner_ev'] = val
                            else: mac['corner_dep'] = val
                # Corner Toplamı
                mac['corner_toplam'] = mac['corner_ev'] + mac['corner_dep']

        # 2. Olaylar (Son Gol Dakikası) (/fixtures/events?fixture={id})
        events_url = f"{BASE_URL}/fixtures/events?fixture={fixture_id}"
        async with session.get(events_url, headers=API_HEADERS, timeout=10) as events_resp:
            if events_resp.status == 200:
                events_data = await events_resp.json()
                son_gol_dak = 0
                for event in events_data.get('response', []):
                    if event.get('type') == 'Goal':
                        gdk = int(event.get('time', {}).get('elapsed', 0) or 0)
                        if gdk > son_gol_dak: son_gol_dak = gdk
                mac['son_gol'] = son_gol_dak

        # 3. Oranlar (Asian Handicap) (/odds?fixture={id})
        # (Bu Adım 2'den buraya çekildi, API limitini korumak için)
        # Sadece Adım 1'deki kriterleri geçen maçlara atılabilir, ama biz
        # detay doldurma aşamasında hepsini alıyoruz. Planınızın limitini kontrol edin.
        odds_url = f"{BASE_URL}/odds?fixture={fixture_id}"
        async with session.get(odds_url, headers=API_HEADERS, timeout=10) as odds_resp:
            if odds_resp.status == 200:
                odds_data = await odds_resp.json()
                for bet_group in odds_data.get('response', []):
                    for bet in bet_group.get('bookmakers', []):
                        for bet_item in bet.get('bets', []):
                            bet_name = bet_item.get('name', '').lower()
                            if 'asian handicap' in bet_name and not 'corners' in bet_name and not 'goals' in bet_name:
                                for v in bet_item.get('values', []):
                                    val_str = v.get('value', '').lower()
                                    if 'home' in val_str:
                                        # AH parse: "Home -1.5" → -1.5
                                        for p in val_str.split():
                                            try: mac['ah_deger'] = float(p); break
                                            except: pass

        return mac

    except Exception as e:
        logger.error(f"Mac Detay Doldurma Hatasi ({fixture_id}): {e}")
        return None


# ================================================
# BİLDİRİM VE ANA DÖNGÜ
# ================================================
async def bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, ai_yorum):
    """Sinyal bildirimini Telegram'a istenen formatta gönderir."""

    # Puan'a göre kasa hesapla
    kasa = kasa_hesapla(puan, mac['dakika'], mac['ah_deger'])

    # Emoji ve Karar
    if puan >= 10: karar_emoji = "🔥🔥"; karar = "KESİN GİR"
    elif puan >= 8: karar_emoji = "🔥"; karar = "KESİN GİR"
    elif puan >= 6: karar_emoji = "✅"; karar = "GİREBİLİRSİN"
    else: karar_emoji = "⚠️"; karar = "DİKKATLİ OL"

    sonraki = sonraki_gol_tahmini(mac, strateji)
    neden = f"{tavsiye_uret(mac, strateji)[1]} (Winning Code: {mac['wc']['detay']})"

    # İstatistik Satırı
    ah = mac['ah_deger']
    istat_satirlari = (
        f"⚽ Şut: {mac['shots_on_target_ev']}/{mac['shots_on_target_dep']} "
        f"| 🏃 Top: %{mac['possession_ev']}/%{100-mac['possession_ev']}\n"
        f"💥 Atak: {mac['dangerous_attacks_ev']}/{mac['dangerous_attacks_dep']} "
        f"| 🚩 Corner: {mac['corner_ev']}/{mac['corner_dep']}"
        + (f" | 📈 AH: {ah}" if ah != 0 else "")
    )

    # Detay Listesi (max 4)
    detay_str = "\n".join([f"- {d}" for d in detay[:4]])

    mesaj = (
        f"{karar_emoji} {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"🏆 {mac['lig']} | ⏱️ {mac['dakika']}. DK\n"
        f"────────────────────\n"
        f"📈 SİNYAL PUANI: {puan}/12\n"
        f"🎯 STRATEJİ: {sonraki}\n"
        f"────────────────────\n"
        f"📝 SİSTEM ANALİZİ:\n"
        f"{detay_str}\n"
        f"────────────────────\n"
        f"📊 İSTATİSTİKLER:\n"
        f"{istat_satirlari}\n"
        f"────────────────────\n"
        f"🧠 AI DERİN ANALİZİ:\n"
        f"{ai_yorum if ai_yorum else 'Analiz yapılamadı.'}\n"
        f"────────────────────\n"
        f"💡 TAHMİN: {tahmin}\n"
        f"📌 NEDEN: {neden}\n"
        f"────────────────────\n"
        f"💰 KASA: %{kasa}\n"
        f"{'═'*20}\n"
        f"{karar_emoji} {karar}\n"
        f"{'═'*20}"
    )

    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode='Markdown')
        # DB'ye kaydet
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum or "", kasa)
        logger.info(f"Sinyal Bildirimi Gönderildi: {mac['ev']} vs {mac['dep']} | {puan}p")
    except Exception as e:
        logger.error(f"Bildirim Gönderme Hatasi: {e}")


async def sonuc_bildir(bot, mac_id, ev, dep, tahmin, sonuc, fin_ev, fin_dep):
    """Maçın nihai sonucunu ve tahminin tutup tutmadığını bildirir."""
    emoji = "✅ TUTTU!" if sonuc == "TUTTU" else "❌ DÜŞTÜ!"
    text = (
        f"📊 SONUÇ: {ev} {fin_ev}-{fin_dep} {dep}\n"
        f"{emoji}\n💡 Tahmin: {tahmin}"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode='Markdown')
        # DB'de güncelle
        await sonuc_guncelle(mac_id, sonuc, fin_ev, fin_dep)
    except Exception as e:
        logger.error(f"Sonuc Bildirimi Hatasi: {e}")


# ================================================
# ANA DÖNGÜ (LOOP)
# ================================================
async def ana_dongu():
    """Botun ana döngüsü. Maçları tarar, analiz eder ve bildirir."""
    # Bot ve DB Baglantisi
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglanti_kur()

    # Başlangıç Bildirimi
    try:
        simdi = datetime.now()
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "🤖 **MAÇ ANALİZ BOTU — AKTİF v2.0**\n\n"
                "Sistem yenilendi, API istatistik v3 entegre edildi.\n"
                "Sinyaller geldikçe bildirim atacağım 🚀\n"
                f"Zaman: {simdi.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        )
        logger.info("Bot basladi!")
    except Exception as e: logger.error(f"Baslangic Bildirimi Hatasi: {e}")

    uyku_bildirimi = False
    son_haftalik_rapor = None

    while True:
        try:
            # 1. Hafta içi/sonu aktiflik kontrolü
            if not aktif_mi():
                if not uyku_bildirimi:
                    await bot.send_message(chat_id=CHAT_ID, text=f"😴 UYKU MODU\n\nAPI hakkı korunuyor.\n⏰ {sonraki_aktif()}")
                    uyku_bildirimi = True
                await asyncio.sleep(1800) # 30 dk uyu
                continue
            elif uyku_bildirimi:
                await bot.send_message(chat_id=CHAT_ID, text="⚡ UYANDIM! Maç taraması başlıyor...")
                uyku_bildirimi = False

            # 2. Haftalık Rapor (Pzt 10:00)
            simdi = datetime.now()
            if simdi.weekday() == 0 and simdi.hour == 10 and son_haftalik_rapor != simdi.date():
                await haftalik_rapor(bot)
                son_haftalik_rapor = simdi.date()

            async with aiohttp.ClientSession() as session:
                # ADIM 1: Temel maç listesini çek (1 API isteği)
                temel_fixtures = await maclari_temel_cek(session)
                aktif_mac_idler = [str(f['fixture']['id']) for f in temel_fixtures]

                logger.info(f"Döngü Başladi. {len(temel_fixtures)} canlı maç taranıyor...")

                # Biten maçların sonucunu bildir
                for mac_id, bilgi in list(biten_maclar.items()):
                    if mac_id not in aktif_mac_idler:
                        # Maç API'da görünmüyor = Bitti. Sonucunu kontrol et.
                        # (Burada son skoru veritabanından veya son döngüden çekmeniz gerekir,
                        #  basit olması için son_ev/son_dep'i bir önceki döngüde güncellediğimizi varsayıyoruz)
                        sonuc = await sonuc_kontrol(bilgi['tahmin'], bilgi['bas_ev'], bilgi['bas_dep'], bilgi['son_ev'], bilgi['son_dep'])
                        await sonuc_bildir(bot, mac_id, bilgi['ev'], bilgi['dep'], bilgi['tahmin'], sonuc, bilgi['son_ev'], bilgi['son_dep'])
                        del biten_maclar[mac_id]

                # ADIM 2: Potansiyel Maçları Filtrele ve Detay Çek
                adaylar = []
                # API Limitini korumak için max 15 maç işleyin (en aktif olanlar)
                for f_data in temel_fixtures[:15]:
                    dakika = int(f_data['fixture']['status']['elapsed'] or 1)
                    # Sadece belirli dakika aralığında maçları detaylı analiz et
                    if 15 <= dakika <= 85:
                        # Detayları çek (Her maç için 3 API isteği! Plan limitinizi tüketecektir.)
                        mac = await mac_detaylarini_doldur(session, f_data)
                        if mac: adaylar.append(mac)
                        # API limitini aşmamak için maçlar arası es
                        await asyncio.sleep(0.5)

                # ADIM 3: Analiz, Puanlama ve Gemini AI
                for mac in adaylar:
                    # Biten maç takibi için güncelle
                    biten_maclar[mac['id']] = {
                        'ev': mac['ev'], 'dep': mac['dep'],
                        # Başlangıçta 0-0 veya o anki skoru al (sinyal anı skoru)
                        # Biten maç takibinde bu skoru 'bas_ev/dep' olarak kullanacağız.
                    }

                    # Sinyal Puanla
                    puan, detay, strateji, wc = sinyal_hesapla(mac)
                    mac['wc'] = wc # Winning code detayını mac objesine ekle

                    # Potansiyel sinyal
                    if puan >= MIN_PUAN:
                        # Daha önce bildirim gönderildi mi, puanı daha mı yüksek?
                        onceki_puan = bildirim_gonderilen.get(mac['id'], {}).get('puan', 0)
                        if puan > onceki_puan:
                            # Cooling Off Kontrolü (Son gol ve tempo)
                            cooling, c_msg = cooling_off_kontrol(mac)
                            if cooling:
                                logger.info(f"Cooling Off: {mac['ev']} - {c_msg}")
                                continue

                            # Gemini AI Analizi
                            tahmin_uretildi = tavsiye_uret(mac, strateji)
                            ai_yorum, gir_onay = await gemini_analiz(session, mac, puan, strateji, tahmin_uretildi[0], tahmin_uretildi[1], wc)

                            # Bildirim Gönder
                            if gir_onay:
                                # Biten maç sonucuna baz olacak skoru sinyal anı skoru olarak kaydet
                                biten_maclar[mac['id']]['tahmin'] = tahmin_uretildi[0]
                                biten_maclar[mac['id']]['bas_ev'] = mac['ev_gol']
                                biten_maclar[mac['id']]['bas_dep'] = mac['dep_gol']
                                # Sürekli güncellenen skor
                                biten_maclar[mac['id']]['son_ev'] = mac['ev_gol']
                                biten_maclar[mac['id']]['son_dep'] = mac['dep_gol']

                                await bildirim_gonder(bot, mac, puan, detay, strateji, tahmin_uretildi[0], ai_yorum)
                                # Bildirimi kaydet (tekrar göndermemek için)
                                bildirim_gonderilen[mac['id']] = {'puan': puan}

                    # Maç bitti mi kontrolü için her döngüde skoru güncelle
                    if mac['id'] in biten_maclar:
                        biten_maclar[mac['id']]['son_ev'] = mac['ev_gol']
                        biten_maclar[mac['id']]['son_dep'] = mac['dep_gol']

        except Exception as e:
            logger.error(f"Ana Döngü Kritik Hatasi: {e}")

        # Her döngü sonu bekleme (Örn: 7 dk)
        logger.info("Döngü Bitti. Bekleniyor...")
        await asyncio.sleep(420) # 7 dk

# ================================================
# BAŞLANGIÇ NOKTASI
# ================================================
if __name__ == "__main__":
    logger.info("BOT STARTED")
    try:
        # Kodun hata vermemesi için gerekli ortam değişkenlerini kontrol et
        if not all([TELEGRAM_TOKEN, CHAT_ID, APISPORTS_KEY, DATABASE_URL, GEMINI_KEY]):
            logger.error("HATA: Ortam değişkenleri eksik. Lütfen yapılandırmayı kontrol edin.")
            exit(1)
        asyncio.run(ana_dongu())
    except KeyboardInterrupt:
        logger.info("Bot KeyboardInterrupt ile kapatildi.")
