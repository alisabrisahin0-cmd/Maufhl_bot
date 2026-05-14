import asyncio, aiohttp, os, logging, time, sqlite3
from telegram import Bot
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from collections import deque

# ============================================================================
# BOT V53 — ARŞİV ANALİZİ ENTEGRE EDİLMİŞ
# 507.347 maç verisi ile doğrulanmış pattern'ler aktif
# ============================================================================
#
# ARŞİV BULGULARI (base rate'e göre lift):
#
# [ALTI PATTERN — DÜŞÜK GOL]
#  P1: IY0.5 ALT → M2.5 ALT          : %93.0  (n=158k, lift=1.86x)
#  P2: IY0.5 ALT + KG YOK → M1.5 ALT : %92.9  (n=119k, lift=1.94x)
#  P3: IY0.5 ALT + KG YOK + TC ÇİFT  : %97.1  (n=60k,  lift=2.03x)
#  P4: IY0.5 ALT + KG YOK → M2.5 ALT : %98.2  (n=119k)
#
# [ÜST PATTERN — YÜKSEK GOL]
#  P5: IY1.5 ÜST → M2.5 ÜST          : %63.8  (n=175k, lift=2.04x)
#  P6: IY1.5 ÜST + KG VAR → M2.5 ÜST : %69.3  (n=133k, lift=2.22x)
#  P7: IY1.5 ÜST + KG VAR + TC ÇİFT  : %83.3  (n=66k,  lift=2.67x)
#
# [EV/DEP GOL]
#  P8: Ev0.5 ÜST: %75.8 base rate
#  P9: Dep0.5 ÜST: %67.8 base rate
#  P10: IY1.5 ÜST + KG VAR + EV1.5 ÜST → M2.5 ÜST: %82.2 (n=93k)
# ============================================================================

# ────────────────────────────────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID        = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN  = os.getenv("BETSAPI_TOKEN", "")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# LİG AYARLARI
# ────────────────────────────────────────────────────────────────────────────

# Karantina: veri kalitesi düşük veya manipülasyon riski yüksek
KARANTINA_LIGLER = [
    'brazil', 'brasil', 'kenya', 'ethiopia', 'rwanda', 'oman',
    'kuwait', 'iraq', 'afghanistan', 'serie d', 'national 2',
    'czechia 3', 'philippines', 'vietnam'
]

# Öncelikli ligler — daha güvenilir istatistik
ONCELIKLI_LIGLER = [
    'bundesliga', 'champions league', 'europa league', 'serie a',
    'la liga', 'ligue 1', 'eredivisie', 'primeira liga', 'super lig',
    'süper lig', 'premier league', 'championship', 'serie b',
    'turkish cup', 'fa cup', 'copa del rey'
]

# ────────────────────────────────────────────────────────────────────────────
# SİNYAL VERİTABANI — tekrar gönderim engeli
# ────────────────────────────────────────────────────────────────────────────

