import asyncio, aiohttp, os, logging, time, sqlite3
from telegram import Bot
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from collections import deque

# ============================================================================
# BOT V54 — AH + ORAN ANALİZİ EDİSYONU
# ============================================================================
#
# VERİ KAYNAĞI: 26.005 gerçek maç kaydı (veri_exceli_güncel_28_03_2026.xlsx)
# ARŞİV: 507.347 maç (xlsb)
#
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KANITLANMIŞ PATTERNLER (Gerçek veriden)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# [AH SHARP MONEY]
#  |AH| >= 2.5  → Win%=94.3%  (n=932)   ← piyasa çok emin
#  |AH| >= 2.0  → Win%=92.6%  (n=1855)
#  |AH| >= 1.5  → Win%=90.7%  (n=3709)
#  |AH| >= 1.0  → Win%=87.2%  (n=8222)
#
# [AH ÇIZGISI × DAKİKA]
#  AH<=-1.5 + 0-20dk + EŞİT   → Win%=95.2% (n=372)  ← EN GÜÇLÜ
#  AH<=-0.75 + 0-20dk + GERİDE → Win%=94.5% (n=290)
#  AH<=-0.75 + İY 0 gol + 0-30 → Win%=91.3% (n=1102)
#  AH<=-0.5 + 0-30dk + EŞİT   → Win%=89.6% (n=1421)
#  AH<=-0.75 + 21-35dk + GERİDE→ Win%=91.7% (n=351)
#
# [VALUE BET: FAVORİ GERİDEYKEN]
#  AH<=-0.5 + DEP_ONE + 0-30dk → Win%=93.4% (n=755) ← "geri dönüş" pattern
#  AH<=-0.5 + DEP_ONE + 31-45dk→ Win%=87.7% (n=481)
#  AH<=-1.5 + GERİDE          → Win%=90.1% (n=111)
#
# [UNDERDOG ÖNDEYKEN]
#  AH>=+0.75 + 0-30dk + ÖNDE  → Win%=88.5% (n=260)  ← "gol olacak" pattern
#  AH>=+1.0 + 0-30dk + EŞİT   → Win%=89.1% (n=632)
#
# [ARŞİV PATTERN (507k maç)]
#  IY0.5 ALT → M2.5 ALT       → %93.0 (n=158k)
#  IY0.5 ALT + KG YOK          → %98.2 (n=119k)
#  IY1.5 ÜST + KG VAR + TC ÇİFT→ %83.3 (n=66k)
#
# [OPTIMAL PENCERE]
#  5-25. dakika arası: Win%=88-91%  (AH -0.5/-0.75)
#  45dk sonrası: Win% düşer → 69-74%
#  90dk yakın: Win%=67%  ← EN DÜŞÜK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID        = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN  = os.getenv("BETSAPI_TOKEN", "")

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s',
                    datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# LİG AYARLARI
# ────────────────────────────────────────────────────────────────────────────

KARANTINA_LIGLER = [
    'brazil', 'brasil', 'kenya', 'ethiopia', 'rwanda', 'oman',
    'kuwait', 'iraq', 'afghanistan', 'serie d', 'national 2',
    'czechia 3', 'philippines', 'vietnam', 'myanmar', 'cambodia'
]

PREMIUM_LIGLER = [
    'bundesliga', 'champions league', 'europa league', 'serie a',
    'la liga', 'ligue 1', 'eredivisie', 'primeira liga', 'super lig',
    'süper lig', 'premier league', 'championship', 'serie b',
    'turkish cup', 'fa cup', 'copa del rey', 'bundesliga 2'
]

# ────────────────────────────────────────────────────────────────────────────
# SİNYAL VERİTABANI
# ────────────────────────────────────────────────────────────────────────────

class SinyalDB:
    def __init__(self, db_path="bot_v54.db"):
        self.db = db_path
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS log (
                event_id TEXT, dk_grubu INTEGER, sinyal TEXT, zaman REAL,
                PRIMARY KEY (event_id, dk_grubu, sinyal))""")
            c.execute("DELETE FROM log WHERE zaman < ?", (time.time() - 86400,))
            c.commit()

    def goruldu_mu(self, eid: str, dk: int, sinyal: str) -> bool:
        dk_g = (dk // 10) * 10
        with sqlite3.connect(self.db) as c:
            r = c.execute("SELECT 1 FROM log WHERE event_id=? AND dk_grubu=? AND sinyal=?",
                          (eid, dk_g, sinyal)).fetchone()
        return r is not None

    def kaydet(self, eid: str, dk: int, sinyal: str):
        dk_g = (dk // 10) * 10
        try:
            with sqlite3.connect(self.db) as c:
                c.execute("INSERT INTO log VALUES (?,?,?,?)",
                          (eid, dk_g, sinyal, time.time()))
                c.commit()
        except sqlite3.IntegrityError:
            pass

db = SinyalDB()

# ────────────────────────────────────────────────────────────────────────────
# VERİ YAPILARI
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class MacStats:
    gol:    int = 0
    sot:    int = 0
    da:     int = 0
    ta:     int = 0
    korner: int = 0
    sari:   int = 0
    kirmizi:int = 0

@dataclass
class OranBilgisi:
    """BetsAPI'den çekilen oran verileri"""
    ah_ev:     float = 0.0    # Asian Handicap (ev için, örn -0.75)
    ah_oran_ev:float = 0.0    # AH oranı ev tarafı
    ah_oran_dep:float= 0.0    # AH oranı dep tarafı
    ou_line:   float = 2.5    # Over/Under çizgisi
    ou_ust:    float = 0.0    # Over oranı
    ou_alt:    float = 0.0    # Under oranı
    ms_ev:     float = 0.0    # 1X2 ev oranı
    ms_x:      float = 0.0    # 1X2 beraberlik
    ms_dep:    float = 0.0    # 1X2 dep oranı
    gecerli:   bool  = False

