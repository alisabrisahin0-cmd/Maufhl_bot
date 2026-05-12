import asyncio, aiohttp, os, logging, re, time, sqlite3
from telegram import Bot
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

# ============================================================================
# BOT V51 — AH MİKROYAPI ENTEGRASYONu (Quantitative AH Market Analysis)
# ============================================================================
# V50'deki 15 düzeltme korundu. Yeni eklenenler:
#
# [AH-1]  AH split mekanizması — çeyrekli handikap modality hesabı
#          (-0.75 = stake/2 → -0.5 + stake/2 → -1.0 mantığı)
# [AH-2]  AH hareket takibi + CLV hesabı
#          (önceki AH ile fark → daralıyor mu genişliyor mu)
# [AH-3]  Reverse Line Movement (RLM) tespiti
#          (AH favoriden uzaklaşıyorsa → sharp money karşı tarafta)
# [AH-4]  SOT kalitesi — xG per shot + gol verimliliği
#          (Basaksehir case: 17 şut + 6 büyük fırsat farkı)
# [AH-5]  Corner deficit + AH düşüşü kombinasyonu (Signal Beta)
#          (baskın takım köşe üretemiyor ama AH düşük → gizli değer)
# [AH-6]  UCL knockout/grup ayrımı — knockout'ta çarpan düşürüldü
# [AH-7]  AH 0.0 (Draw No Bet) özel bonusu
# [AH-8]  Erken dakika korner oranı filtresi (korner/dakika > 0.3)
# ============================================================================

# ============================================================================
# YAPISAL SABITLER
# ============================================================================

LIG_CARPANLARI = {
    'bundesliga':           1.85,
    'champions league':     1.85,
    'uefa champions':       1.85,
    'eredivisie':           1.50,
    'türkiye 1 lig':        1.35,
    'turkiye 1 lig':        1.35,
    '1. lig':               1.35,
    'serie b':              1.30,
    'ligue 1':              1.20,
    'la liga':              1.15,
    'serie a':              1.10,
    'primeira liga':        1.10,
    'primera liga':         1.10,
    'championship':         0.85,
    'premier league':       0.85,
    'england premier':      0.85,
    'super lig':            0.75,
    'süper lig':            0.75,
    'brazil':               0.65,
    'serie a brazil':       0.65,
}

KARANTINA_LIGLER = [
    'brazil', 'brasil', 'kenya', 'ethiopia', 'rwanda',
    'oman', 'kuwait', 'iraq stars', 'afghanistan',
]

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

sinyal_logger = logging.getLogger('sinyal')
_sh = logging.StreamHandler()
_sh.setFormatter(logging.Formatter('🎯 %(asctime)s SINYAL | %(message)s', '%H:%M:%S'))
sinyal_logger.addHandler(_sh)
sinyal_logger.setLevel(logging.INFO)

# ============================================================================
# KONFIGÜRASYON
# ============================================================================

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID           = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN     = os.getenv("BETSAPI_TOKEN", "")
GROK_API_KEY      = os.getenv("GROK_API_KEY") or None
GEMINI_API_KEY_1  = os.getenv("GEMINI_API_KEY_1") or None
GEMINI_API_KEY_2  = os.getenv("GEMINI_API_KEY_2") or None
GEMINI_API_KEY_3  = os.getenv("GEMINI_API_KEY_3") or None

print(f"🔑 API Keys: Grok={'✅' if GROK_API_KEY else '❌'} | "
      f"Gemini={sum(1 for k in [GEMINI_API_KEY_1,GEMINI_API_KEY_2,GEMINI_API_KEY_3] if k)}/3")

# ============================================================================
# KALICI SİNYAL GEÇMİŞİ — SQLite
# ============================================================================