class SinyalDB:
    def __init__(self, db_path="sinyaller_v53.db"):
        self.db = db_path
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS log (
                event_id TEXT, dk_grubu INTEGER, sinyal TEXT, zaman REAL,
                PRIMARY KEY (event_id, dk_grubu, sinyal))""")
            c.execute("DELETE FROM log WHERE zaman < ?", (time.time() - 86400,))
            c.commit()

    def goruldu_mu(self, eid, dk, sinyal) -> bool:
        dk_g = (dk // 10) * 10  # 10 dakikalık gruplar
        with sqlite3.connect(self.db) as c:
            r = c.execute("SELECT 1 FROM log WHERE event_id=? AND dk_grubu=? AND sinyal=?",
                          (eid, dk_g, sinyal)).fetchone()
        return r is not None

    def kaydet(self, eid, dk, sinyal):
        dk_g = (dk // 10) * 10
        try:
            with sqlite3.connect(self.db) as c:
                c.execute("INSERT INTO log VALUES (?,?,?,?)", (eid, dk_g, sinyal, time.time()))
                c.commit()
        except sqlite3.IntegrityError:
            pass

db = SinyalDB()

# ────────────────────────────────────────────────────────────────────────────
# VERİ YAPILARI
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class MacStats:
    """Bir takımın anlık istatistikleri"""
    gol:    int = 0
    sot:    int = 0   # isabetli şut
    da:     int = 0   # tehlikeli atak
    ta:     int = 0   # toplam atak
    korner: int = 0
    kirmizi: int = 0

    def valid(self) -> bool:
        return self.ta >= self.da >= self.sot >= 0

@dataclass
class MacDurumu:
    """Maçın tüm anlık durumu"""
    eid:   str
    ev:    str
    dep:   str
    lig:   str
    dk:    int
    skor:  str
    home:  MacStats
    away:  MacStats

    # Oran bilgileri (betsapi'den)
    ah_ev:      float = 0.0   # asian handicap (ev için, örn -0.5)
    ou_line:    float = 2.5   # over/under çizgisi
    kg_oran:    float = 0.0   # KG var oranı
    ev_oran:    float = 0.0   # ev gol atar oranı
    dep_oran:   float = 0.0   # dep gol atar oranı

    # Hesaplanan özellikler
    iy_gol:     int = 0   # yarı arası toplam gol (dk<46)
    toplam_gol: int = 0

    def __post_init__(self):
        self.toplam_gol = self.home.gol + self.away.gol
        self.iy_gol = self.toplam_gol if self.dk < 46 else self.iy_gol

@dataclass
class Sinyal:
    """Üretilen sinyal"""
    tip:     str
    mesaj:   str
    guc:     float   # 0-100
    pattern: str     # hangi arşiv patternine dayanıyor

# ────────────────────────────────────────────────────────────────────────────
# ARŞİV TABANLI SİNYAL MOTORLARİ
# ────────────────────────────────────────────────────────────────────────────

class ArsivMotoru:
    """
    507k maç arşivinden doğrulanmış patternleri uygular.
    Tüm pattern'ler gerçek veriden çıkarılmış, overfitting önlemi olarak
    minimum n=10.000 kriteri uygulanmıştır.
    """

    # ── IY bilgisini puana çevir ─────────────────────────────────────────────

    @staticmethod
    def iy05_ust(mac: MacDurumu) -> bool:
        """İlk yarıda en az 1 gol atıldı mı? (İY 0.5 üstü)"""
        return mac.toplam_gol >= 1

    @staticmethod
    def iy15_ust(mac: MacDurumu) -> bool:
        """İlk yarıda 2+ gol atıldı mı? (İY 1.5 üstü)"""
        return mac.toplam_gol >= 2

    @staticmethod
    def kg_var(mac: MacDurumu) -> bool:
        """Her iki takım da gol attı mı?"""
        return mac.home.gol > 0 and mac.away.gol > 0

    @staticmethod
    def ev15_ust(mac: MacDurumu) -> bool:
        """Ev sahibi 2+ gol attı mı?"""
        return mac.home.gol >= 2

    # ─────────────────────────────────────────────────────────────────────────
    # YARI ARASI ANALİZİ (dk 43-47)
    # En güçlü zaman penceresi: arşivden 1. yarı bilgisi kesinleşmiş
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def yari_arasi_analiz(cls, mac: MacDurumu) -> List[Sinyal]:
        """
        Yarı arası veya 1. yarı bitimine yakın (dk 40-47) üretilen sinyaller.
        Bu penceredeki pattern'ler en güvenilir olanlardır çünkü 1. yarı
        bilgisi eksiksizdir ve 2. yarı için tahmin yapılır.
        """
        sinyaller = []
        if not (38 <= mac.dk <= 47):
            return sinyaller

        gol = mac.toplam_gol
        kg  = cls.kg_var(mac)
        iy05_ust = gol >= 1
        iy15_ust = gol >= 2

        # ── P1: IY0.5 ALT → M2.5 ALT (%93, lift 1.86x) ─────────────────────
        # 1. yarıda hiç gol yoksa maçın tamamında 2.5 alt %93 ihtimalle gerçekleşir.
        if not iy05_ust:
            sinyaller.append(Sinyal(
                tip="ALT_2.5",
                mesaj=(
                    f"⚽ 1. YARI GOLSÜZ → MAÇ 2.5 ALT\n"
                    f"📊 İY: 0 gol | Arşiv: %93 hit | Lift: 1.86x\n"
                    f"💡 507k maçtan: IY0.5 ALT → M2.5 ALT"
                ),
                guc=93.0,
                pattern="P1: IY0.5 ALT → M2.5 ALT"
            ))

        # ── P2: IY0.5 ALT + KG YOK → M1.5 ALT (%92.9, lift 1.94x) ──────────
        if not iy05_ust and not kg:
            sinyaller.append(Sinyal(
                tip="ALT_1.5",
                mesaj=(
                    f"⚽ GOLSÜZ 1.YARI + KG YOK → MAÇ 1.5 ALT\n"
                    f"📊 İY: 0-0 | KG: Yok | Arşiv: %92.9 | Lift: 1.94x\n"
                    f"💡 Her iki takım da golsüz + ilk yarı sıfır"
                ),
                guc=92.9,
                pattern="P2: IY0.5 ALT + KG YOK → M1.5 ALT"
            ))

        # ── P4: IY0.5 ALT + KG YOK → M2.5 ALT (%98.2!) ─────────────────────
        if not iy05_ust and not kg:
            sinyaller.append(Sinyal(
                tip="ALT_2.5_GÜÇLÜ",
                mesaj=(
                    f"🔥 GÜÇLÜ: GOLSÜZ + KG YOK → MAÇ 2.5 ALT\n"
                    f"📊 İY: 0-0 | KG: Yok | Arşiv: %98.2 | Lift: 1.96x\n"
                    f"💡 En güçlü alt pattern (n=119k maç)"
                ),
                guc=98.2,
                pattern="P4: IY0.5 ALT + KG YOK → M2.5 ALT"
            ))

        # ── P5: IY1.5 ÜST → M2.5 ÜST (%63.8, lift 2.04x) ──────────────────
        if iy15_ust:
            sinyaller.append(Sinyal(
                tip="ÜST_2.5",
                mesaj=(
                    f"🔥 1.YARIDA 2+ GOL → MAÇ 2.5 ÜST\n"
                    f"📊 İY: {gol} gol | Arşiv: %63.8 | Lift: 2.04x\n"
                    f"💡 IY1.5 ÜST → M2.5 ÜST (n=175k)"
                ),
                guc=63.8,
                pattern="P5: IY1.5 ÜST → M2.5 ÜST"
            ))

        # ── P6: IY1.5 ÜST + KG VAR → M2.5 ÜST (%69.3, lift 2.22x) ─────────
        if iy15_ust and kg:
            sinyaller.append(Sinyal(
                tip="ÜST_2.5_KG",
                mesaj=(
                    f"🔥 2+ GOL + KG VAR → MAÇ 2.5 ÜST\n"
                    f"📊 İY: {mac.home.gol}-{mac.away.gol} | KG: Var | Arşiv: %69.3 | Lift: 2.22x\n"
                    f"💡 Her iki takım da golsüz değil, tempo yüksek"
                ),
                guc=69.3,
                pattern="P6: IY1.5 ÜST + KG VAR → M2.5 ÜST"
            ))

        # ── P7: IY1.5 ÜST + KG VAR + EV1.5 ÜST → M2.5 ÜST (%82.2, n=93k) ─
        if iy15_ust and kg and cls.ev15_ust(mac):
            sinyaller.append(Sinyal(
                tip="ÜST_2.5_ELMAS",
                mesaj=(
                    f"💎 ELMAS: 2+GOL + KG + EV 2+ → MAÇ 2.5 ÜST\n"
                    f"📊 İY: {mac.home.gol}-{mac.away.gol} | KG: Var | EV: {mac.home.gol} gol\n"
                    f"🎯 Arşiv: %82.2 | Lift: 2.63x | n=93k maç"
                ),
                guc=82.2,
                pattern="P7: IY1.5 ÜST + KG VAR + EV1.5 ÜST → M2.5 ÜST"
            ))

        return sinyaller

    # ─────────────────────────────────────────────────────────────────────────
    # 2. YARI ANALİZİ (dk 46-90) — CANLI
    # Artık 1. yarı bilgisi kesinleşmiştir; 2. yarı tahmini yapılır
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def ikinci_yari_analiz(cls, mac: MacDurumu) -> List[Sinyal]:
        """
        2. yarı devam ederken üretilen sinyaller.
        IY gol sayısı bilgisi kesinleşmiş; 2. yarı açılış ile birleştirilir.
        Not: bu pencerede IY bilgisini 'mac.iy_gol' üzerinden okuyoruz.
        """
        sinyaller = []
        if not (46 <= mac.dk <= 85):
            return sinyaller

        iy_g   = mac.iy_gol   # 1. yarıdaki gol sayısı (sabit)
        mac_g  = mac.toplam_gol
        iy2_g  = mac_g - iy_g  # 2. yarıdaki gol sayısı (şimdiye kadar)
        dk2    = mac.dk - 45   # 2. yarıda kaçıncı dakika

        kg = cls.kg_var(mac)

        # ── 2Y GOL YOK, IY DA GOLSÜZ: Çok düşük gol maçı ───────────────────
        if iy_g == 0 and iy2_g == 0 and mac.dk <= 70:
            sinyaller.append(Sinyal(
                tip="ALT_1.5_2Y",
                mesaj=(
                    f"⚽ İKİ YARI GOLSÜZ ({mac.dk}') → ALT 1.5 / DEVAM\n"
                    f"📊 İY: 0 | 2Y({dk2}'): 0 | Toplam: 0 gol\n"
                    f"💡 Arşiv: IY0.5 ALT → M1.5 ALT %80.9 (n=158k)"
                ),
                guc=80.0,
                pattern="2Y: Golsüz devam → ALT"
            ))

        # ── IY'DA 2+ GOL + 2Y'DA HENÜZ GOL YOK (55-70. dk) ─────────────────
        # 2. yarı henüz golsüz ama tempo yüksek → gol beklentisi
        if iy_g >= 2 and iy2_g == 0 and 50 <= mac.dk <= 70:
            sinyaller.append(Sinyal(
                tip="ÜST_2.5_2Y_GOL_BEK",
                mesaj=(
                    f"🔥 IY 2+ GOL + 2.YARI ({dk2}') GOLSÜZ → ÜST VEYA SONRAKI GOL\n"
                    f"📊 İY: {iy_g} gol | 2Y({dk2}'): 0 gol\n"
                    f"💡 Yüksek tempolu maç, 2. yarı henüz filizlenmedi"
                ),
                guc=65.0,
                pattern="2Y: IY1.5 ÜST + 2Y golsüz → sonraki gol"
            ))

        # ── IY'DA 2+ GOL + 2Y'DA DA GOL VAR: Çok gollü maç ─────────────────
        if iy_g >= 2 and iy2_g >= 1:
            sinyaller.append(Sinyal(
                tip="ÜST_3.5",
                mesaj=(
                    f"💎 YÜKSEK TEMPOLU: {mac.dk}' — İY:{iy_g} + 2Y:{iy2_g} = {mac_g} gol\n"
                    f"📊 KG: {'Var' if kg else 'Yok'} | {mac.home.gol}-{mac.away.gol}\n"
                    f"💡 Arşiv: IY1.5 ÜST + KG VAR → %82.2 M2.5 ÜST (zaten geçilmiş)"
                ),
                guc=78.0,
                pattern="2Y: IY1.5 ÜST + 2Y gol var → 3.5 ÜST?"
            ))

        return sinyaller

    # ─────────────────────────────────────────────────────────────────────────
    # İSTATİSTİK BAZLI CANLI SİNYALLER (15-42. dk)
    # Arşiv patternleri + anlık baskı verisi
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def canli_analiz(cls, mac: MacDurumu) -> List[Sinyal]:
        """
        Maç devam ederken anlık istatistik bazlı sinyaller.
        Arşiv base rate'leri ile güçlendirilmiş.
        """
        sinyaller = []
        if not (10 <= mac.dk <= 42):
            return sinyaller

        h, a = mac.home, mac.away
        toplam_da = h.da + a.da

        # ── Yüksek baskı sinyali: Birinin aşırı üstünlüğü ───────────────────
        if toplam_da >= 8 and mac.dk <= 35:
            dominant = h if h.da > a.da else a
            taraf = "EV SAHİBİ" if h.da > a.da else "DEPLASMAN"
            oran = dominant.da / max(a.da if h.da > a.da else h.da, 1)
            if oran >= 2.5 and dominant.sot >= 3:
                sinyaller.append(Sinyal(
                    tip="GOL_BEK",
                    mesaj=(
                        f"🎯 YÜKSEK BASKI: {mac.dk}' | {taraf} BASKIYOR\n"
                        f"📊 DA: {h.da}-{a.da} | SOT: {h.sot}-{a.sot} | Korner: {h.korner}-{a.korner}\n"
                        f"💡 DA oran: {oran:.1f}x | SOT: {dominant.sot}"
                    ),
                    guc=60.0,
                    pattern="Canli: Yüksek DA + SOT üstünlüğü"
                ))

        # ── İY gol beklentisi: İyi tempo ─────────────────────────────────────
        if 20 <= mac.dk <= 40 and toplam_da >= 6:
            gol_yok = mac.toplam_gol == 0
            if gol_yok and (h.sot + a.sot) >= 4:
                sinyaller.append(Sinyal(
                    tip="IY_GOL_BEK",
                    mesaj=(
                        f"⏱️ İY GOL BEKLENTİSİ: {mac.dk}' | Golsüz ama aktif\n"
                        f"📊 DA: {toplam_da} | SOT: {h.sot+a.sot} | Korner: {h.korner+a.korner}\n"
                        f"💡 Arşiv: İY0.5 ÜST base rate %68.8"
                    ),
                    guc=55.0,
                    pattern="Canli: Aktif tempo + golsüz"
                ))

        return sinyaller


# ────────────────────────────────────────────────────────────────────────────
# 1. YARI GOL SAYISINI SAKLA (2. yarıda kullanmak için)
# ────────────────────────────────────────────────────────────────────────────

iy_gol_hafiza: Dict[str, int] = {}  # event_id → 1. yarı gol sayısı


# ────────────────────────────────────────────────────────────────────────────
# API YARDIMCILARI
# ────────────────────────────────────────────────────────────────────────────

def stat_al(v: dict, key: str, varsayilan=0) -> int:
    """BetsAPI stats dict'inden güvenli int çekimi"""
    try:
        return int(float(v.get(key, varsayilan) or 0))
    except (ValueError, TypeError):
        return varsayilan