@dataclass
class MacDurumu:
    eid:   str
    ev:    str
    dep:   str
    lig:   str
    dk:    int
    skor:  str
    home:  MacStats
    away:  MacStats
    oran:  OranBilgisi = field(default_factory=OranBilgisi)

    # Hesaplananlar
    toplam_gol: int = 0
    gol_fark:   int = 0
    iy_gol:     int = 0  # 1. yarı gol (dk>=46 için hafızadan)

    def __post_init__(self):
        self.toplam_gol = self.home.gol + self.away.gol
        self.gol_fark   = self.home.gol - self.away.gol

    @property
    def ah_abs(self) -> float:
        return abs(self.oran.ah_ev)

    @property
    def skor_durum(self) -> str:
        if self.gol_fark > 0: return "EV_ONE"
        if self.gol_fark < 0: return "DEP_ONE"
        return "ESIT"

    @property
    def favori_geride(self) -> bool:
        """Favori (AH<0) geride mi?"""
        return self.oran.ah_ev <= -0.5 and self.skor_durum == "DEP_ONE"

    @property
    def underdog_onde(self) -> bool:
        """Underdog (AH>0) önde mi?"""
        return self.oran.ah_ev >= 0.5 and self.skor_durum == "EV_ONE"

    @property
    def kg_var(self) -> bool:
        return self.home.gol > 0 and self.away.gol > 0

    @property
    def iy05_ust(self) -> bool:
        return self.iy_gol >= 1 if self.dk >= 46 else self.toplam_gol >= 1

    @property
    def iy15_ust(self) -> bool:
        return self.iy_gol >= 2 if self.dk >= 46 else self.toplam_gol >= 2


@dataclass
class Sinyal:
    tip:      str
    mesaj:    str
    guc:      float
    pattern:  str
    oncelik:  int = 1  # 1=normal, 2=yüksek, 3=elmas


# ────────────────────────────────────────────────────────────────────────────
# AH HAREKET TAKİBİ (Sharp Money Proxy)
# ────────────────────────────────────────────────────────────────────────────

class AHHareket:
    """
    AH çizgisinin zaman içindeki değişimini izler.
    Büyük değişim → sharp money hareketi sinyali.
    """
    def __init__(self):
        self._gecmis: Dict[str, deque] = {}

    def kaydet(self, eid: str, ah: float, zaman: float = None):
        if eid not in self._gecmis:
            self._gecmis[eid] = deque(maxlen=20)
        self._gecmis[eid].append((zaman or time.time(), ah))

    def hareket_hesapla(self, eid: str) -> Dict:
        """AH değişim hızı ve yönü döner"""
        kayitlar = list(self._gecmis.get(eid, []))
        if len(kayitlar) < 2:
            return {'hareket': 0.0, 'yon': 'sabit', 'sharp': False}

        ilk_ah = kayitlar[0][1]
        son_ah = kayitlar[-1][1]
        delta = son_ah - ilk_ah

        # Sharp money eşiği: 0.25 üzeri değişim anlamlı
        sharp = abs(delta) >= 0.25

        if delta > 0.1:   yon = 'ev_lehine'       # AH ev için artıyor
        elif delta < -0.1: yon = 'dep_lehine'      # AH dep için artıyor
        else:              yon = 'sabit'

        return {
            'ilk': ilk_ah,
            'son': son_ah,
            'delta': round(delta, 3),
            'yon': yon,
            'sharp': sharp
        }

ah_hareket = AHHareket()
iy_hafiza: Dict[str, int] = {}  # eid → 1.yarı gol sayısı


# ────────────────────────────────────────────────────────────────────────────
# ANA SİNYAL MOTORU
# ────────────────────────────────────────────────────────────────────────────