class SinyalGecmisi:
    def __init__(self, db_path="sinyaller.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sinyaller (
                    event_id    TEXT NOT NULL,
                    dk_grubu    INTEGER NOT NULL,
                    sinyal_tipi TEXT NOT NULL,
                    zaman       REAL NOT NULL,
                    PRIMARY KEY (event_id, dk_grubu, sinyal_tipi)
                )
            """)
            conn.execute("DELETE FROM sinyaller WHERE zaman < ?",
                         (time.time() - 86400,))
            conn.commit()

    @staticmethod
    def _dk_grubu(dakika: int) -> int:
        return (dakika // 5) * 5

    def zaten_gonderildi_mi(self, event_id: str, dakika: int,
                            sinyal_tipi: str) -> bool:
        dk = self._dk_grubu(dakika)
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT 1 FROM sinyaller WHERE event_id=? AND dk_grubu=? AND sinyal_tipi=?",
                (event_id, dk, sinyal_tipi)
            )
            return cur.fetchone() is not None

    def kaydet(self, event_id: str, dakika: int, sinyal_tipi: str):
        dk = self._dk_grubu(dakika)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO sinyaller VALUES (?,?,?,?)",
                    (event_id, dk, sinyal_tipi, time.time())
                )
                conn.commit()
        except sqlite3.IntegrityError:
            pass


sinyal_gecmisi = SinyalGecmisi()

# ============================================================================
# [AH-2] AH HAREKET TAKİBİ — Maç bazında önceki AH değerlerini sakla
# ============================================================================

class AHHareketTakibi:
    """
    Her maç için AH geçmişini saklar.
    Daralma (contraction): AH mutlak değeri küçülüyor → time-decay mispricing fırsatı
    Genişleme (expansion): AH mutlak değeri büyüyor → sharp money favoriyi desteklemiyor

    Signal Alpha tetikleyicisi:
      önceki AH -1.25 → şimdiki AH -0.50 (0.75 daralma) + yüksek DA → ALIM
    """

    def __init__(self):
        # {event_id: [(zaman, ah_ev, ah_dep, oran_ev, oran_dep), ...]}
        self._gecmis: Dict[str, List[Tuple]] = {}
        self._maks_gecmis = 10   # maç başına en fazla 10 kayıt

    def kaydet(self, event_id: str, ah_ev: float, ah_dep: float,
               oran_ev: float, oran_dep: float):
        if event_id not in self._gecmis:
            self._gecmis[event_id] = []
        kayitlar = self._gecmis[event_id]
        kayitlar.append((time.time(), ah_ev, ah_dep, oran_ev, oran_dep))
        if len(kayitlar) > self._maks_gecmis:
            kayitlar.pop(0)

    def ah_hareketi(self, event_id: str, guncel_ah_ev: float
                    ) -> Tuple[float, str]:
        """
        Döner: (hareket_miktari, yon)
        yon: 'daralma' | 'genisleme' | 'sabit' | 'yetersiz_veri'
        """
        kayitlar = self._gecmis.get(event_id, [])
        if len(kayitlar) < 2:
            return 0.0, 'yetersiz_veri'
        ilk_ah  = abs(kayitlar[0][1])
        son_ah  = abs(guncel_ah_ev)
        fark    = ilk_ah - son_ah   # pozitif = daralma (değer küçüldü)
        if abs(fark) < 0.10:
            return fark, 'sabit'
        return fark, ('daralma' if fark > 0 else 'genisleme')

    def clv_hesapla(self, event_id: str, alinma_ah: float,
                    kapanma_ah: float) -> float:
        """
        CLV = (|alinma_ah| - |kapanma_ah|) / |alinma_ah|
        Pozitif = alım kapanıştan daha yüksek AH'ta yapıldı = değer var
        """
        if abs(alinma_ah) < 0.01:
            return 0.0
        return round((abs(alinma_ah) - abs(kapanma_ah)) / abs(alinma_ah), 4)

    def temizle(self, event_id: str):
        self._gecmis.pop(event_id, None)


ah_hareket = AHHareketTakibi()


# ============================================================================
# [AH-1] AH SPLİT MEKANİZMASI
# ============================================================================

@dataclass
class AHSplitSonuc:
    """
    Çeyrekli AH bahsinin split sonucunu temsil eder.
    Örnek: -0.75 AH'ta 1 gol farkı → half_win
    """
    ah_degeri:       float
    tip:             str    # 'tam_iade' | 'yarim_iade' | 'ikilik' | 'dnb'
    split_alt:       float  # alt çizgi (örn. -0.75 için -0.5)
    split_ust:       float  # üst çizgi (örn. -0.75 için -1.0)
    iade_olasiligi:  float  # 0.0 - 1.0
    kayip_baskisi:   float  # relatif kayıp baskısı katsayısı
    bonus_puan:      float  # sinyal puanına eklenir


def ah_split_hesapla(ah_degeri: float) -> AHSplitSonuc:
    """
    [AH-1] AH split mekanizması.

    Rapor bulgusu: Bettors lose MORE on no-refund lines (-0.5, -1.5)
    than on split lines (-0.25, -0.75) because of mispriced margin.

    AH türleri:
    - Tam sayı (0, -1, -2):   DNB veya tam iade mümkün → en düşük kayıp
    - Yarım (.5, -0.5, -1.5): Kesinlikle kazanır ya da kaybeder → yüksek kayıp baskısı
    - Çeyrek (.25, -0.25 vb): Stake ikiye bölünür → orta kayıp baskısı

    Bonus mantığı:
    - -0.25 veya -0.50: split riski yok/düşük → Signal Alpha preferred → +2.0 bonus
    - 0.0 (DNB): tam iade mümkün → en düşük risk → +3.0 bonus
    - -0.75 veya -1.25: split var ama bir bacak iade → +1.0 bonus
    - -0.50 veya -1.50: no-refund → kayıp baskısı yüksek → 0 bonus
    """
    a = abs(ah_degeri)
    kesir = round(a % 1, 2)

    if a < 0.01:
        # AH 0.0 = Draw No Bet — [AH-7] özel bonus
        return AHSplitSonuc(
            ah_degeri=ah_degeri, tip='dnb',
            split_alt=0.0, split_ust=0.0,
            iade_olasiligi=1.0, kayip_baskisi=0.5,
            bonus_puan=3.0
        )
    elif kesir < 0.01 or abs(kesir - 1.0) < 0.01:
        # Tam sayı: -1.0, -2.0 → tam iade mümkün
        return AHSplitSonuc(
            ah_degeri=ah_degeri, tip='tam_iade',
            split_alt=a, split_ust=a,
            iade_olasiligi=0.5, kayip_baskisi=0.7,
            bonus_puan=2.0
        )
    elif abs(kesir - 0.5) < 0.01:
        # Yarım: -0.5, -1.5 → no-refund, yüksek kayıp baskısı
        return AHSplitSonuc(
            ah_degeri=ah_degeri, tip='ikilik',
            split_alt=a, split_ust=a,
            iade_olasiligi=0.0, kayip_baskisi=1.3,
            bonus_puan=0.0
        )
    elif abs(kesir - 0.25) < 0.01:
        # Çeyrek: -0.25 → stake yarısı 0.0 (DNB), yarısı -0.5
        # Düşük kayıp baskısı — Signal Alpha'nın tercihi
        return AHSplitSonuc(
            ah_degeri=ah_degeri, tip='yarim_iade',
            split_alt=0.0, split_ust=a + 0.25,
            iade_olasiligi=0.5, kayip_baskisi=0.8,
            bonus_puan=2.0
        )
    elif abs(kesir - 0.75) < 0.01:
        # Üç çeyrek: -0.75 → stake yarısı -0.5, yarısı -1.0
        # 1 gol farkında: -0.5 kazanır, -1.0 iade → half win
        return AHSplitSonuc(
            ah_degeri=ah_degeri, tip='yarim_iade',
            split_alt=a - 0.25, split_ust=a + 0.25,
            iade_olasiligi=0.5, kayip_baskisi=1.0,
            bonus_puan=1.0
        )
    else:
        return AHSplitSonuc(
            ah_degeri=ah_degeri, tip='bilinmeyen',
            split_alt=a, split_ust=a,
            iade_olasiligi=0.0, kayip_baskisi=1.0,
            bonus_puan=0.0
        )


# ============================================================================
# [AH-3] REVERSE LINE MOVEMENT TESPİTİ
# ============================================================================

def rlm_tespit(event_id: str, guncel_ah_ev: float,
               home_da_ratio: float) -> Tuple[bool, str, float]:
    """
    [AH-3] Reverse Line Movement tespiti.

    Senaryo: DA oranı ev sahibini destekliyor (>0.6)
    AMA AH ev sahibinden uzaklaşıyor (genişliyor / daha az favori)
    → Sharp money DEPLASMAN'da → public trap

    Ters senaryo (Signal Beta):
    DA düşük (ev sahibi köşe/toposa baskın görünüyor)
    AMA AH ev sahibi lehine → sharp money EV SAHİBİ'nde → gizli değer

    Döner: (rlm_var_mi, aciklama, bonus_puan)
    """
    hareket, yon = ah_hareket.ah_hareketi(event_id, guncel_ah_ev)

    if yon == 'yetersiz_veri':
        return False, "RLM: Yetersiz veri", 0.0

    # Klasik RLM: DA ev sahibini destekliyor ama AH genişliyor
    if home_da_ratio > 0.60 and yon == 'genisleme' and abs(hareket) >= 0.25:
        return (True,
                f"RLM TESPİT: DA ev lehine ({home_da_ratio:.0%}) "
                f"ama AH genişliyor ({hareket:+.2f}) → sharp dep'ta",
                -2.0)   # Ceza: sistem ters oynuyor olabilir

    # Gizli değer: DA düşük ama AH daralıyor (sharp ev sahibini destekliyor)
    if home_da_ratio < 0.45 and yon == 'daralma' and abs(hareket) >= 0.25:
        return (True,
                f"GİZLİ DEĞER: Düşük DA ({home_da_ratio:.0%}) "
                f"ama AH daralıyor ({hareket:+.2f}) → sharp ev'de",
                +2.5)   # Bonus: sharp money arka planda ev sahibini destekliyor

    # Signal Beta: Corner deficit + AH düşük → public trap
    return False, f"RLM: Normal hareket ({yon}, {hareket:+.2f})", 0.0


# ============================================================================
# [AH-4] SOT KALİTESİ — xG per shot ve gol verimliliği
# ============================================================================

def sot_kalitesi_hesapla(sot: int, da: int, gol: int,
                         dakika: int) -> Tuple[float, str, float]:
    """
    [AH-4] Şut kalitesi = gerçek tehdit mi, steril baskı mı?

    Rapor: Signal Beta tetikleyicisi → xG per shot > 0.12

    xG_per_shot proxy = gol / max(sot, 1)
    Eğer gol yoksa DA/SOT oranı kullanılır (conversion capacity)

    Döner: (kalite_skoru, açıklama, bonus_puan)
    """
    if sot == 0:
        return 0.0, "SOT sıfır — veri yok", 0.0

    # Temel verimlilik: gol / şut
    gol_verimlilik = gol / sot if sot > 0 else 0.0

    # DA→SOT dönüşüm oranı: kaliteli atak üretiyor mu?
    da_sot_oran = sot / da if da > 0 else 0.0

    # Dakika başı şut yoğunluğu
    sot_per_min = sot / max(dakika, 1)

    # Composite kalite skoru (0-1 aralığı)
    kalite = (
        min(da_sot_oran * 2.0, 0.4) +   # DA→SOT dönüşüm: max 0.4
        min(gol_verimlilik * 2.0, 0.3) + # Gol verimliliği: max 0.3
        min(sot_per_min * 10, 0.3)        # Şut yoğunluğu: max 0.3
    )

    if kalite >= 0.60:
        return kalite, f"YÜKSEK KALİTE (DA→SOT:{da_sot_oran:.2f}, Verim:{gol_verimlilik:.2f})", +2.0
    elif kalite >= 0.35:
        return kalite, f"ORTA KALİTE (DA→SOT:{da_sot_oran:.2f})", +0.5
    else:
        return kalite, f"DÜŞÜK KALİTE — steril baskı riski (DA→SOT:{da_sot_oran:.2f})", -1.0


# ============================================================================
# API RATE LIMITER
# ============================================================================

class APIRateLimiter:
    def __init__(self, max_concurrent=5, requests_per_second=10):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.requests_per_second = requests_per_second
        self.last_request_time = 0.0
        self.request_count = 0
        self.lock = asyncio.Lock()
        self.total_requests = 0
        self.throttled_count = 0

    async def acquire(self):
        await self.semaphore.acquire()
        async with self.lock:
            now = time.time()
            if now - self.last_request_time >= 1.0:
                self.request_count = 0
                self.last_request_time = now
            if self.request_count >= self.requests_per_second:
                wait = 1.0 - (now - self.last_request_time)
                if wait > 0:
                    self.throttled_count += 1
                    await asyncio.sleep(wait)
                self.request_count = 0
                self.last_request_time = time.time()
            self.request_count += 1
            self.total_requests += 1

    def release(self):
        self.semaphore.release()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *_):
        self.release()

    def get_stats(self) -> dict:
        return {
            'total': self.total_requests,
            'throttled': self.throttled_count,
            'rate': self.request_count
        }


api_rate_limiter = APIRateLimiter()


# ============================================================================
# EVENT LOOP MONITOR
# ============================================================================

class EventLoopMonitor:
    def __init__(self, threshold_ms=50, interval=0.1):
        self.threshold_ms = threshold_ms
        self.interval = interval
        self.lag_count = 0
        self.max_lag = 0.0
        self.total = 0
        self.running = False

    async def monitor(self):
        self.running = True
        while self.running:
            t0 = time.time()
            await asyncio.sleep(self.interval)
            lag = (time.time() - t0 - self.interval) * 1000
            self.total += 1
            if lag > self.threshold_ms:
                self.lag_count += 1
                self.max_lag = max(self.max_lag, lag)
                if lag > 200:
                    logger.error(f"KRİTİK LAG: {lag:.0f}ms")
            if self.total % 3000 == 0:
                pct = self.lag_count / self.total * 100
                logger.info(f"EventLoop: %{pct:.1f} lag, max={self.max_lag:.0f}ms")

    def stop(self):
        self.running = False


loop_monitor = EventLoopMonitor()


# ============================================================================
# LİG FİLTRELEME
# ============================================================================

class LeagueFilter:
    ALWAYS_REJECT = [
        r'\be[-\s]?sport[s]?\b',
        r'\bvirtual\b', r'\bsimulat',
        r'\b(w|women|kadın|kadin)\b',
        r'\b(reserves?|rezerv)\b',
        r'\b(youth|junior|academy)\b',
        r'\bu\d{2}\b',
    ]
    KARANTINA = KARANTINA_LIGLER
    WHITELIST  = [
        'bundesliga', 'eredivisie', 'champions league', 'europa league',
        'conference league', 'premier league', 'championship',
        'serie a', 'serie b', 'la liga', 'ligue 1', 'ligue 2',
        'primeira liga', 'pro league', 'super league', 'süper lig',
        'super lig', '1. lig', '2. lig', 'premiership',
        'superligaen', 'allsvenskan', 'eliteserien',
    ]

    @staticmethod
    def check_league(league_name: str, home_team: str,
                     away_team: str) -> Tuple[bool, str]:
        full  = f"{league_name} {home_team} {away_team}".lower()
        lig_l = league_name.lower()
        for pat in LeagueFilter.ALWAYS_REJECT:
            if re.search(pat, full):
                return False, f"REJECT: {pat}"
        for kw in LeagueFilter.KARANTINA:
            if kw in lig_l:
                return False, f"KARANTINA: {kw}"
        for kw in LeagueFilter.WHITELIST:
            if kw in lig_l:
                return True, f"WHITELIST: {kw}"
        return True, "NEUTRAL"

    @staticmethod
    def get_league_multiplier(league_name: str) -> float:
        ll = league_name.lower()
        # [AH-6] UCL knockout tespiti — daha düşük çarpan
        if ('champions league' in ll or 'uefa champions' in ll):
            if any(x in ll for x in ['knockout', 'round of', 'quarterfinal',
                                      'semifinal', 'final', 'last 16', 'last16']):
                return 1.40   # UCL knockout: yüksek entropi, daha az alpha
            return 1.85       # UCL group: normal
        for kw, c in LIG_CARPANLARI.items():
            if kw in ll:
                return c
        return 1.0

    @staticmethod
    def get_da_threshold(league_name: str) -> float:
        ll = league_name.lower()
        if any(k in ll for k in ['bundesliga', 'eredivisie', 'u23', 'u21',
                                  'u20', 'u19', 'süper lig', 'super lig',
                                  'turkey', 'portugal']):
            return 1.3
        if any(k in ll for k in ['kuwait', 'egypt', 'third division',
                                  'regionalliga', 'amateur']):
            return 2.0
        return 1.5

    @staticmethod
    def is_karantina(league_name: str) -> bool:
        ll = league_name.lower()
        return any(k in ll for k in KARANTINA_LIGLER)


# ============================================================================
# YARDIMCI FONKSİYONLAR
# ============================================================================

def guvenli_int(v, d=0):
    try:
        return int(float(v)) if v not in ('', None) else d
    except:
        return d

def guvenli_float(v, d=0.0):
    try:
        return float(v) if v not in ('', None) else d
    except:
        return d

def esnek_liste_duzelt(veri):
    duz = []
    if isinstance(veri, list):
        for e in veri:
            duz.extend(esnek_liste_duzelt(e))
    elif isinstance(veri, dict):
        duz.append(veri)
    return duz


# ============================================================================
# TAKIM İSTATİSTİKLERİ
# ============================================================================

@dataclass
class TeamStats:
    ta:     int = 0
    da:     int = 0
    sot:    int = 0
    gol:    int = 0
    korner: int = 0

    def validate_hierarchy(self) -> Tuple[bool, List[str]]:
        errs = []
        if self.ta  < self.da:  errs.append(f"TA({self.ta})<DA({self.da})")
        if self.da  < self.sot: errs.append(f"DA({self.da})<SOT({self.sot})")
        if self.sot < self.gol: errs.append(f"SOT({self.sot})<Gol({self.gol})")
        return len(errs) == 0, errs

    def calculate_xg(self, dakika: int = 45) -> float:
        ham = (self.sot * 0.15 + self.da * 0.015 +
               self.ta * 0.01 + self.korner * 0.03)
        return round(ham * (45 / max(dakika, 1)), 2)

    def detect_fake_pressure(self) -> bool:
        if self.da > 8 and self.sot == 0:
            return True
        if self.da > 0 and self.sot > 0 and self.da / self.sot > 8:
            return True
        if self.korner >= 8 and self.sot < 5:
            return True
        return False

    def korner_orani(self, dakika: int) -> float:
        """[AH-8] Dakika başı köşe oranı"""
        return self.korner / max(dakika, 1)


class MatchDataProtection:
    @staticmethod
    def validate_match_data(home: TeamStats,
                            away: TeamStats) -> Tuple[bool, List[str]]:
        errs = []
        ok_h, e_h = home.validate_hierarchy()
        if not ok_h: errs.extend([f"EV: {e}" for e in e_h])
        ok_a, e_a = away.validate_hierarchy()
        if not ok_a: errs.extend([f"DEP: {e}" for e in e_a])
        if home.gol + away.gol >= 5:
            errs.append("KOPMUŞ MAÇ: ≥5 gol")
        return len(errs) == 0, errs


# ============================================================================
# SKOR DURUMU FİLTRESİ
# ============================================================================

def skor_durumu_kontrol(ev_gol: int,
                        dep_gol: int) -> Tuple[bool, str, float]:
    toplam = ev_gol + dep_gol
    fark   = abs(ev_gol - dep_gol)
    if toplam >= 5:  return False, "KAOS",    0.0
    if fark   >= 3:  return False, "ROLANTI", 0.0
    if toplam == 0:  return False, "SIFIR",   0.0
    if toplam == 1:  return True,  "DUSUK",  -3.0
    if toplam == 2:  return True,  "NORMAL", -1.0
    if toplam == 3:  return True,  "IYI",    +2.0
    return             True,  "ALTIN",  +3.0


# ============================================================================
# SİNYAL TİPLERİ
# ============================================================================

class SignalType(Enum):
    IY_GOL  = "İY_GOL"
    EV_GOL  = "EV_GOL"
    DEP_GOL = "DEP_GOL"
    IY2_GOL = "İY2_GOL"
    IY2_GEC = "İY2_GEC"


@dataclass
class SignalResult:
    valid:       bool
    signal_type: Optional[SignalType]
    score:       float
    reason:      str
    details:     Dict = field(default_factory=dict)


# ============================================================================
# İLK YARI GOL MODÜLÜ
# ============================================================================

class IYGolModule:
    @staticmethod
    def check(minute: int, home_score: int, away_score: int,
              home: TeamStats, away: TeamStats,
              league_name: str = "") -> SignalResult:

        if not (15 <= minute <= 40):
            return SignalResult(False, None, 0.0, "Dakika dışı", {})
        if home_score + away_score > 1:
            return SignalResult(False, None, 0.0, "İY skor yüksek", {})

        total_da     = home.da + away.da
        da_esik      = LeagueFilter.get_da_threshold(league_name)
        da_per_min   = total_da / minute if minute > 0 else 0

        if da_per_min < da_esik:
            return SignalResult(False, None, 0.0,
                f"DA düşük: {da_per_min:.2f}<{da_esik}", {})

        # [AH-8] Erken dönemde aşırı köşe oranı → sahte baskı riski
        toplam_korner = home.korner + away.korner
        korner_per_dk = toplam_korner / max(minute, 1)
        if korner_per_dk > 0.3 and home.sot + away.sot < 3:
            return SignalResult(False, None, 0.0,
                f"Sahte baskı: Yüksek köşe oranı ({korner_per_dk:.2f}/dk) ama SOT düşük", {})

        score  = 5.0 + min(da_per_min * 2, 5.0)
        reason = "İY Gol — DA ivmesi yüksek"
        if 24 <= minute <= 36:
            score  *= 1.8
            reason += " | ALTIN PENCERE (×1.8)"

        return SignalResult(True, SignalType.IY_GOL, round(score, 2),
                            reason, {'da_per_min': round(da_per_min, 2)})


# ============================================================================
# EV/DEPLASMAN GOL MODÜLÜ — [AH-4] [AH-5] [AH-1] [AH-3] entegre
# ============================================================================

class EvDepGolModule:
    @staticmethod
    def check(minute: int, home: TeamStats, away: TeamStats,
              ah_home: float, ah_away: float,
              league_name: str = "",
              event_id: str = "") -> SignalResult:

        if not (20 <= minute <= 80):
            return SignalResult(False, None, 0.0, "Dakika dışı", {})

        total_da = home.da + away.da
        if total_da == 0:
            return SignalResult(False, None, 0.0, "DA verisi yok", {})

        home_da_ratio = home.da / total_da
        away_da_ratio = away.da / total_da

        # Baskın takım tespiti
        if home_da_ratio > 0.6:
            dom, dom_ratio, sig_type = "HOME", home_da_ratio, SignalType.EV_GOL
            if ah_home >= 0:
                return SignalResult(False, None, 0.0, "Ev favori değil (AH)", {})
            dom_stats, dom_ah = home, ah_home
        elif away_da_ratio > 0.6:
            dom, dom_ratio, sig_type = "AWAY", away_da_ratio, SignalType.DEP_GOL
            if ah_away <= 0:
                return SignalResult(False, None, 0.0, "Dep favori değil (AH)", {})
            dom_stats, dom_ah = away, ah_away
        else:
            # [AH-5] Corner deficit + AH düşük (Signal Beta) kontrolü
            # Köşede geri olan ama AH'ta favori olan takım gizli değer
            corner_deficit_home = home.korner < away.korner and abs(ah_home) <= 0.50
            corner_deficit_away = away.korner < home.korner and abs(ah_away) <= 0.50
            if corner_deficit_home or corner_deficit_away:
                dom  = "HOME" if corner_deficit_home else "AWAY"
                dom_stats = home if dom == "HOME" else away
                dom_ah    = ah_home if dom == "HOME" else ah_away
                sig_type  = SignalType.EV_GOL if dom == "HOME" else SignalType.DEP_GOL
                dom_ratio = 0.50  # düşük ratio — corner deficit senaryosu
                logger.debug(f"[AH-5] Corner deficit + AH düşük → Signal Beta")
            else:
                return SignalResult(False, None, 0.0, "Baskın takım yok", {})

        # Sahte baskı kontrolü
        if dom_stats.detect_fake_pressure():
            return SignalResult(False, None, 0.0, "Sahte baskı", {})

        score  = 6.0 + (dom_ratio - 0.5) * 10
        reason = f"{dom} baskın"

        # [AH-1] AH split analizi
        ah_split = ah_split_hesapla(dom_ah)
        score   += ah_split.bonus_puan
        reason  += f" | AH-SPLIT:{ah_split.tip}(+{ah_split.bonus_puan:.1f})"

        # [AH-7] DNB özel bonusu
        if ah_split.tip == 'dnb':
            reason += " | DNB_BONUS"

        # [AH-2] AH hareket analizi
        if event_id:
            hareket, yon = ah_hareket.ah_hareketi(event_id, dom_ah)
            if yon == 'daralma' and abs(hareket) >= 0.25:
                score  += 2.5
                reason += f" | TEMPORAL_DECAY({hareket:+.2f}→ALIM)"
            elif yon == 'genisleme' and abs(hareket) >= 0.25:
                score  -= 1.5
                reason += f" | AH_GENİŞLİYOR({hareket:+.2f}→DİKKAT)"

        # [AH-3] RLM tespiti
        if event_id:
            rlm_var, rlm_msg, rlm_bonus = rlm_tespit(
                event_id, dom_ah, home_da_ratio)
            if rlm_var:
                score  += rlm_bonus
                reason += f" | {rlm_msg}"

        # [AH-4] SOT kalitesi
        kalite, kalite_msg, kalite_bonus = sot_kalitesi_hesapla(
            dom_stats.sot, dom_stats.da, dom_stats.gol, minute)
        score  += kalite_bonus
        reason += f" | SOT_KALİTE:{kalite_msg}"

        # [AH-5] Corner deficit bonus
        if dom_stats.korner < (away.korner if dom == "HOME" else home.korner):
            corner_fark = ((away.korner if dom == "HOME" else home.korner)
                           - dom_stats.korner)
            if corner_fark >= 3 and abs(dom_ah) <= 0.50:
                score  += 3.0
                reason += f" | SIGNAL_BETA(köşe_fark:{corner_fark})"

        return SignalResult(True, sig_type, round(score, 2), reason, {
            'dom': dom, 'da_ratio': round(dom_ratio, 2),
            'ah_split': ah_split.tip,
            'sot_kalite': round(kalite, 2)
        })


# ============================================================================
# İKİNCİ YARI GOL MODÜLÜ
# ============================================================================

class IY2Module:
    @staticmethod
    def check(minute: int, home: TeamStats, away: TeamStats,
              home_score: int, away_score: int,
              league_name: str = "") -> SignalResult:

        if 46 <= minute <= 65:
            window, sig_t, base = "ERKEN", SignalType.IY2_GOL, 5.0
        elif 76 <= minute <= 90:
            window, sig_t, base = "GEC",   SignalType.IY2_GEC, 4.0
        else:
            return SignalResult(False, None, 0.0, "Dakika dışı", {})

        diff = abs(home_score - away_score)
        if diff >= 3:
            return SignalResult(False, None, 0.0, "Rölanti ≥3", {})
        if diff >= 2 and minute >= 75:
            return SignalResult(False, None, 0.0, "Oyun yönetimi", {})
        if home.sot + away.sot > 15:
            return SignalResult(False, None, 0.0, "SOT doygunluğu", {})

        total_da  = home.da + away.da
        da_per_mn = total_da / minute if minute > 0 else 0

        if minute >= 60 and da_per_mn < 0.8:
            return SignalResult(False, None, 0.0,
                f"Rölanti: düşük momentum({da_per_mn:.2f})", {})
        if da_per_mn < 1.0:
            return SignalResult(False, None, 0.0, "DA düşük", {})

        score  = base + min(da_per_mn, 3.0)
        reason = f"İY2 {window}"
        if 48 <= minute <= 58:
            score  *= 2.0
            reason += " | KIRILMA (×2.0)"

        return SignalResult(True, sig_t, round(score, 2),
                            reason, {'da_per_min': round(da_per_mn, 2)})


# ============================================================================
# SİNYAL KONSENSÜS MOTORU
# ============================================================================

class SinyalKonsensus:
    @staticmethod
    def sec(sinyaller: List[SignalResult]) -> Optional[SignalResult]:
        gecerli = [s for s in sinyaller if s and s.valid]
        if not gecerli:
            return None
        en_iyi = max(gecerli, key=lambda s: s.score)
        if len(gecerli) > 1:
            en_iyi.score  = round(en_iyi.score * 1.15, 2)
            en_iyi.reason += f" | KONSENSÜS(×1.15, {len(gecerli)}modül)"
        return en_iyi


# ============================================================================
# VERİ KORUMA KATMANI
# ============================================================================

class VeriKorumaKatmani:
    def __init__(self):
        self.s_kod = {'S1':'SOT','S2':'Korner','S3':'TA','S4':'DA','SC':'Gol'}
        self.anomali = 0
        self.toplam  = 0

    def yeni_format_parse(self, stats):
        try:
            if 'corners' in stats and isinstance(stats.get('corners'), list):
                def _get(k, i):
                    return stats.get(k, ['0','0'])[i]
                ev  = {'S1':_get('on_target',0), 'S2':_get('corners',0),
                       'S3':_get('attacks',0),   'S4':_get('dangerous_attacks',0),
                       'SC':_get('goals',0)}
                dep = {'S1':_get('on_target',1), 'S2':_get('corners',1),
                       'S3':_get('attacks',1),   'S4':_get('dangerous_attacks',1),
                       'SC':_get('goals',1)}
                return ev, dep
        except Exception as e:
            logger.error(f"Parse hatası: {e}")
        return None

    def veri_cikart_guvenli(self, ev_v, dep_v):
        self.toplam += 1
        ters = {v: k for k, v in self.s_kod.items()}
        try:
            veri = {
                'ev_sot':    guvenli_int(ev_v.get(ters.get('SOT',    'S1'), 0)),
                'ev_korner': guvenli_int(ev_v.get(ters.get('Korner', 'S2'), 0)),
                'ev_ta':     guvenli_int(ev_v.get(ters.get('TA',     'S3'), 0)),
                'ev_da':     guvenli_int(ev_v.get(ters.get('DA',     'S4'), 0)),
                'ev_gol':    guvenli_int(ev_v.get(ters.get('Gol',    'SC'), 0)),
                'dep_sot':   guvenli_int(dep_v.get(ters.get('SOT',   'S1'), 0)),
                'dep_korner':guvenli_int(dep_v.get(ters.get('Korner','S2'), 0)),
                'dep_ta':    guvenli_int(dep_v.get(ters.get('TA',    'S3'), 0)),
                'dep_da':    guvenli_int(dep_v.get(ters.get('DA',    'S4'), 0)),
                'dep_gol':   guvenli_int(dep_v.get(ters.get('Gol',   'SC'), 0)),
            }
            ta  = veri['ev_ta']  + veri['dep_ta']
            da  = veri['ev_da']  + veri['dep_da']
            sot = veri['ev_sot'] + veri['dep_sot']
            gol = veri['ev_gol'] + veri['dep_gol']
            if ta < da or da < sot or sot < gol:
                self.anomali += 1
            return veri
        except Exception as e:
            logger.error(f"veri_cikart hatası: {e}")
            return None

    def istatistik(self):
        if self.toplam:
            logger.info(f"VeriKoruma: {self.toplam} kontrol, "
                        f"{self.anomali} anomali "
                        f"(%{self.anomali/self.toplam*100:.1f})")


veri_koruma = VeriKorumaKatmani()


def veri_cikart(ev_v, dep_v) -> dict:
    sonuc = veri_koruma.veri_cikart_guvenli(ev_v, dep_v)
    if sonuc is None:
        return {
            'ev_sot':0,'ev_korner':0,'ev_ta':0,'ev_da':0,'ev_gol':0,
            'dep_sot':0,'dep_korner':0,'dep_ta':0,'dep_da':0,'dep_gol':0
        }
    return sonuc


# ============================================================================
# NESİNE LİG KONTROLÜ
# ============================================================================

def nesine_lig_kontrolu(league_name: str, ev_adi: str, dep_adi: str) -> bool:
    full = f"{league_name} {ev_adi} {dep_adi}".lower()
    for pat in LeagueFilter.ALWAYS_REJECT:
        if re.search(pat, full):
            return False
    nesine = [
        'super lig','süper lig','premier league','championship',
        'la liga','bundesliga','2. bundesliga','serie a','serie b',
        'ligue 1','ligue 2','eredivisie','primeira liga',
        'champions league','europa league','conference league',
        'pro league','scottish premiership',
    ]
    return any(n in league_name.lower() for n in nesine)


# ============================================================================
# ADAPTİF DÖNGÜ SÜRESİ
# ============================================================================

def dongu_suresi_hesapla(maclar: list) -> int:
    for m in maclar:
        t = m.get('timer', {})
        dk = guvenli_int(t.get('tm', 0)) if isinstance(t, dict) else 0
        if (22 <= dk <= 38) or (46 <= dk <= 60):
            return 20
    return 60


# ============================================================================
# PUAN BARAJI
# ============================================================================

def puan_baraji_hesapla(dakika: int, league_name: str) -> float:
    c = LeagueFilter.get_league_multiplier(league_name)
    if c >= 1.5: return 7.0
    if c <= 0.8: return 6.0
    return 6.5


# ============================================================================
# AI ANALİZCİLER
# ============================================================================

class GrokAIAnalyzer:
    def __init__(self):
        self.api_key = GROK_API_KEY

    async def analiz_yap(self, mac_verisi: dict, session) -> Optional[str]:
        if not self.api_key:
            return None
        try:
            prompt = (
                f"Futbol analistiyim. Kısa sezgisel analiz (MAX 350 karakter):\n"
                f"MAÇ: {mac_verisi['ev_adi']} {mac_verisi['skor']} "
                f"{mac_verisi['dep_adi']} ({mac_verisi['dakika']}')\n"
                f"TA:{mac_verisi['ev_ta']}/{mac_verisi['dep_ta']} "
                f"DA:{mac_verisi['ev_da']}/{mac_verisi['dep_da']} "
                f"SOT:{mac_verisi['ev_sot']}/{mac_verisi['dep_sot']}\n"
                f"Sahte baskı? Kontra riski? +EV var mı?"
            )
            async with api_rate_limiter:
                async with session.post(
                    "https://api.x.ai/v1/chat/completions",
                    json={"model": "grok-beta",
                          "messages": [{"role": "user", "content": prompt}],
                          "temperature": 0.85, "max_tokens": 400},
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            if data.get('choices'):
                return data['choices'][0]['message']['content']
        except Exception as e:
            logger.debug(f"Grok hata: {e}")
        return None


class GeminiAIAnalyzer:
    def __init__(self):
        self.keys = [k for k in [GEMINI_API_KEY_1,
                                  GEMINI_API_KEY_2,
                                  GEMINI_API_KEY_3] if k]
        self.idx  = 0

    def _key(self):
        if not self.keys:
            return None
        k = self.keys[self.idx]
        self.idx = (self.idx + 1) % len(self.keys)
        return k

    async def analiz_yap(self, mac_verisi: dict, session) -> Optional[str]:
        key = self._key()
        if not key:
            return None
        try:
            prompt = (
                f"Kısa analiz (MAX 300 karakter): "
                f"{mac_verisi['ev_adi']} {mac_verisi['skor']} "
                f"{mac_verisi['dep_adi']} ({mac_verisi['dakika']}'). "
                f"TA:{mac_verisi['ev_ta']}/{mac_verisi['dep_ta']} "
                f"DA:{mac_verisi['ev_da']}/{mac_verisi['dep_da']}. "
                f"Sahte baskı? +EV?"
            )
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"gemini-2.0-flash:generateContent?key={key}")
            async with session.post(
                url,
                json={"contents":[{"parts":[{"text": prompt}]}],
                      "generationConfig":{"temperature":0.85,"maxOutputTokens":400}},
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
            if data.get('candidates'):
                return data['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            logger.debug(f"Gemini hata: {e}")
        return None


grok_ai   = GrokAIAnalyzer()
gemini_ai = GeminiAIAnalyzer()


async def ai_analiz_yap(mac_verisi: dict,
                        session) -> Tuple[Optional[str], Optional[str]]:
    if grok_ai.api_key:
        r = await grok_ai.analiz_yap(mac_verisi, session)
        if r:
            return r, "Grok"
    if gemini_ai.keys:
        r = await gemini_ai.analiz_yap(mac_verisi, session)
        if r:
            return r, "Gemini"
    return None, None


# ============================================================================
# ASIAN HANDICAP ÇEK — [AH-2] hareket kaydıyla birlikte
# ============================================================================

async def asian_handicap_cek(event_id: str, session) -> Optional[dict]:
    try:
        async with api_rate_limiter:
            async with session.get(
                f"https://api.betsapi.com/v1/event/odds"
                f"?token={BETSAPI_TOKEN}&event_id={event_id}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        if data.get('success') != 1:
            return None
        results = data.get('results', {})
        if not isinstance(results, dict):
            return None

        asian_data = None
        for key in ['1_2', 'asian_handicap', 'ah', 'handicap']:
            if key in results:
                asian_data = results[key]
                break
        if not asian_data:
            for key in results:
                if any(x in str(key).lower() for x in ['asian','handicap','ah']):
                    asian_data = results[key]
                    break
        if not asian_data:
            return None

        ev_h = dep_h = ev_o = dep_o = 0.0

        if isinstance(asian_data, list) and asian_data:
            lat = asian_data[0]
            if isinstance(lat, dict):
                h   = guvenli_float(lat.get('handicap', 0))
                ev_h  = h
                dep_h = -h
                ev_o  = guvenli_float(lat.get('home_od', 0))
                dep_o = guvenli_float(lat.get('away_od', 0))
        elif isinstance(asian_data, dict):
            if 'home' in asian_data:
                ev_h  = guvenli_float(asian_data['home'].get('handicap', 0))
                ev_o  = guvenli_float(asian_data['home'].get('odds', 0))
                dep_h = guvenli_float(asian_data['away'].get('handicap', 0))
                dep_o = guvenli_float(asian_data['away'].get('odds', 0))

        if ev_o > 0 and dep_o > 0:
            # [AH-2] Hareketi kaydet
            ah_hareket.kaydet(event_id, ev_h, dep_h, ev_o, dep_o)
            return {'ev_handicap': ev_h, 'dep_handicap': dep_h,
                    'ev_oran': ev_o,    'dep_oran': dep_o}
        return None

    except asyncio.TimeoutError:
        return None
    except Exception as e:
        logger.error(f"AH hatası: {e}")
        return None


# ============================================================================
# ANA ANALİZ MOTORU
# ============================================================================

async def mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk,
                        bot, session, event_id=None, league_name=""):
    try:
        v = veri_cikart(ev_v, dep_v)

        home_stats = TeamStats(ta=v['ev_ta'],  da=v['ev_da'],
                               sot=v['ev_sot'],  gol=v['ev_gol'],
                               korner=v.get('ev_korner', 0))
        away_stats = TeamStats(ta=v['dep_ta'], da=v['dep_da'],
                               sot=v['dep_sot'], gol=v['dep_gol'],
                               korner=v.get('dep_korner', 0))

        ev_gol     = home_stats.gol
        dep_gol    = away_stats.gol
        toplam_gol = ev_gol + dep_gol
        ta  = home_stats.ta  + away_stats.ta
        da  = home_stats.da  + away_stats.da
        sot = home_stats.sot + away_stats.sot

        # Veri kalitesi
        ok, errs = MatchDataProtection.validate_match_data(home_stats, away_stats)
        if not ok:
            logger.debug(f"Veri kalitesi: {errs}")
            return None

        # Global filtreler
        lig_ok, lig_r = LeagueFilter.check_league(league_name, ev_adi, dep_adi)
        if not lig_ok:
            return None
        if LeagueFilter.is_karantina(league_name):
            return None
        if abs(ev_gol - dep_gol) >= 3:
            return None

        skor_ok, skor_d, skor_bonus = skor_durumu_kontrol(ev_gol, dep_gol)
        if not skor_ok:
            return None

        # Tüm modülleri çalıştır
        sinyaller: List[SignalResult] = []

        if 15 <= dk <= 40:
            s = IYGolModule.check(dk, ev_gol, dep_gol,
                                  home_stats, away_stats, league_name)
            sinyaller.append(s)

        if (46 <= dk <= 65) or (76 <= dk <= 90):
            s = IY2Module.check(dk, home_stats, away_stats,
                                ev_gol, dep_gol, league_name)
            sinyaller.append(s)

        if 20 <= dk <= 80 and event_id:
            ah_data = await asian_handicap_cek(event_id, session)
            if ah_data:
                s = EvDepGolModule.check(
                    dk, home_stats, away_stats,
                    ah_data['ev_handicap'], ah_data['dep_handicap'],
                    league_name, event_id
                )
                sinyaller.append(s)

        sinyal = SinyalKonsensus.sec(sinyaller)
        if not sinyal:
            return None

        # Skor bonusu + lig çarpanı
        sinyal.score = round(sinyal.score + skor_bonus, 2)
        if skor_bonus != 0:
            sinyal.reason += f" | SKOR({skor_bonus:+.0f})"

        lig_c = LeagueFilter.get_league_multiplier(league_name)
        sinyal.score = round(sinyal.score * lig_c, 2)
        if lig_c != 1.0:
            sinyal.reason += f" | LİG(×{lig_c})"

        # Puan barajı
        baraji = puan_baraji_hesapla(dk, league_name)
        if sinyal.score < baraji:
            return None

        # Çift sinyal kontrolü
        if event_id and sinyal_gecmisi.zaten_gonderildi_mi(
                event_id, dk, sinyal.signal_type.value):
            return None

        sinyal_logger.info(
            f"{ev_adi} vs {dep_adi} | {dk}' | "
            f"{sinyal.signal_type.value} | Puan:{sinyal.score:.1f} | "
            f"{lig_c}x | {sinyal.reason}"
        )

        # AI analizi
        mac_v = {
            'ev_adi': ev_adi, 'dep_adi': dep_adi,
            'skor': skor, 'dakika': dk,
            'ta': ta, 'da': da, 'sot': sot, 'gol': toplam_gol,
            'ev_ta': v['ev_ta'],  'dep_ta': v['dep_ta'],
            'ev_da': v['ev_da'],  'dep_da': v['dep_da'],
            'ev_sot': v['ev_sot'],'dep_sot': v['dep_sot'],
            'ev_gol': ev_gol,     'dep_gol': dep_gol,
        }
        ai_analiz, ai_src = await ai_analiz_yap(mac_v, session)

        # Mesaj
        nesine = nesine_lig_kontrolu(league_name, ev_adi, dep_adi)
        nesine_str = "✅ Nesine'de OYNANMAKTADIR" if nesine else "ℹ️ Nesine'de yok"

        # AH split bilgisi mesaja ekle
        ah_split_str = ""
        if sinyal.details.get('ah_split'):
            ah_split_str = f"• AH Split: {sinyal.details['ah_split']}\n"

        mesaj = (
            f"💎 *SİNYAL — Puan: {sinyal.score:.1f}*\n"
            f"⚽ {ev_adi} {skor} {dep_adi}\n"
            f"🏆 {league_name}\n"
            f"⏱ {dk}' | 🎯 {sinyal.signal_type.value}\n"
            f"{'─'*32}\n"
            f"📊 *İstatistikler:*\n"
            f"• TA: {ta} (Ev:{v['ev_ta']}, Dep:{v['dep_ta']})\n"
            f"• DA: {da} (Ev:{v['ev_da']}, Dep:{v['dep_da']})\n"
            f"• SOT: {sot} (Ev:{v['ev_sot']}, Dep:{v['dep_sot']})\n"
            f"• Gol: {toplam_gol} (Ev:{ev_gol}, Dep:{dep_gol})\n"
            f"• Köşe: Ev:{v.get('ev_korner',0)}, Dep:{v.get('dep_korner',0)}\n"
            f"{ah_split_str}"
            f"{'─'*32}\n"
            f"🎯 *Sebep:* {sinyal.reason}\n"
        )

        if ai_analiz:
            mesaj += f"{'─'*32}\n🤖 *{ai_src} AI:*\n{ai_analiz}\n"

        mesaj += f"{'─'*32}\n{nesine_str}"

        if event_id:
            sinyal_gecmisi.kaydet(event_id, dk, sinyal.signal_type.value)

        return mesaj

    except Exception as e:
        logger.error(f"mac_analiz_et: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


# ============================================================================
# MAÇ İŞLEME
# ============================================================================

async def mac_isle(bot, mac_data: dict, session) -> Optional[str]:
    try:
        mac_id  = str(mac_data.get('id', ''))
        home_d  = mac_data.get('home', {})
        away_d  = mac_data.get('away', {})
        ev_adi  = home_d.get('name', '') if isinstance(home_d, dict) else ''
        dep_adi = away_d.get('name', '') if isinstance(away_d, dict) else ''
        if not ev_adi or not dep_adi:
            return None

        lig_d       = mac_data.get('league', {})
        league_name = lig_d.get('name', 'Unknown') if isinstance(lig_d, dict) else 'Unknown'
        timer       = mac_data.get('timer', {})
        dk          = guvenli_int(timer.get('tm', 0)) if isinstance(timer, dict) else 0
        skor        = mac_data.get('ss', '0-0') or '0-0'

        stats_data = None
        try:
            async with session.get(
                f"https://api.betsapi.com/v1/event/view"
                f"?token={BETSAPI_TOKEN}&event_id={mac_id}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    ed = await r.json()
                    if ed.get('success') == 1:
                        res = ed.get('results', [])
                        if res:
                            s = res[0].get('stats', {})
                            if s and isinstance(s, dict):
                                stats_data = s
        except Exception:
            pass

        if not stats_data:
            stats_data = mac_data.get('stats', {})

        if not stats_data or not isinstance(stats_data, dict):
            return None

        ev_v = dep_v = None
        if 'corners' in stats_data and isinstance(stats_data.get('corners'), list):
            r = VeriKorumaKatmani().yeni_format_parse(stats_data)
            if r:
                ev_v, dep_v = r

        if not ev_v or not dep_v:
            ev_v  = stats_data.get('1', {})
            dep_v = stats_data.get('2', {})

        if not ev_v or not dep_v:
            return None

        if (sum(1 for k in ev_v  if k.startswith('S')) == 0 or
                sum(1 for k in dep_v if k.startswith('S')) == 0):
            return None

        return await mac_analiz_et(
            ev_v, dep_v, ev_adi, dep_adi, skor, dk,
            bot, session, event_id=mac_id, league_name=league_name
        )

    except Exception as e:
        logger.error(f"mac_isle: {e}")
        return None


# ============================================================================
# TELEGRAM QUEUE
# ============================================================================

telegram_queue: asyncio.Queue = None


async def telegram_gondericisi(bot):
    while True:
        try:
            chat_id, mesaj = await telegram_queue.get()
            try:
                await bot.send_message(chat_id=chat_id, text=mesaj,
                                       parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Telegram: {e}")
            finally:
                telegram_queue.task_done()
            await asyncio.sleep(1.5)
        except Exception as e:
            logger.error(f"TG queue: {e}")
            await asyncio.sleep(1)


# ============================================================================
# ANA DÖNGÜ
# ============================================================================

async def ana_dongu():
    global telegram_queue
    telegram_queue = asyncio.Queue()
    asyncio.create_task(loop_monitor.monitor())

    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "🚀 *BOT V51 — AH MİKROYAPI ENTEGRASYONu*\n\n"
                "*V50 korunanlar (15 düzeltme):*\n"
                "✅ Lig çarpanları excel verisiyle kalibre\n"
                "✅ Skor filtresi toplam gol bazlı\n"
                "✅ DA ivmesi lig bazlı eşik\n"
                "✅ SQLite kalıcı sinyal geçmişi\n"
                "✅ Context manager düzeltmeleri\n"
                "✅ Telegram queue flood koruması\n"
                "✅ xG dakika normalizasyonu\n"
                "✅ Konsensüs motoru\n"
                "✅ Adaptif döngü süresi\n\n"
                "*V51 yeni özellikler (AH raporu):*\n"
                "🆕 [AH-1] AH split mekanizması\n"
                "🆕 [AH-2] AH hareket takibi + CLV\n"
                "🆕 [AH-3] Reverse Line Movement\n"
                "🆕 [AH-4] SOT kalitesi (xG per shot)\n"
                "🆕 [AH-5] Corner deficit Signal Beta\n"
                "🆕 [AH-6] UCL knockout çarpan ayarı\n"
                "🆕 [AH-7] DNB (0.0) özel bonusu\n"
                "🆕 [AH-8] Erken köşe oranı filtresi\n\n"
                "🎯 Hazır — sinyaller bekleniyor..."
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Bot başlatma: {e}")
        return

    asyncio.create_task(telegram_gondericisi(bot))

    async with aiohttp.ClientSession() as session:
        dongu = 0
        aktif_maclar: list = []

        while True:
            dongu += 1
            try:
                async with api_rate_limiter:
                    async with session.get(
                        f"https://api.betsapi.com/v1/events/inplay"
                        f"?sport_id=1&token={BETSAPI_TOKEN}",
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as resp:
                        if resp.status != 200:
                            await asyncio.sleep(60)
                            continue
                        data = await resp.json()

                aktif_maclar = esnek_liste_duzelt(data.get('results', []))
                logger.info(f"#{dongu} | {len(aktif_maclar)} canlı maç")

                async def isle(mac_data):
                    try:
                        mesaj = await mac_isle(bot, mac_data, session)
                        if mesaj:
                            await telegram_queue.put((CHAT_ID, mesaj))
                    except Exception as e:
                        logger.error(f"Isle wrapper: {e}")

                await asyncio.gather(
                    *[isle(m) for m in aktif_maclar],
                    return_exceptions=True
                )

                if dongu % 10 == 0:
                    s = api_rate_limiter.get_stats()
                    logger.info(f"Rate: {s['total']} istek, {s['throttled']} throttled")
                    veri_koruma.istatistik()

            except Exception as e:
                logger.error(f"Ana döngü: {e}")
                import traceback
                logger.error(traceback.format_exc())

            await asyncio.sleep(dongu_suresi_hesapla(aktif_maclar))


# ============================================================================
# GİRİŞ
# ============================================================================

if __name__ == "__main__":
    logger.info("🚀 Bot V51 Başlatılıyor...")
    logger.info(
        f"Telegram:{'✅' if TELEGRAM_TOKEN else '❌'} | "
        f"Chat:{'✅' if CHAT_ID else '❌'} | "
        f"BetsAPI:{'✅' if BETSAPI_TOKEN else '❌'}"
    )
    try:
        asyncio.run(ana_dongu())
    except KeyboardInterrupt:
        logger.info("Bot durduruldu")
    except Exception as e:
        logger.error(f"Kritik: {e}")
        import traceback
        logger.error(traceback.format_exc())