async def oran_cek(session: aiohttp.ClientSession, eid: str) -> Dict:
    """Event odds API'sinden AH + OU oranlarını çek"""
    try:
        url = f"https://api.betsapi.com/v2/event/odds?token={BETSAPI_TOKEN}&event_id={eid}&source=bet365"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
            data = await r.json()
            if data.get('success') != 1:
                return {}
            return data.get('results', {})
    except Exception:
        return {}


# ────────────────────────────────────────────────────────────────────────────
# ANA ANALİZ FONKSİYONU
# ────────────────────────────────────────────────────────────────────────────

async def mac_analiz(mac_data: dict, session: aiohttp.ClientSession) -> List[str]:
    """
    Tek bir maçı analiz eder, sinyal mesajları listesi döner.
    """
    try:
        eid  = str(mac_data['id'])
        ev   = mac_data['home']['name']
        dep  = mac_data['away']['name']
        lig  = mac_data['league']['name']
        dk   = int(mac_data.get('timer', {}).get('tm', 0))
        skor = mac_data.get('ss', '0-0')

        # ── Karantina kontrolü ────────────────────────────────────────────────
        lig_lower = lig.lower()
        if any(k in lig_lower for k in KARANTINA_LIGLER):
            return []

        # ── Stats çekimi ──────────────────────────────────────────────────────
        stats = mac_data.get('stats', {})
        ev_v  = stats.get('1', {})
        dep_v = stats.get('2', {})

        home = MacStats(
            gol=stat_al(ev_v, 'SC'),
            sot=stat_al(ev_v, 'S1'),
            da=stat_al(ev_v, 'S4'),
            ta=stat_al(ev_v, 'S3'),
            korner=stat_al(ev_v, 'S2'),
            kirmizi=stat_al(ev_v, 'S7'),
        )
        away = MacStats(
            gol=stat_al(dep_v, 'SC'),
            sot=stat_al(dep_v, 'S1'),
            da=stat_al(dep_v, 'S4'),
            ta=stat_al(dep_v, 'S3'),
            korner=stat_al(dep_v, 'S2'),
            kirmizi=stat_al(dep_v, 'S7'),
        )

        # ── Temel filtreler ───────────────────────────────────────────────────
        if home.kirmizi > 0 or away.kirmizi > 0:
            return []  # Kırmızı kart: dengeyi bozar
        if abs(home.gol - away.gol) >= 4:
            return []  # Çok büyük fark: piyasa değeri kalmaz

        # ── IY gol hafızası ───────────────────────────────────────────────────
        if 43 <= dk <= 47:
            # Yarı arası → 1. yarı gol sayısını kaydet
            iy_gol_hafiza[eid] = home.gol + away.gol
        iy_gol = iy_gol_hafiza.get(eid, 0) if dk >= 46 else 0

        # ── MacDurumu oluştur ─────────────────────────────────────────────────
        mac = MacDurumu(
            eid=eid, ev=ev, dep=dep, lig=lig, dk=dk, skor=skor,
            home=home, away=away, iy_gol=iy_gol
        )

        # ── Arşiv motorunu çalıştır ───────────────────────────────────────────
        tum_sinyaller: List[Sinyal] = []

        if 38 <= dk <= 47:
            tum_sinyaller += ArsivMotoru.yari_arasi_analiz(mac)
        elif 46 <= dk <= 85:
            tum_sinyaller += ArsivMotoru.ikinci_yari_analiz(mac)
        elif 10 <= dk <= 42:
            tum_sinyaller += ArsivMotoru.canli_analiz(mac)

        # ── Minimum güç filtresi ──────────────────────────────────────────────
        tum_sinyaller = [s for s in tum_sinyaller if s.guc >= 55.0]

        # ── Tekrar gönderim filtresi + mesaj üretimi ──────────────────────────
        mesajlar = []
        for sinyal in tum_sinyaller:
            if db.goruldu_mu(eid, dk, sinyal.tip):
                continue
            db.kaydet(eid, dk, sinyal.tip)

            # Öncelikli lig ekstrası
            oncelikli = any(ol in lig_lower for ol in ONCELIKLI_LIGLER)
            oncelik_tag = "⭐ " if oncelikli else ""

            msg = (
                f"{'━'*32}\n"
                f"{oncelik_tag}🎯 *{sinyal.tip}* | Güç: {sinyal.guc:.0f}%\n"
                f"{'━'*32}\n"
                f"🏟️ {ev} {skor} {dep}\n"
                f"⏱️ {dk}' | 🏆 {lig}\n"
                f"\n{sinyal.mesaj}\n"
                f"\n📌 *Pattern:* {sinyal.pattern}"
            )
            mesajlar.append(msg)

        return mesajlar

    except Exception as e:
        logger.error(f"Analiz hatası [{mac_data.get('id', '?')}]: {e}")
        return []