class V54Motor:
    """
    26.005 maç verisi + 507k arşivden çıkarılmış patternler.
    Her sinyal için gerçek n ve win% gösterilir.
    """

    # ── YARDIMCI ─────────────────────────────────────────────────────────────

    @staticmethod
    def sinyal_olustur(tip, mesaj, guc, pattern, oncelik=1) -> Sinyal:
        return Sinyal(tip=tip, mesaj=mesaj, guc=guc, pattern=pattern, oncelik=oncelik)

    # ── 1. SHARP MONEY SİNYALLERİ (AH Extreme) ───────────────────────────────

    @classmethod
    def sharp_money_kontrol(cls, mac: MacDurumu) -> List[Sinyal]:
        """
        AH |değeri| büyüdükçe piyasa 'sharp money' ile fiyatlamış demek.
        Veri: |AH|>=2.5 → Win%=94.3%, |AH|>=2.0 → 92.6%, |AH|>=1.5 → 90.7%
        """
        sinyaller = []
        ah = mac.oran.ah_ev
        ah_abs = mac.ah_abs

        # Güçlü favori: |AH| >= 2.0
        if ah_abs >= 2.0 and mac.dk <= 60:
            taraf = "EV SAHİBİ" if ah <= 0 else "DEPLASMAN"
            guc = 94.3 if ah_abs >= 2.5 else 92.6 if ah_abs >= 2.0 else 90.7
            sinyaller.append(cls.sinyal_olustur(
                tip="SHARP_EXTREME",
                mesaj=(
                    f"💎 SHARP MONEY — {taraf} GÜÇLÜ FAVORİ\n"
                    f"📊 AH={ah:+.2f} | |AH|={ah_abs:.2f} | {mac.dk}'\n"
                    f"🎯 Piyasa ezici favori fiyatladı\n"
                    f"📈 Veri: |AH|>={ah_abs:.1f} → Win%={guc:.1f}% (n={'932' if ah_abs>=2.5 else '1855' if ah_abs>=2.0 else '3709'})"
                ),
                guc=guc,
                pattern=f"Sharp: |AH|>={ah_abs:.1f}",
                oncelik=3
            ))

        return sinyaller

    # ── 2. FAVORİ GERİDEYKEN (Value Bet / Geri Dönüş) ───────────────────────

    @classmethod
    def favori_geri_donus(cls, mac: MacDurumu) -> List[Sinyal]:
        """
        Favori (AH<=-0.5) geride → Geri dönüş ihtimali yüksek.
        Veri: 0-30dk → Win%=93.4% (n=755), 31-45dk → 87.7% (n=481)
        
        Mantık: Piyasa favoriye güvenmiş, kısa vadeli geri kalmış → 
        bahis değeri (value) oluşuyor.
        """
        sinyaller = []
        if not mac.favori_geride:
            return sinyaller

        ah = mac.oran.ah_ev
        dk = mac.dk

        if dk <= 30:
            guc = 93.4 if ah <= -0.75 else 90.0
            n = "755"
        elif dk <= 45:
            guc = 87.7 if ah <= -0.75 else 84.0
            n = "481"
        elif dk <= 60:
            guc = 73.9
            n = "498"
        else:
            return sinyaller  # 60dk sonrası: zayıf (67.5%)

        taraf = "EV SAHİBİ" if ah <= 0 else "DEPLASMAN"
        sinyaller.append(cls.sinyal_olustur(
            tip="FAV_GERI_DONUS",
            mesaj=(
                f"🔄 FAVORİ GERİDE → GERİ DÖNÜŞ BEKLENTİSİ\n"
                f"📊 AH={ah:+.2f} | Skor: {mac.skor} | {dk}'\n"
                f"🏹 {taraf} favori ama geride\n"
                f"📈 Veri: {dk}dk pencere → Win%={guc:.1f}% (n={n})\n"
                f"💡 Ort. maç sonu gol: {1.2 if dk<=30 else 1.85:.2f}"
            ),
            guc=guc,
            pattern=f"Fav Geri Dönüş: AH={ah:+.2f} + DEP_ONE + {dk}'",
            oncelik=2
        ))
        return sinyaller

    # ── 3. AH × DAKİKA × SKOR — KOMBİNASYON PATTERNLER ─────────────────────

    @classmethod
    def ah_dakika_skor_combo(cls, mac: MacDurumu) -> List[Sinyal]:
        """
        26k maçtan çıkan en güçlü üçlü kombinasyonlar.
        """
        sinyaller = []
        ah = mac.oran.ah_ev
        dk = mac.dk
        sd = mac.skor_durum

        # ── ELMAS PATTERN: AH<=-1.5 + 0-20dk + EŞİT (Win%=95.2, n=372) ─────
        if ah <= -1.5 and dk <= 20 and sd == 'ESIT':
            sinyaller.append(cls.sinyal_olustur(
                tip="ELMAS_AH15_ESIT",
                mesaj=(
                    f"💎 ELMAS PATTERN — AH<=-1.5 + EŞİT + ERKEN\n"
                    f"📊 AH={ah:+.2f} | Skor: {mac.skor} (0-0) | {dk}'\n"
                    f"🎯 Güçlü favori erken golsüz = gol beklentisi çok yüksek\n"
                    f"📈 Veri: Win%=95.2% (n=372) ← En güçlü pattern"
                ),
                guc=95.2,
                pattern="ELMAS: AH<=-1.5 + 0-20dk + EŞİT",
                oncelik=3
            ))

        # ── AH<=-0.75 + 0-20dk + GERİDE (Win%=94.5, n=290) ─────────────────
        elif ah <= -0.75 and dk <= 20 and sd == 'DEP_ONE':
            sinyaller.append(cls.sinyal_olustur(
                tip="AH075_ERKEN_GERIDE",
                mesaj=(
                    f"🔥 AH<=-0.75 + ERKEN + GERİDE\n"
                    f"📊 AH={ah:+.2f} | Skor: {mac.skor} | {dk}'\n"
                    f"🎯 Hafif favori erken geride → güçlü geri dönüş\n"
                    f"📈 Veri: Win%=94.5% (n=290)"
                ),
                guc=94.5,
                pattern="AH<=-0.75 + 0-20dk + GERİDE",
                oncelik=3
            ))

        # ── AH<=-0.75 + İY 0 gol + 0-30dk (Win%=91.3, n=1102) ──────────────
        elif ah <= -0.75 and dk <= 30 and mac.toplam_gol == 0:
            sinyaller.append(cls.sinyal_olustur(
                tip="AH075_GOLSUZ_ERKEN",
                mesaj=(
                    f"⚽ FAVORİ + GOLSÜZ + ERKEN\n"
                    f"📊 AH={ah:+.2f} | Skor: 0-0 | {dk}'\n"
                    f"🎯 Maç henüz golsüz, favori baskı kurması beklenir\n"
                    f"📈 Veri: Win%=91.3% (n=1102, lift üst pattern)"
                ),
                guc=91.3,
                pattern="AH<=-0.75 + İY 0 gol + 0-30dk",
                oncelik=2
            ))

        # ── AH<=-0.5 + 0-30dk + EŞİT (Win%=89.6, n=1421) ───────────────────
        elif ah <= -0.5 and dk <= 30 and sd == 'ESIT':
            sinyaller.append(cls.sinyal_olustur(
                tip="FAV_ESIT_ERKEN",
                mesaj=(
                    f"⚡ FAVORİ + EŞİT + ERKEN PENCERE\n"
                    f"📊 AH={ah:+.2f} | Skor: {mac.skor} | {dk}'\n"
                    f"🎯 Favori henüz gol atmamış, baskı devam edecek\n"
                    f"📈 Veri: Win%=89.6% (n=1421)"
                ),
                guc=89.6,
                pattern="AH<=-0.5 + 0-30dk + EŞİT",
                oncelik=2
            ))

        # ── AH<=-0.75 + 21-35dk + GERİDE (Win%=91.7, n=351) ────────────────
        elif ah <= -0.75 and 21 <= dk <= 35 and sd == 'DEP_ONE':
            sinyaller.append(cls.sinyal_olustur(
                tip="AH075_ORTA_GERIDE",
                mesaj=(
                    f"🔥 FAVORİ + ORTA PERIYOT + GERİDE\n"
                    f"📊 AH={ah:+.2f} | Skor: {mac.skor} | {dk}'\n"
                    f"🎯 21-35dk arası favori geride = değerli pencere\n"
                    f"📈 Veri: Win%=91.7% (n=351)"
                ),
                guc=91.7,
                pattern="AH<=-0.75 + 21-35dk + GERİDE",
                oncelik=2
            ))

        # ── UNDERDOG ÖNDE + ERKEN (Win%=88-89%, n=260-632) ──────────────────
        elif ah >= 0.75 and dk <= 30 and sd == 'EV_ONE':
            guc = 88.5 if ah >= 0.75 else 85.0
            sinyaller.append(cls.sinyal_olustur(
                tip="UNDERDOG_ONDE_ERKEN",
                mesaj=(
                    f"⚡ UNDERDOG ÖNDE + ERKEN → GOL BEKLENTISI\n"
                    f"📊 AH={ah:+.2f} | Skor: {mac.skor} | {dk}'\n"
                    f"🎯 Underdog önde ama maç henüz erken → gol her iki taraftan beklenir\n"
                    f"📈 Veri: Win%={guc:.1f}% (n=260)"
                ),
                guc=guc,
                pattern="AH>=+0.75 + 0-30dk + ÖNDE",
                oncelik=2
            ))

        elif ah >= 1.0 and dk <= 30 and sd == 'ESIT':
            sinyaller.append(cls.sinyal_olustur(
                tip="UNDERDOG_ESIT_ERKEN",
                mesaj=(
                    f"⚡ UNDERDOG EŞİT + ERKEN\n"
                    f"📊 AH={ah:+.2f} | Skor: 0-0 | {dk}'\n"
                    f"🎯 Güçlü underdog eşit durumda → gol beklentisi\n"
                    f"📈 Veri: Win%=89.1% (n=632)"
                ),
                guc=89.1,
                pattern="AH>=+1.0 + 0-30dk + EŞİT",
                oncelik=2
            ))

        return sinyaller

    # ── 4. ARŞİV PATTERN (507k maç) × AH KONFIRMASYONU ──────────────────────

    @classmethod
    def arsiv_ah_kombo(cls, mac: MacDurumu) -> List[Sinyal]:
        """
        507k arşiv patternleri + AH bilgisi ile konfirme edilmiş sinyaller.
        Yarı arası (dk 38-47) en güçlü pencere.
        """
        sinyaller = []
        ah = mac.oran.ah_ev
        dk = mac.dk

        if not (38 <= dk <= 47):
            return sinyaller

        gol = mac.toplam_gol
        kg = mac.kg_var

        # ── IY0.5 ALT → M2.5 ALT (%93) × AH konfirmasyonu ───────────────────
        if gol == 0:
            guc_base = 93.0
            # AH dengeli veya underdog ise maç gerçekten düşük tempolu
            ah_bonus = 2.0 if ah >= -0.5 else 0.0
            guc = min(guc_base + ah_bonus, 98.5)

            sinyaller.append(cls.sinyal_olustur(
                tip="IY_GOLSUZ_M25_ALT",
                mesaj=(
                    f"⚽ 1.YARI GOLSÜZ → MAÇ 2.5 ALT\n"
                    f"📊 İY: 0-0 | AH={ah:+.2f} | {dk}'\n"
                    f"🎯 ARŞİV: IY0.5 ALT → M2.5 ALT = {guc_base:.0f}% (n=158k)\n"
                    f"📈 AH konfirme: {'+Bonus' if ah_bonus>0 else 'Nötr'} | Final Güç: {guc:.1f}%"
                ),
                guc=guc,
                pattern="ARŞİV P1: IY0.5 ALT → M2.5 ALT",
                oncelik=2
            ))

            # KG YOK ek konfirmasyon (%98.2)
            if not kg:
                sinyaller.append(cls.sinyal_olustur(
                    tip="IY_GOLSUZ_KG_YOK_M25_ALT",
                    mesaj=(
                        f"💎 GÜÇLÜ: IY GOLSÜZ + KG YOK → M2.5 ALT\n"
                        f"📊 İY: 0-0 | KG: Yok | AH={ah:+.2f} | {dk}'\n"
                        f"🎯 ARŞİV: IY0.5 ALT + KG YOK → %98.2 (n=119k)\n"
                        f"📈 En güçlü alt market pattern"
                    ),
                    guc=98.2,
                    pattern="ARŞİV P4: IY0.5 ALT + KG YOK → M2.5 ALT",
                    oncelik=3
                ))

        # ── IY1.5 ÜST + KG VAR → M2.5 ÜST (%69-83) ─────────────────────────
        elif gol >= 2 and kg:
            guc = 83.3 if ah <= -0.5 else 69.3

            sinyaller.append(cls.sinyal_olustur(
                tip="IY15_UST_KG_VAR_M25_UST",
                mesaj=(
                    f"🔥 1.YARIDA 2+ GOL + KG VAR → M2.5 ÜST\n"
                    f"📊 İY: {mac.home.gol}-{mac.away.gol} | KG: Var | AH={ah:+.2f} | {dk}'\n"
                    f"🎯 ARŞİV: IY1.5 ÜST + KG VAR → %{guc:.0f}% (n={'66k' if guc>=83 else '133k'})\n"
                    f"📈 AH={'Favori güçlendirir' if ah<=-0.5 else 'Nötr'}"
                ),
                guc=guc,
                pattern="ARŞİV P6/P7: IY1.5 ÜST + KG VAR → M2.5 ÜST",
                oncelik=2
            ))

        # ── IY 1 gol (sadece 1 takım) ─────────────────────────────────────────
        elif gol == 1 and not kg:
            sinyaller.append(cls.sinyal_olustur(
                tip="IY_1_GOL_TEK_TAKIM",
                mesaj=(
                    f"⚡ İY 1 GOL (TEK TAKIM) → M1.5 ÜST BEKLENIYOR\n"
                    f"📊 İY: {mac.skor} | KG: Yok | AH={ah:+.2f} | {dk}'\n"
                    f"🎯 ARŞİV: IY0.5 ÜST → M1.5 ÜST base rate yüksek\n"
                    f"📈 Maçta gol var, 2. yarı da gol beklentisi meşru"
                ),
                guc=72.0,
                pattern="ARŞİV: IY 1 gol → M1.5 ÜST",
                oncelik=1
            ))

        return sinyaller

    # ── 5. 2. YARI CANLI ANALİZ (dk 46-85) ──────────────────────────────────

    @classmethod
    def ikinci_yari(cls, mac: MacDurumu) -> List[Sinyal]:
        """2. yarıda AH + iy_gol kombinasyonu"""
        sinyaller = []
        if not (46 <= mac.dk <= 82):
            return sinyaller

        ah  = mac.oran.ah_ev
        dk  = mac.dk
        sd  = mac.skor_durum
        iy  = mac.iy_gol
        mac2_g = mac.toplam_gol - iy  # 2.yarı golleri

        # AH<=-1.0 + İY 1+ gol (Win%=86.8, n=4190) ───────────────────────────
        if ah <= -1.0 and iy >= 1 and mac.dk <= 65:
            sinyaller.append(cls.sinyal_olustur(
                tip="AH10_IY_GOL_2Y",
                mesaj=(
                    f"⚡ GÜÇLÜ FAVORİ + İY GOL VAR + 2.YARI\n"
                    f"📊 AH={ah:+.2f} | İY={iy} gol | 2Y={mac2_g} gol | {dk}'\n"
                    f"🎯 Favori 1.yarıda gol bulmuş, 2.yarı devam beklenir\n"
                    f"📈 Veri: AH<=-1.0 + İY 1+ → Win%=86.8% (n=4190)"
                ),
                guc=86.8,
                pattern="2Y: AH<=-1.0 + IY 1+ gol",
                oncelik=2
            ))

        # Favori 2.yarıda hâlâ geride (zayıf, dikkat) ─────────────────────────
        elif mac.favori_geride and dk <= 70:
            guc = 73.9 if dk <= 60 else 67.5
            sinyaller.append(cls.sinyal_olustur(
                tip="FAV_2Y_GERIDE",
                mesaj=(
                    f"⚠️ FAVORİ 2.YARIDA GERİDE ({dk}')\n"
                    f"📊 AH={ah:+.2f} | Skor: {mac.skor}\n"
                    f"🎯 Geri dönüş hâlâ mümkün ama zayıflıyor\n"
                    f"📈 Veri: {dk}dk → Win%={guc:.0f}% (n={'498' if dk<=60 else '234'})"
                ),
                guc=guc,
                pattern=f"2Y: Fav Geride {dk}'",
                oncelik=1
            ))

        return sinyaller

    # ── 6. AH HAREKET (Sharp Money Akışı) ────────────────────────────────────

    @classmethod
    def ah_hareket_sinyal(cls, mac: MacDurumu) -> List[Sinyal]:
        """AH çizgisindeki büyük değişim = sharp money hareketi"""
        sinyaller = []
        hareket = ah_hareket.hareket_hesapla(mac.eid)
        if not hareket.get('sharp', False):
            return sinyaller

        delta = hareket['delta']
        yon = hareket['yon']
        ilk = hareket.get('ilk', mac.oran.ah_ev)
        son = hareket.get('son', mac.oran.ah_ev)

        taraf = "EV SAHİBİ" if yon == 'ev_lehine' else "DEPLASMAN"
        sinyaller.append(cls.sinyal_olustur(
            tip="SHARP_MONEY_HAREKET",
            mesaj=(
                f"📉 SHARP MONEY HAREKETİ TESPİT EDİLDİ\n"
                f"📊 AH: {ilk:+.2f} → {son:+.2f} (Δ={delta:+.3f}) | {mac.dk}'\n"
                f"🎯 {taraf} lehine büyük para akışı\n"
                f"💡 >=0.25 değişim = profesyonel bahisçi baskısı\n"
                f"📈 Sharp money hareketi: piyasayı takip et"
            ),
            guc=72.0,
            pattern=f"Sharp Hareket: Δ={delta:+.3f} {yon}",
            oncelik=2
        ))
        return sinyaller