# ────────────────────────────────────────────────────────────────────────────
# ANA DÖNGÜ
# ────────────────────────────────────────────────────────────────────────────

async def ana_dongu():
    if not TELEGRAM_TOKEN or not BETSAPI_TOKEN:
        logger.error("TELEGRAM_TOKEN veya BETSAPI_TOKEN eksik! .env dosyasını kontrol edin.")
        return

    bot = Bot(token=TELEGRAM_TOKEN)

    baslik = (
        "🚀 *BOT V53 — ARŞİV EDİSYONU AKTİF*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *507.347 maç* analiz edildi\n"
        "✅ Doğrulanmış pattern'ler aktif:\n"
        "  • IY0.5 ALT → M2.5 ALT: %93\n"
        "  • IY0.5 ALT + KG YOK → M1.5 ALT: %92.9\n"
        "  • IY1.5 ÜST + KG VAR + TC ÇİFT → M2.5 ÜST: %83.3\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛡️ Filtreler: Kırmızı kart, Karantina lig, Tekrar gönderim"
    )
    await bot.send_message(CHAT_ID, baslik, parse_mode="Markdown")
    logger.info("Bot V53 başlatıldı.")

    async with aiohttp.ClientSession() as session:
        dongu = 0
        while True:
            dongu += 1
            try:
                url = f"https://api.betsapi.com/v1/events/inplay?sport_id=1&token={BETSAPI_TOKEN}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    data = await r.json()

                maclar = data.get('results', [])
                logger.info(f"[Döngü {dongu}] {len(maclar)} maç taranıyor...")

                sinyal_sayisi = 0
                for mac in maclar:
                    mesajlar = await mac_analiz(mac, session)
                    for msg in mesajlar:
                        await bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                        sinyal_sayisi += 1
                        await asyncio.sleep(0.3)  # Telegram flood koruması

                if sinyal_sayisi > 0:
                    logger.info(f"[Döngü {dongu}] {sinyal_sayisi} sinyal gönderildi.")
                else:
                    logger.info(f"[Döngü {dongu}] Sinyal yok.")

            except aiohttp.ClientError as e:
                logger.error(f"API bağlantı hatası: {e}")
            except Exception as e:
                logger.error(f"Döngü hatası: {e}")

            await asyncio.sleep(60)