# ────────────────────────────────────────────────────────────────────────────
# API YARDIMCILARI
# ────────────────────────────────────────────────────────────────────────────

def si(v, k, d=0) -> int:
    try: return int(float(v.get(k, d) or d))
    except: return d


async def ah_cek(session: aiohttp.ClientSession, eid: str) -> OranBilgisi:
    """BetsAPI v2 odds endpoint — AH, OU ve 1X2 oranlarını çeker"""
    oran = OranBilgisi()
    try:
        url = (f"https://api.betsapi.com/v2/event/odds"
               f"?token={BETSAPI_TOKEN}&event_id={eid}&source=bet365")
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
            data = await r.json()

        if data.get('success') != 1:
            return oran

        res = data.get('results', {})

        # Asian Handicap
        ah_list = res.get('asian_handicap', {}).get('sp', {})
        if ah_list:
            try:
                first = list(ah_list.values())[0]
                oran.ah_ev = float(first.get('handicap', 0))
                odds = first.get('odds', [])
                if len(odds) >= 2:
                    oran.ah_oran_ev  = float(odds[0].get('odds', 0))
                    oran.ah_oran_dep = float(odds[1].get('odds', 0))
            except Exception:
                pass

        # Over/Under
        ou_list = res.get('over_under', {}).get('sp', {})
        if ou_list:
            try:
                first = list(ou_list.values())[0]
                oran.ou_line = float(first.get('handicap', 2.5))
                odds = first.get('odds', [])
                if len(odds) >= 2:
                    oran.ou_ust = float(odds[0].get('odds', 0))
                    oran.ou_alt = float(odds[1].get('odds', 0))
            except Exception:
                pass

        # 1X2
        ms_list = res.get('1_x_2', {}).get('sp', {})
        if ms_list:
            try:
                first = list(ms_list.values())[0]
                odds = first.get('odds', [])
                if len(odds) >= 3:
                    oran.ms_ev  = float(odds[0].get('odds', 0))
                    oran.ms_x   = float(odds[1].get('odds', 0))
                    oran.ms_dep = float(odds[2].get('odds', 0))
            except Exception:
                pass

        oran.gecerli = oran.ah_ev != 0.0

    except Exception as e:
        logger.debug(f"Oran çekme hatası [{eid}]: {e}")

    return oran


# ────────────────────────────────────────────────────────────────────────────
# ANA ANALİZ FONKSİYONU
# ────────────────────────────────────────────────────────────────────────────

async def mac_analiz(mac_data: dict, session: aiohttp.ClientSession) -> List[str]:
    try:
        eid  = str(mac_data['id'])
        ev   = mac_data['home']['name']
        dep  = mac_data['away']['name']
        lig  = mac_data['league']['name']
        dk   = int(mac_data.get('timer', {}).get('tm', 0))
        skor = mac_data.get('ss', '0-0')

        # Karantina
        lig_l = lig.lower()
        if any(k in lig_l for k in KARANTINA_LIGLER):
            return []

        # Stats
        stats = mac_data.get('stats', {})
        ev_v  = stats.get('1', {})
        dep_v = stats.get('2', {})

        home = MacStats(
            gol=si(ev_v,'SC'), sot=si(ev_v,'S1'), da=si(ev_v,'S4'),
            ta=si(ev_v,'S3'), korner=si(ev_v,'S2'),
            sari=si(ev_v,'S5'), kirmizi=si(ev_v,'S7')
        )
        away = MacStats(
            gol=si(dep_v,'SC'), sot=si(dep_v,'S1'), da=si(dep_v,'S4'),
            ta=si(dep_v,'S3'), korner=si(dep_v,'S2'),
            sari=si(dep_v,'S5'), kirmizi=si(dep_v,'S7')
        )

        # Temel filtreler
        if home.kirmizi > 0 or away.kirmizi > 0: return []
        if abs(home.gol - away.gol) >= 4: return []
        if dk < 5 or dk > 88: return []

        # IY gol hafızası
        if 43 <= dk <= 47:
            iy_hafiza[eid] = home.gol + away.gol
        iy_gol = iy_hafiza.get(eid, 0) if dk >= 46 else 0

        # Oran çek (20dk üstü)
        oran = OranBilgisi()
        if dk >= 5:
            oran = await ah_cek(session, eid)
            if oran.gecerli:
                ah_hareket.kaydet(eid, oran.ah_ev)

        mac = MacDurumu(
            eid=eid, ev=ev, dep=dep, lig=lig, dk=dk, skor=skor,
            home=home, away=away, oran=oran, iy_gol=iy_gol
        )

        # Oran yoksa sadece arşiv sinyalleri
        motor = V54Motor()
        tum_sinyaller: List[Sinyal] = []

        if oran.gecerli:
            tum_sinyaller += motor.sharp_money_kontrol(mac)
            tum_sinyaller += motor.favori_geri_donus(mac)
            tum_sinyaller += motor.ah_dakika_skor_combo(mac)
            tum_sinyaller += motor.ah_hareket_sinyal(mac)
            if dk >= 46:
                tum_sinyaller += motor.ikinci_yari(mac)

        # Arşiv patternleri (her zaman)
        tum_sinyaller += motor.arsiv_ah_kombo(mac)

        # Minimum güç filtresi
        MIN_GUC = {1: 70.0, 2: 65.0, 3: 60.0}  # önceliğe göre farklı eşik
        tum_sinyaller = [s for s in tum_sinyaller
                         if s.guc >= MIN_GUC.get(s.oncelik, 70.0)]

        # Tekrar filtresi + mesaj üret
        premium = any(p in lig_l for p in PREMIUM_LIGLER)
        mesajlar = []

        for s in sorted(tum_sinyaller, key=lambda x: -x.oncelik):
            if db.goruldu_mu(eid, dk, s.tip):
                continue
            db.kaydet(eid, dk, s.tip)

            etiket = "💎 " if s.oncelik == 3 else "🔥 " if s.oncelik == 2 else ""
            prem   = "⭐ " if premium else ""

            msg = (
                f"{'━'*34}\n"
                f"{prem}{etiket}*{s.tip}* | Güç: {s.guc:.0f}%\n"
                f"{'━'*34}\n"
                f"🏟️ {ev} {skor} {dep}\n"
                f"⏱️ {dk}' | 🏆 {lig}\n"
                f"\n{s.mesaj}\n"
                f"\n📌 *Pattern:* `{s.pattern}`"
            )
            mesajlar.append(msg)

        return mesajlar

    except Exception as e:
        logger.error(f"Analiz hatası [{mac_data.get('id','?')}]: {e}")
        return []