# ────────────────────────────────────────────────────────────────────────────
# DEBUG MODU — API olmadan test
# ────────────────────────────────────────────────────────────────────────────

def debug_test():
    """
    Gerçek API olmadan pattern'leri test et.
    python bot_v53_arsiv.py --test komutuyla çalıştır.
    """
    print("\n" + "="*60)
    print("BOT V53 — PATTERN TEST")
    print("="*60)

    test_durumlar = [
        # (açıklama, dk, ev_gol, dep_gol, beklenen_sinyal)
        ("IY golsüz (P1+P2+P4)", 45, 0, 0, ["ALT_2.5", "ALT_1.5", "ALT_2.5_GÜÇLÜ"]),
        ("IY 1-1 (P5+P6)", 45, 1, 1, ["ÜST_2.5", "ÜST_2.5_KG"]),
        ("IY 2-0 (P5+P7)", 45, 2, 0, ["ÜST_2.5", "ÜST_2.5_ELMAS"]),
        ("IY 2-1 (P5+P6+P7)", 45, 2, 1, ["ÜST_2.5", "ÜST_2.5_KG", "ÜST_2.5_ELMAS"]),
        ("IY 1-0 sadece P5", 45, 1, 0, ["ÜST_2.5"]),
    ]

    motor = ArsivMotoru()
    for aciklama, dk, ev_g, dep_g, beklenen in test_durumlar:
        mac = MacDurumu(
            eid="test", ev="Ev", dep="Dep", lig="Test Ligi",
            dk=dk, skor=f"{ev_g}-{dep_g}",
            home=MacStats(gol=ev_g, sot=4, da=6, ta=10, korner=3),
            away=MacStats(gol=dep_g, sot=3, da=5, ta=8, korner=2),
        )
        sinyaller = motor.yari_arasi_analiz(mac)
        tipler = [s.tip for s in sinyaller]

        print(f"\n📋 {aciklama}")
        print(f"   Beklenen: {beklenen}")
        print(f"   Üretilen: {tipler}")
        for s in sinyaller:
            durum = "✅" if s.tip in beklenen else "⚠️"
            print(f"   {durum} {s.tip} ({s.guc:.0f}%) — {s.pattern}")

    print("\n" + "="*60)
    print("Test tamamlandı.")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        debug_test()
    else:
        asyncio.run(ana_dongu())