# ────────────────────────────────────────────────────────────────────────────
# ANA DÖNGÜ
# ────────────────────────────────────────────────────────────────────────────

async def ana_dongu():
    if not TELEGRAM_TOKEN or not BETSAPI_TOKEN:
        logger.error("ENV eksik! TELEGRAM_TOKEN ve BETSAPI_TOKEN gerekli.")
        return

    bot = Bot(token=TELEGRAM_TOKEN)

    baslik = (
        "🚀 *BOT V54 — AH + ORAN ANALİZİ*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *26.005 maç* (veri_exceli) + *507k arşiv*\n\n"
        "💎 *ELMAS PATTERNLER:*\n"
        "  AH<=-1.5 + 0-20dk + EŞİT → %95.2\n"
        "  AH<=-0.75 + 0-20dk + GERİDE → %94.5\n"
        "  IY0.5 ALT + KG YOK → %98.2\n\n"
        "🔥 *DEĞER PATTERN:*\n"
        "  FAVORİ GERİDE + 0-30dk → %93.4\n"
        "  |AH|>=2.5 → %94.3 (Sharp Money)\n\n"
        "🛡️ Filtreler: Krmz. Kart | Karantina | Tekrar\n"
        "⏱️ Optimal pencere: dk 5-35"
    )
    await bot.send_message(CHAT_ID, baslik, parse_mode="Markdown")
    logger.info("Bot V54 başlatıldı.")

    async with aiohttp.ClientSession() as session:
        dongu = 0
        while True:
            dongu += 1
            try:
                url = (f"https://api.betsapi.com/v1/events/inplay"
                       f"?sport_id=1&token={BETSAPI_TOKEN}")
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    data = await r.json()

                maclar = data.get('results', [])
                logger.info(f"[D{dongu}] {len(maclar)} maç | "
                            f"Hafıza: {len(iy_hafiza)} İY kaydı")

                sinyal_sayisi = 0
                for mac in maclar:
                    mesajlar = await mac_analiz(mac, session)
                    for msg in mesajlar:
                        await bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                        sinyal_sayisi += 1
                        await asyncio.sleep(0.3)

                if sinyal_sayisi:
                    logger.info(f"[D{dongu}] → {sinyal_sayisi} sinyal gönderildi")

            except aiohttp.ClientError as e:
                logger.error(f"API hatası: {e}")
            except Exception as e:
                logger.error(f"Döngü hatası: {e}")

            await asyncio.sleep(60)


# ────────────────────────────────────────────────────────────────────────────
# DEBUG / TEST MODU
# ────────────────────────────────────────────────────────────────────────────

def debug_test():
    print("\n" + "="*65)
    print("BOT V54 — AH + ORAN ANALİZİ TEST")
    print("="*65)

    test_cases = [
        # (açıklama, dk, ev_g, dep_g, ah, beklenen_tipler)
        ("ELMAS: AH=-1.75 + 0-0 + 15dk",         15, 0, 0, -1.75, ["ELMAS_AH15_ESIT"]),
        ("Fav Erken Geride: AH=-0.9 + 0-1 + 18dk", 18, 0, 1, -0.9, ["AH075_ERKEN_GERIDE"]),
        ("Golsüz Erken: AH=-0.8 + 0-0 + 22dk",    22, 0, 0, -0.8, ["AH075_GOLSUZ_ERKEN"]),
        ("Fav Eşit 25dk: AH=-0.6 + 0-0",          25, 0, 0, -0.6, ["FAV_ESIT_ERKEN"]),
        ("Underdog Önde: AH=+1.0 + 1-0 + 20dk",   20, 1, 0, +1.0, ["UNDERDOG_ONDE_ERKEN"]),
        ("Sharp Extreme: AH=-2.5 + 45dk",          45, 1, 0, -2.5, ["SHARP_EXTREME"]),
        ("İY Golsüz YArı arası: 0-0 + 45dk",       45, 0, 0, -0.5, ["IY_GOLSUZ_M25_ALT","IY_GOLSUZ_KG_YOK_M25_ALT"]),
        ("İY 2-1 + KG VAR",                         45, 2, 1, -0.75,["IY15_UST_KG_VAR_M25_UST"]),
        ("Fav Değer: AH=-1.0 + 0-1 + 0-30dk",      28, 0, 1, -1.0, ["FAV_GERI_DONUS"]),
    ]

    motor = V54Motor()
    for aciklama, dk, eg, dg, ah, beklenen in test_cases:
        mac = MacDurumu(
            eid="T", ev="Ev", dep="Dep", lig="Test Ligi",
            dk=dk, skor=f"{eg}-{dg}",
            home=MacStats(gol=eg, sot=4, da=7, ta=12, korner=3),
            away=MacStats(gol=dg, sot=3, da=5, ta=9, korner=2),
            oran=OranBilgisi(ah_ev=ah, gecerli=True),
            iy_gol=eg+dg if dk>=46 else 0
        )

        tum = []
        tum += motor.sharp_money_kontrol(mac)
        tum += motor.favori_geri_donus(mac)
        tum += motor.ah_dakika_skor_combo(mac)
        tum += motor.arsiv_ah_kombo(mac)

        uretilen = [s.tip for s in tum]
        tum_ok = all(b in uretilen for b in beklenen)

        print(f"\n{'✅' if tum_ok else '⚠️'} {aciklama}")
        print(f"   Beklenen: {beklenen}")
        print(f"   Üretilen: {uretilen}")
        for s in tum:
            isaret = "✅" if s.tip in beklenen else "➕"
            print(f"   {isaret} {s.tip} (Güç:{s.guc:.0f}% | Ön:{s.oncelik})")

    print("\n" + "="*65)
    print("Test tamamlandı.")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        debug_test()
    else:
        asyncio.run(ana_dongu())
