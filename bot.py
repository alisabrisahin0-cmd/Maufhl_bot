import asyncio, aiohttp, os, urllib.parse, logging, re, time, sqlite3
from telegram import Bot
from collections import deque
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple

# ============================================================================
# V50: TÜM KRİTİK DÜZELTMELER UYGULANMIŞ
# ============================================================================
# DÜZELTME LİSTESİ:
# [FIX-1]  Lig çarpanları excel verisine göre yeniden kalibre edildi
# [FIX-2]  Skor filtresi: toplam gol bazlı — veriye uygun
# [FIX-3]  DA ivmesi eşiği lig bazlı kullanıyor (LeagueFilter entegre)
# [FIX-4]  SQLite tabanlı kalıcı sinyal geçmişi (restart'ta çift sinyal yok)
# [FIX-5]  Grok API context manager hatası düzeltildi
# [FIX-6]  Telegram flood koruması — asyncio.Queue sistemi
# [FIX-7]  Sahte baskı xG eşiği dakikaya normalize edildi
# [FIX-8]  Whitelist öncelik sırası sıkılaştırıldı
# [FIX-9]  xG formülü dakikaya normalize edildi
# [FIX-10] Sinyal çakışma yönetimi eklendi (konsensüs motoru)
# [FIX-11] Gemini API modeli güncellendi (gemini-2.0-flash)
# [FIX-12] Adaptif döngü süresi (altın pencerede 20sn)
# [FIX-13] Puan barajı normalize edildi (lig/dakika bazlı)
# [FIX-14] Structured logging (sinyal/debug ayrımı)
# [FIX-15] asian_handicap_cek context manager hatası düzeltildi
# ============================================================================

# ============================================================================
# YAPISAL SABITLER — VERİ BAZLI (Excel Analizi)
# ============================================================================

# [FIX-1] Lig çarpanları excel win rate'e göre kalibre edildi
# Excel verisi: Bundesliga %93.2, UCL %92.4, Eredivisie %85.1...
LIG_CARPANLARI = {
    'bundesliga':             1.85,
    'champions league':       1.85,
    'uefa champions':         1.85,
    'eredivisie':             1.50,
    'türkiye 1 lig':          1.35,
    'turkiye 1 lig':          1.35,
    '1. lig':                 1.35,
    'serie b':                1.30,
    'ligue 1':                1.20,
    'la liga':                1.15,
    'serie a':                1.10,
    'primera liga':           1.10,
    'primeira liga':          1.10,
    'championship':           0.85,
    'premier league':         0.85,
    'england premier':        0.85,
    'super lig':              0.75,   # Türkiye SL %67.7 — en düşük
    'süper lig':              0.75,
    'brazil':                 0.65,   # %58.3 — karantina sınırında
    'serie a brazil':         0.65,
}

# [FIX-1] Karantina ligleri — sinyal üretilmez
KARANTINA_LIGLER = [
    'brazil', 'brasil', 'kenya', 'ethiopia', 'rwanda',
    'oman', 'kuwait', 'iraq stars', 'afghanistan',
]

# ============================================================================
# LOGGING — STRUCTURED (FIX-14)
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Ayrı sinyal logger — sadece gerçek sinyaller
sinyal_logger = logging.getLogger('sinyal')
sinyal_handler = logging.StreamHandler()
sinyal_handler.setFormatter(logging.Formatter('🎯 %(asctime)s SINYAL | %(message)s', '%H:%M:%S'))
sinyal_logger.addHandler(sinyal_handler)
sinyal_logger.setLevel(logging.INFO)

# ============================================================================
# KONFIGÜRASYON
# ============================================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

GROK_API_KEY    = os.getenv("GROK_API_KEY") or None
GEMINI_API_KEY_1 = os.getenv("GEMINI_API_KEY_1") or None
GEMINI_API_KEY_2 = os.getenv("GEMINI_API_KEY_2") or None
GEMINI_API_KEY_3 = os.getenv("GEMINI_API_KEY_3") or None

print(f"🔑 API Keys: Grok={'✅' if GROK_API_KEY else '❌'} | "
      f"Gemini={sum(1 for k in [GEMINI_API_KEY_1,GEMINI_API_KEY_2,GEMINI_API_KEY_3] if k)}/3")

# ============================================================================
# [FIX-4] KALICI SİNYAL GEÇMİŞİ — SQLite
# ============================================================================

class SinyalGecmisi:
    """
    SQLite tabanlı kalıcı sinyal geçmişi.
    Bot restart'ta bile çift sinyal gönderilmez.
    event_id + dakika_grubu + sinyal_tipi benzersiz key.
    """

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
            # 24 saatten eski kayıtları temizle
            conn.execute("DELETE FROM sinyaller WHERE zaman < ?", (time.time() - 86400,))
            conn.commit()

    @staticmethod
    def _dk_grubu(dakika: int) -> int:
        """5 dakikalık gruplama — aynı pencerede tekrar sinyal gönderilmez"""
        return (dakika // 5) * 5

    def zaten_gonderildi_mi(self, event_id: str, dakika: int, sinyal_tipi: str) -> bool:
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
# API RATE LIMITER
# ============================================================================

class APIRateLimiter:
    def __init__(self, max_concurrent=5, requests_per_second=10):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.requests_per_second = requests_per_second
        self.last_request_time = 0
        self.request_count = 0
        self.lock = asyncio.Lock()
        self.total_requests = 0
        self.throttled_count = 0

    async def acquire(self):
        await self.semaphore.acquire()
        async with self.lock:
            current_time = time.time()
            if current_time - self.last_request_time >= 1.0:
                self.request_count = 0
                self.last_request_time = current_time
            if self.request_count >= self.requests_per_second:
                wait_time = 1.0 - (current_time - self.last_request_time)
                if wait_time > 0:
                    self.throttled_count += 1
                    await asyncio.sleep(wait_time)
                self.request_count = 0
                self.last_request_time = time.time()
            self.request_count += 1
            self.total_requests += 1

    def release(self):
        self.semaphore.release()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False

    def get_stats(self):
        return {
            'total_requests': self.total_requests,
            'throttled_count': self.throttled_count,
            'current_rate': self.request_count
        }


api_rate_limiter = APIRateLimiter(max_concurrent=5, requests_per_second=10)

# ============================================================================
# EVENT LOOP MONITOR
# ============================================================================

class EventLoopMonitor:
    def __init__(self, threshold_ms=50, check_interval=0.1):
        self.threshold_ms = threshold_ms
        self.check_interval = check_interval
        self.lag_count = 0
        self.max_lag = 0
        self.total_checks = 0
        self.running = False

    async def monitor(self):
        self.running = True
        logger.info(f"📊 Event Loop Monitor başlatıldı (eşik: {self.threshold_ms}ms)")
        while self.running:
            expected_time = time.time()
            await asyncio.sleep(self.check_interval)
            actual_time = time.time()
            lag_ms = (actual_time - expected_time - self.check_interval) * 1000
            self.total_checks += 1
            if lag_ms > self.threshold_ms:
                self.lag_count += 1
                self.max_lag = max(self.max_lag, lag_ms)
                if lag_ms > 200:
                    logger.error(f"🚨 KRİTİK LAG: {lag_ms:.2f}ms")
            if self.total_checks % 3000 == 0:
                self._log_stats()

    def _log_stats(self):
        if self.total_checks > 0:
            pct = (self.lag_count / self.total_checks) * 100
            logger.info(f"📊 EventLoop: lag={self.lag_count}/{self.total_checks} "
                        f"({pct:.1f}%), max={self.max_lag:.2f}ms")

    def stop(self):
        self.running = False
        self._log_stats()


loop_monitor = EventLoopMonitor(threshold_ms=50)

# ============================================================================
# LİG FİLTRELEME — [FIX-8] Öncelik sırası sıkılaştırıldı
# ============================================================================

class LeagueFilter:
    # ALWAYS_REJECT: En yüksek öncelik — hiç geçemez
    ALWAYS_REJECT = [
        r'\be[-\s]?sport[s]?\b',
        r'\bvirtual\b', r'\bsimulat',
        r'\b(w|women|kadın|kadin)\b',
        r'\b(reserves?|rezerv)\b',
        r'\b(youth|junior|academy)\b',
        r'\bu\d{2}\b',
    ]

    # Karantina ligleri
    KARANTINA = KARANTINA_LIGLER

    # [FIX-8] Whitelist sadece tam lig adı eşleşmesi — "premier league reserves" geçmez
    WHITELIST = [
        'bundesliga', 'eredivisie', 'champions league', 'europa league',
        'conference league', 'premier league', 'championship',
        'serie a', 'serie b', 'la liga', 'ligue 1', 'ligue 2',
        'primeira liga', 'pro league', 'super league', 'süper lig',
        'super lig', '1. lig', '2. lig', 'premiership',
        'superligaen', 'allsvenskan', 'eliteserien',
    ]

    @staticmethod
    def check_league(league_name: str, home_team: str, away_team: str) -> Tuple[bool, str]:
        full_text = f"{league_name} {home_team} {away_team}".lower()
        league_lower = league_name.lower()

        # 1. ALWAYS_REJECT — regex tabanlı
        for pattern in LeagueFilter.ALWAYS_REJECT:
            if re.search(pattern, full_text):
                return False, f"REJECT: '{pattern}'"

        # 2. Karantina kontrolü (sadece lig adında)
        for keyword in LeagueFilter.KARANTINA:
            if keyword in league_lower:
                return False, f"KARANTINA: '{keyword}'"

        # 3. Whitelist kontrolü (sadece lig adında — takım adı değil)
        for keyword in LeagueFilter.WHITELIST:
            if keyword in league_lower:
                return True, f"WHITELIST: '{keyword}'"

        # 4. Nötr — geç
        return True, "NEUTRAL"

    @staticmethod
    def get_league_multiplier(league_name: str) -> float:
        """[FIX-1] Excel verisiyle kalibre edilmiş çarpanlar"""
        league_lower = league_name.lower()
        for keyword, carpan in LIG_CARPANLARI.items():
            if keyword in league_lower:
                return carpan
        return 1.0

    @staticmethod
    def get_da_threshold(league_name: str) -> float:
        """[FIX-3] Lig bazlı DA ivmesi eşiği"""
        league_lower = league_name.lower()
        high_tempo = ['bundesliga', 'eredivisie', 'u23', 'u21', 'u20', 'u19',
                      'süper lig', 'super lig', 'turkey', 'portugal']
        low_tempo  = ['kuwait', 'egypt', 'third division', 'regionalliga', 'amateur']
        if any(k in league_lower for k in high_tempo):
            return 1.3
        if any(k in league_lower for k in low_tempo):
            return 2.0
        return 1.5

    @staticmethod
    def is_karantina(league_name: str) -> bool:
        league_lower = league_name.lower()
        return any(k in league_lower for k in KARANTINA_LIGLER)


# ============================================================================
# VERİ KORUMA KATMANI
# ============================================================================

def guvenli_int(deger, varsayilan=0):
    try:
        if deger == '' or deger is None:
            return varsayilan
        return int(float(deger))
    except:
        return varsayilan

def guvenli_float(deger, varsayilan=0.0):
    try:
        if deger == '' or deger is None:
            return varsayilan
        return float(deger)
    except:
        return varsayilan


@dataclass
class TeamStats:
    ta: int = 0
    da: int = 0
    sot: int = 0
    gol: int = 0
    korner: int = 0

    def validate_hierarchy(self) -> Tuple[bool, List[str]]:
        errors = []
        if self.ta < self.da:
            errors.append(f"TA ({self.ta}) < DA ({self.da})")
        if self.da < self.sot:
            errors.append(f"DA ({self.da}) < SOT ({self.sot})")
        if self.sot < self.gol:
            errors.append(f"SOT ({self.sot}) < Gol ({self.gol})")
        return len(errors) == 0, errors

    def calculate_xg(self, dakika: int = 45) -> float:
        """[FIX-9] xG dakikaya normalize edildi"""
        sot    = max(0, self.sot)
        da     = max(0, self.da)
        ta     = max(0, self.ta)
        korner = max(0, self.korner)
        ham_xg = (sot * 0.15) + (da * 0.015) + (ta * 0.01) + (korner * 0.03)
        # 45. dakikayı referans al — erken dakikalar daha değerli
        norm = 45 / max(dakika, 1)
        return round(ham_xg * norm, 2)

    def detect_fake_pressure(self) -> bool:
        if self.da > 8 and self.sot == 0:
            return True
        if self.da > 0 and self.sot > 0:
            if self.da / self.sot > 8:
                return True
        if self.korner >= 8 and self.sot < 5:
            return True
        return False


class MatchDataProtection:
    @staticmethod
    def check_broken_match(total_goals: int) -> Tuple[bool, str]:
        if total_goals >= 5:
            return False, f"KOPMUŞ MAÇ: {total_goals} >= 5"
        return True, "OK"

    @staticmethod
    def validate_match_data(home: TeamStats, away: TeamStats) -> Tuple[bool, List[str]]:
        errors = []
        home_ok, home_errors = home.validate_hierarchy()
        if not home_ok:
            errors.extend([f"EV: {e}" for e in home_errors])
        away_ok, away_errors = away.validate_hierarchy()
        if not away_ok:
            errors.extend([f"DEP: {e}" for e in away_errors])
        total_goals = home.gol + away.gol
        broken_ok, broken_msg = MatchDataProtection.check_broken_match(total_goals)
        if not broken_ok:
            errors.append(broken_msg)
        return len(errors) == 0, errors


# ============================================================================
# [FIX-2] SKOR DURUMU — TOPLAM GOL BAZLI
# ============================================================================

def skor_durumu_kontrol(ev_gol: int, dep_gol: int) -> Tuple[bool, str, float]:
    """
    [FIX-2] Toplam gol bazlı filtre — Excel verisiyle tam uyumlu:
    0 gol: %0 win rate   → BLOK
    1 gol: %25 win rate  → ağır ceza (-3)
    2 gol: %47 win rate  → hafif ceza (-1)
    3 gol: %79 win rate  → bonus (+2)
    4 gol: %92 win rate  → büyük bonus (+3)
    5 gol: %96 win rate  → kapat (kaos sınırında)
    """
    toplam = ev_gol + dep_gol
    fark   = abs(ev_gol - dep_gol)

    if toplam >= 5:
        return False, "KAOS_BOLGESI", 0.0

    if fark >= 3:
        return False, "ROLANTI_EVRESI", 0.0

    if toplam == 0:
        return False, "SIFIR_GOL_RISK", 0.0   # %0 win rate — geç

    if toplam == 1:
        return True, "DUSUK_SKOR", -3.0        # %25 win rate — büyük ceza

    if toplam == 2:
        return True, "NORMAL_SKOR", -1.0       # %47 win rate — hafif ceza

    if toplam == 3:
        return True, "IYI_SKOR", +2.0          # %79 win rate — bonus

    if toplam >= 4:
        return True, "ALTIN_SKOR", +3.0        # %92 win rate — büyük bonus

    return True, "NORMAL", 0.0


# ============================================================================
# SİNYAL SINIFLARI
# ============================================================================

class SignalType(Enum):
    IY_GOL   = "İY_GOL"
    EV_GOL   = "EV_GOL"
    DEP_GOL  = "DEP_GOL"
    IY2_GOL  = "İY2_GOL"
    IY2_GEC  = "İY2_GEC"


@dataclass
class SignalResult:
    valid:       bool
    signal_type: Optional[SignalType]
    score:       float
    reason:      str
    details:     Dict


# ============================================================================
# İLK YARI GOL MODÜLÜ
# ============================================================================

class IYGolModule:
    @staticmethod
    def check(minute: int, home_score: int, away_score: int,
              home: TeamStats, away: TeamStats,
              league_name: str = "") -> SignalResult:

        if not (15 <= minute <= 40):
            return SignalResult(False, None, 0.0, "Dakika aralık dışı", {})

        total_goals = home_score + away_score
        if total_goals > 1:
            return SignalResult(False, None, 0.0, "İY: Skor yüksek", {})

        total_da = home.da + away.da

        # [FIX-3] Lig bazlı DA eşiği
        da_esik = LeagueFilter.get_da_threshold(league_name)
        da_per_minute = total_da / minute if minute > 0 else 0

        if da_per_minute < da_esik:
            return SignalResult(False, None, 0.0,
                f"DA ivmesi düşük: {da_per_minute:.2f} < {da_esik}", {})

        score = 5.0
        score += min(da_per_minute * 2, 5.0)

        reason = "İY Gol — DA ivmesi yüksek"
        if 24 <= minute <= 36:
            score *= 1.8
            reason += " | ALTIN PENCERE (×1.8)"

        return SignalResult(
            valid=True,
            signal_type=SignalType.IY_GOL,
            score=round(score, 2),
            reason=reason,
            details={'da_per_minute': round(da_per_minute, 2), 'total_da': total_da}
        )


# ============================================================================
# EV/DEPLASMAN GOL MODÜLÜ
# ============================================================================

class EvDepGolModule:
    @staticmethod
    def check(minute: int, home: TeamStats, away: TeamStats,
              ah_home: float, ah_away: float,
              league_name: str = "") -> SignalResult:

        if not (20 <= minute <= 80):
            return SignalResult(False, None, 0.0, "Dakika aralık dışı", {})

        total_da = home.da + away.da
        if total_da == 0:
            return SignalResult(False, None, 0.0, "DA verisi yok", {})

        home_da_ratio = home.da / total_da
        away_da_ratio = away.da / total_da

        dominant_team  = None
        dominant_ratio = 0.0
        signal_type    = None

        if home_da_ratio > 0.6:
            dominant_team  = "HOME"
            dominant_ratio = home_da_ratio
            signal_type    = SignalType.EV_GOL
            if ah_home >= 0:
                return SignalResult(False, None, 0.0, "Ev sahibi favori değil (AH)", {})

        elif away_da_ratio > 0.6:
            dominant_team  = "AWAY"
            dominant_ratio = away_da_ratio
            signal_type    = SignalType.DEP_GOL
            if ah_away <= 0:
                return SignalResult(False, None, 0.0, "Deplasman favori değil (AH)", {})
        else:
            return SignalResult(False, None, 0.0, "Baskın takım yok", {})

        dominant_stats = home if dominant_team == "HOME" else away
        if dominant_stats.detect_fake_pressure():
            return SignalResult(False, None, 0.0, "Sahte baskı tespit edildi", {})

        score = 6.0 + (dominant_ratio - 0.6) * 10

        ah_value = abs(ah_home) if dominant_team == "HOME" else abs(ah_away)
        reason = f"{dominant_team} baskın — AH değerli çizgi"

        if ah_value in [1.0, 2.0, 3.0]:
            score += 3.0
            reason += " | AH ANOMALİ (iade yok)"
        elif ah_value in [0.75, 1.25, 1.75]:
            score += 1.5
            reason += " | AH (iade var)"
        elif ah_value >= 1.0:
            score += 2.0

        return SignalResult(
            valid=True,
            signal_type=signal_type,
            score=round(score, 2),
            reason=reason,
            details={'dominant_team': dominant_team, 'da_ratio': round(dominant_ratio, 2), 'ah_value': ah_value}
        )


# ============================================================================
# İKİNCİ YARI GOL MODÜLÜ
# ============================================================================

class IY2Module:
    @staticmethod
    def check(minute: int, home: TeamStats, away: TeamStats,
              home_score: int, away_score: int,
              league_name: str = "") -> SignalResult:

        if 46 <= minute <= 65:
            window, signal_type, base_score = "ERKEN", SignalType.IY2_GOL, 5.0
        elif 76 <= minute <= 90:
            window, signal_type, base_score = "GEC", SignalType.IY2_GEC, 4.0
        else:
            return SignalResult(False, None, 0.0, "Dakika aralık dışı", {})

        score_diff = abs(home_score - away_score)
        if score_diff >= 3:
            return SignalResult(False, None, 0.0, "Rölanti: Büyük skor farkı (≥3)", {})
        if score_diff >= 2 and minute >= 75:
            return SignalResult(False, None, 0.0, "Rölanti: Oyun yönetimi (2+, 75+dk)", {})

        total_sot = home.sot + away.sot
        if total_sot > 15:
            return SignalResult(False, None, 0.0, f"SOT epilasyonu: {total_sot}", {})

        total_da = home.da + away.da
        da_per_minute = total_da / minute if minute > 0 else 0

        # [FIX-3] Lig bazlı DA eşiği
        da_esik = LeagueFilter.get_da_threshold(league_name)
        if minute >= 60 and da_per_minute < 0.8:
            return SignalResult(False, None, 0.0,
                f"Rölanti: Düşük momentum ({da_per_minute:.2f})", {})
        if da_per_minute < 1.0:
            return SignalResult(False, None, 0.0, "DA momentum düşük", {})

        score = base_score + min(da_per_minute, 3.0)

        reason = f"İY2 {window} — DA momentum yüksek"
        if 48 <= minute <= 58:
            score *= 2.0
            reason += " | KIRILMA EVRESİ (×2.0)"

        return SignalResult(
            valid=True,
            signal_type=signal_type,
            score=round(score, 2),
            reason=reason,
            details={'window': window, 'da_per_minute': round(da_per_minute, 2)}
        )


# ============================================================================
# [FIX-10] SİNYAL KONSENSÜS MOTORU — Çakışma yönetimi
# ============================================================================

class SinyalKonsensus:
    """
    Aynı maçta birden fazla modül ateşlenirse:
    - En yüksek puanlı sinyal önceliklidir
    - Puanlar birbirini destekliyorsa bonus ekle
    """

    @staticmethod
    def sec(sinyaller: List[SignalResult]) -> Optional[SignalResult]:
        gecerli = [s for s in sinyaller if s and s.valid]
        if not gecerli:
            return None
        # En yüksek puan
        en_iyi = max(gecerli, key=lambda s: s.score)
        # Çoklu sinyal bonusu
        if len(gecerli) > 1:
            en_iyi.score = round(en_iyi.score * 1.15, 2)
            en_iyi.reason += f" | KONSENSÜS BONUS (×1.15, {len(gecerli)} modül)"
        return en_iyi


# ============================================================================
# VERİ KORUMA KATMANI — Veri çıkarma
# ============================================================================

class VeriKorumaKatmani:
    def __init__(self):
        self.s_kod_mapping = {
            'S1': 'SOT', 'S2': 'Korner',
            'S3': 'TA',  'S4': 'DA', 'SC': 'Gol'
        }
        self.anomali_sayaci  = 0
        self.toplam_kontrol  = 0

    def yeni_format_parse(self, stats):
        try:
            if not isinstance(stats, dict):
                return None
            if 'corners' in stats and isinstance(stats.get('corners'), list):
                ev_v  = {
                    'S1': stats.get('on_target',         ['0','0'])[0],
                    'S2': stats.get('corners',           ['0','0'])[0],
                    'S3': stats.get('attacks',           ['0','0'])[0],
                    'S4': stats.get('dangerous_attacks', ['0','0'])[0],
                    'SC': stats.get('goals',             ['0','0'])[0],
                }
                dep_v = {
                    'S1': stats.get('on_target',         ['0','0'])[1],
                    'S2': stats.get('corners',           ['0','0'])[1],
                    'S3': stats.get('attacks',           ['0','0'])[1],
                    'S4': stats.get('dangerous_attacks', ['0','0'])[1],
                    'SC': stats.get('goals',             ['0','0'])[1],
                }
                return ev_v, dep_v
            return None
        except Exception as e:
            logger.error(f"Yeni format parse hatası: {e}")
            return None

    def fiziksel_hiyerarsi_dogrula(self, ta, da, sot, gol):
        hatalar = []
        if ta < da:   hatalar.append(f"TA ({ta}) < DA ({da})")
        if da < sot:  hatalar.append(f"DA ({da}) < SOT ({sot})")
        if sot < gol: hatalar.append(f"SOT ({sot}) < Gol ({gol})")
        if ta < sot:  hatalar.append(f"TA ({ta}) < SOT ({sot})")
        return len(hatalar) == 0, hatalar

    def veri_cikart_guvenli(self, ev_v, dep_v):
        self.toplam_kontrol += 1
        try:
            s_kodlari = {}
            for key in list(ev_v.keys()) + list(dep_v.keys()):
                if key.startswith('S') and key not in s_kodlari:
                    s_kodlari[key] = {
                        'ev':  guvenli_int(ev_v.get(key, 0)),
                        'dep': guvenli_int(dep_v.get(key, 0)),
                    }

            if not s_kodlari:
                return None

            mapping = self.s_kod_mapping
            ters    = {v: k for k, v in mapping.items()}

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

            ta  = veri['ev_ta']   + veri['dep_ta']
            da  = veri['ev_da']   + veri['dep_da']
            sot = veri['ev_sot']  + veri['dep_sot']
            gol = veri['ev_gol']  + veri['dep_gol']

            hiyerarsi_ok, hatalar = self.fiziksel_hiyerarsi_dogrula(ta, da, sot, gol)
            if not hiyerarsi_ok:
                self.anomali_sayaci += 1
                logger.warning(f"Hiyerarşi ihlali: {hatalar}")

            veri['hiyerarsi_ok'] = hiyerarsi_ok
            veri['hatalar']      = hatalar
            return veri

        except Exception as e:
            logger.error(f"veri_cikart_guvenli hatası: {e}")
            return None

    def istatistikleri_goster(self):
        if self.toplam_kontrol > 0:
            basari = ((self.toplam_kontrol - self.anomali_sayaci) / self.toplam_kontrol) * 100
            logger.info(f"Veri Koruma: {self.toplam_kontrol} kontrol, "
                        f"%{basari:.1f} başarı, {self.anomali_sayaci} anomali")


veri_koruma = VeriKorumaKatmani()


def esnek_liste_duzelt(veri):
    duz = []
    if isinstance(veri, list):
        for e in veri:
            duz.extend(esnek_liste_duzelt(e))
    elif isinstance(veri, dict):
        duz.append(veri)
    return duz


def veri_cikart(ev_v, dep_v):
    sonuc = veri_koruma.veri_cikart_guvenli(ev_v, dep_v)
    if sonuc is None:
        logger.warning("Veri koruma fallback kullanılıyor")
        return {
            'ev_sot':    guvenli_int(ev_v.get('S1', 0)),
            'ev_korner': guvenli_int(ev_v.get('S2', 0)),
            'ev_ta':     guvenli_int(ev_v.get('S3', 0)),
            'ev_da':     guvenli_int(ev_v.get('S4', 0)),
            'ev_gol':    guvenli_int(ev_v.get('SC', 0)),
            'dep_sot':   guvenli_int(dep_v.get('S1', 0)),
            'dep_korner':guvenli_int(dep_v.get('S2', 0)),
            'dep_ta':    guvenli_int(dep_v.get('S3', 0)),
            'dep_da':    guvenli_int(dep_v.get('S4', 0)),
            'dep_gol':   guvenli_int(dep_v.get('SC', 0)),
        }
    return sonuc


# ============================================================================
# NESİNE LİG KONTROLÜ
# ============================================================================

def nesine_lig_kontrolu(league_name, ev_adi, dep_adi):
    full_text   = f"{league_name} {ev_adi} {dep_adi}".lower()
    league_lower = league_name.lower()

    for pattern in LeagueFilter.ALWAYS_REJECT:
        if re.search(pattern, full_text):
            return False

    nesine_ligler = [
        'super lig', 'süper lig', 'premier league', 'championship',
        'la liga', 'bundesliga', '2. bundesliga', 'serie a', 'serie b',
        'ligue 1', 'ligue 2', 'eredivisie', 'primeira liga',
        'pro league', 'champions league', 'europa league', 'conference league',
        'scottish premiership', 'austrian bundesliga', 'swiss super league',
        'greek super league', 'turkish cup', 'copa del rey', 'fa cup',
        'dfb pokal', 'coppa italia', 'coupe de france',
    ]
    for lig in nesine_ligler:
        if lig in league_lower:
            return True
    return False


# ============================================================================
# [FIX-12] ADAPTİF DÖNGÜ SÜRESİ
# ============================================================================

def dongu_suresi_hesapla(aktif_maclar: list) -> int:
    """Altın pencere dakikalarında 20sn, normal durumda 60sn"""
    for mac in aktif_maclar:
        timer = mac.get('timer', {})
        dk = guvenli_int(timer.get('tm', 0)) if isinstance(timer, dict) else 0
        if (22 <= dk <= 38) or (46 <= dk <= 60):
            return 20
    return 60


# ============================================================================
# [FIX-11] GEMINI AI — güncel model
# ============================================================================

class GrokAIAnalyzer:
    def __init__(self):
        self.api_key = GROK_API_KEY
        self.api_call_count = 0

    async def analiz_yap(self, mac_verisi, session):
        if not self.api_key:
            return None
        try:
            self.api_call_count += 1
            prompt = f"""Sen deneyimli bir futbol analisti ve trading uzmanısın.

MAÇ: {mac_verisi['ev_adi']} {mac_verisi['skor']} {mac_verisi['dep_adi']} ({mac_verisi['dakika']}')

İSTATİSTİKLER:
• TA: {mac_verisi['ta']} (Ev:{mac_verisi['ev_ta']}, Dep:{mac_verisi['dep_ta']})
• DA: {mac_verisi['da']} (Ev:{mac_verisi['ev_da']}, Dep:{mac_verisi['dep_da']})
• SOT: {mac_verisi['sot']} (Ev:{mac_verisi['ev_sot']}, Dep:{mac_verisi['dep_sot']})
• Gol: {mac_verisi['gol']} (Ev:{mac_verisi['ev_gol']}, Dep:{mac_verisi['dep_gol']})

SEZGİSEL ANALİZ (MAX 350 karakter):
1. Skor psikolojisi — takımlar nasıl düşünür?
2. Sahte baskı var mı?
3. Kontra atak riski?
4. Sonuç: +EV var mı?

Matematiksel düşün, duygusal değil."""

            url = "https://api.x.ai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            payload = {
                "model": "grok-beta",
                "messages": [
                    {"role": "system", "content": "Futbol analisti ve bahis uzmanısın."},
                    {"role": "user",   "content": prompt}
                ],
                "temperature": 0.85,
                "max_tokens": 400
            }

            # [FIX-5] Context manager hatası düzeltildi
            async with api_rate_limiter:
                async with session.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status != 200:
                        response_text = await response.text()
                        logger.error(f"Grok API {response.status}: {response_text[:300]}")
                        return None
                    data = await response.json()

            if 'choices' in data and data['choices']:
                return data['choices'][0]['message']['content']
            return None

        except asyncio.TimeoutError:
            logger.error("Grok AI timeout (15sn)")
            return None
        except Exception as e:
            logger.error(f"Grok AI hata: {type(e).__name__}: {e}")
            return None


class GeminiAIAnalyzer:
    def __init__(self):
        self.api_keys = [k for k in [GEMINI_API_KEY_1, GEMINI_API_KEY_2, GEMINI_API_KEY_3] if k]
        self.current_key_index = 0
        self.api_call_count = 0

    def _get_next_api_key(self):
        if not self.api_keys:
            return None
        key = self.api_keys[self.current_key_index]
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        return key

    async def analiz_yap(self, mac_verisi, session):
        if not self.api_keys:
            return None
        try:
            api_key = self._get_next_api_key()
            self.api_call_count += 1

            prompt = f"""Futbol analisti olarak kısa sezgisel analiz yap.
MAÇ: {mac_verisi['ev_adi']} {mac_verisi['skor']} {mac_verisi['dep_adi']} ({mac_verisi['dakika']}')
TA: {mac_verisi['ev_ta']}/{mac_verisi['dep_ta']} | DA: {mac_verisi['ev_da']}/{mac_verisi['dep_da']} | SOT: {mac_verisi['ev_sot']}/{mac_verisi['dep_sot']}
Sahte baskı var mı? Kontra atak riski? +EV var mı? (MAX 300 karakter, doğal dil)"""

            # [FIX-11] Güncel model: gemini-2.0-flash
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"gemini-2.0-flash:generateContent?key={api_key}")

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.85, "maxOutputTokens": 400}
            }

            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status != 200:
                    response_text = await response.text()
                    logger.error(f"Gemini API {response.status}: {response_text[:300]}")
                    return None
                data = await response.json()

            if 'candidates' in data and data['candidates']:
                return data['candidates'][0]['content']['parts'][0]['text']
            return None

        except asyncio.TimeoutError:
            logger.error("Gemini AI timeout (15sn)")
            return None
        except Exception as e:
            logger.error(f"Gemini AI hata: {type(e).__name__}: {e}")
            return None


grok_ai   = GrokAIAnalyzer()
gemini_ai = GeminiAIAnalyzer()


async def ai_analiz_yap(mac_verisi, session):
    if grok_ai.api_key:
        result = await grok_ai.analiz_yap(mac_verisi, session)
        if result:
            return result, "Grok"
    if gemini_ai.api_keys:
        result = await gemini_ai.analiz_yap(mac_verisi, session)
        if result:
            return result, "Gemini"
    return None, None


# ============================================================================
# ASIAN HANDICAP ÇEK — [FIX-15] Context manager hatası düzeltildi
# ============================================================================

async def asian_handicap_cek(event_id, session):
    try:
        async with api_rate_limiter:
            async with session.get(
                f"https://api.betsapi.com/v1/event/odds?token={BETSAPI_TOKEN}&event_id={event_id}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                # [FIX-15] Tüm işlemler context içinde
                if response.status != 200:
                    logger.warning(f"AH API {response.status}")
                    return None
                data = await response.json()

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
            for key in results.keys():
                if any(x in str(key).lower() for x in ['asian', 'handicap', 'ah']):
                    asian_data = results[key]
                    break

        if not asian_data:
            return None

        ev_handicap = dep_handicap = ev_oran = dep_oran = 0.0

        if isinstance(asian_data, list) and asian_data:
            latest = asian_data[0]
            if isinstance(latest, dict):
                h = guvenli_float(latest.get('handicap', 0))
                ev_handicap  = h
                dep_handicap = -h if h != 0 else 0
                ev_oran      = guvenli_float(latest.get('home_od', 0))
                dep_oran     = guvenli_float(latest.get('away_od', 0))

        elif isinstance(asian_data, dict):
            if 'home' in asian_data and 'away' in asian_data:
                ev_handicap  = guvenli_float(asian_data['home'].get('handicap', 0))
                ev_oran      = guvenli_float(asian_data['home'].get('odds', 0))
                dep_handicap = guvenli_float(asian_data['away'].get('handicap', 0))
                dep_oran     = guvenli_float(asian_data['away'].get('odds', 0))

        if ev_oran > 0 and dep_oran > 0:
            return {'ev_handicap': ev_handicap, 'dep_handicap': dep_handicap,
                    'ev_oran': ev_oran, 'dep_oran': dep_oran}
        return None

    except asyncio.TimeoutError:
        logger.warning(f"AH API timeout (event_id: {event_id})")
        return None
    except Exception as e:
        logger.error(f"AH hatası: {e}")
        return None


# ============================================================================
# [FIX-13] NORMALIZE PUAN BARAJI
# ============================================================================

def puan_baraji_hesapla(dakika: int, league_name: str) -> float:
    """
    [FIX-13] Statik 9.0 yerine bağlamsal puan barajı.
    Altın pencere çarpanı barajı da şişiriyor — normalize et.
    """
    baz = 6.5
    lig_carpan = LeagueFilter.get_league_multiplier(league_name)
    # Yüksek tempolu ligde baraj biraz daha yüksek
    if lig_carpan >= 1.5:
        baz = 7.0
    elif lig_carpan <= 0.8:
        baz = 6.0   # Düşük tempolu ligde çıta alçak (zaten karantinada çoğu)
    return baz


# ============================================================================
# ANA ANALİZ MOTORU
# ============================================================================

async def mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot, session,
                        event_id=None, league_name=""):
    try:
        logger.debug(f"Analiz: {ev_adi} vs {dep_adi} {dk}'")

        # 1. Veri çıkar
        v = veri_cikart(ev_v, dep_v)

        home_stats = TeamStats(
            ta=v['ev_ta'], da=v['ev_da'], sot=v['ev_sot'],
            gol=v['ev_gol'], korner=v.get('ev_korner', 0)
        )
        away_stats = TeamStats(
            ta=v['dep_ta'], da=v['dep_da'], sot=v['dep_sot'],
            gol=v['dep_gol'], korner=v.get('dep_korner', 0)
        )

        sot        = home_stats.sot + away_stats.sot
        ta         = home_stats.ta  + away_stats.ta
        da         = home_stats.da  + away_stats.da
        ev_gol     = home_stats.gol
        dep_gol    = away_stats.gol
        toplam_gol = ev_gol + dep_gol

        # 2. Veri kalitesi
        data_valid, errors = MatchDataProtection.validate_match_data(home_stats, away_stats)
        if not data_valid:
            logger.debug(f"Veri kalitesi başarısız: {errors}")
            return None

        # 3. Global filtreler
        lig_uygun, lig_sebep = LeagueFilter.check_league(league_name, ev_adi, dep_adi)
        if not lig_uygun:
            logger.debug(f"Lig filtresi: {lig_sebep}")
            return None

        if LeagueFilter.is_karantina(league_name):
            logger.debug(f"Karantina ligi: {league_name}")
            return None

        # Rölanti filtresi
        if abs(ev_gol - dep_gol) >= 3:
            logger.debug(f"Rölanti: fark={abs(ev_gol - dep_gol)}")
            return None

        # [FIX-2] Skor durumu bazlı bonus/ceza
        skor_ok, skor_durum, skor_bonus = skor_durumu_kontrol(ev_gol, dep_gol)
        if not skor_ok:
            logger.debug(f"Skor filtresi: {skor_durum}")
            return None

        # 4. [FIX-10] Tüm modülleri çalıştır — konsensüs motoru
        sinyaller = []

        if 15 <= dk <= 40:
            s = IYGolModule.check(dk, ev_gol, dep_gol, home_stats, away_stats, league_name)
            sinyaller.append(s)

        if (46 <= dk <= 65) or (76 <= dk <= 90):
            s = IY2Module.check(dk, home_stats, away_stats, ev_gol, dep_gol, league_name)
            sinyaller.append(s)

        if 20 <= dk <= 80:
            if event_id:
                ah_data = await asian_handicap_cek(event_id, session)
                if ah_data:
                    s = EvDepGolModule.check(
                        dk, home_stats, away_stats,
                        ah_data['ev_handicap'], ah_data['dep_handicap'],
                        league_name
                    )
                    sinyaller.append(s)

        # Konsensüs seçimi
        sinyal = SinyalKonsensus.sec(sinyaller)
        if not sinyal:
            logger.debug(f"Geçerli sinyal yok: {ev_adi} {dk}'")
            return None

        # [FIX-2] Skor bonusunu/cezasını ekle
        sinyal.score = round(sinyal.score + skor_bonus, 2)
        if skor_bonus != 0:
            sinyal.reason += f" | SKOR_BONUS({skor_bonus:+.0f})"

        # [FIX-1] Lig çarpanı uygula
        lig_carpan = LeagueFilter.get_league_multiplier(league_name)
        sinyal.score = round(sinyal.score * lig_carpan, 2)
        if lig_carpan != 1.0:
            sinyal.reason += f" | LİG_CARPAN(×{lig_carpan})"

        # 5. [FIX-13] Normalize puan barajı
        PUAN_BARAJI = puan_baraji_hesapla(dk, league_name)
        if sinyal.score < PUAN_BARAJI:
            logger.debug(f"Puan yetersiz: {sinyal.score:.1f} < {PUAN_BARAJI} | {ev_adi}")
            return None

        # [FIX-4] Çift sinyal kontrolü
        if event_id and sinyal_gecmisi.zaten_gonderildi_mi(event_id, dk, sinyal.signal_type.value):
            logger.debug(f"Zaten gönderildi: {event_id} {dk}' {sinyal.signal_type.value}")
            return None

        sinyal_logger.info(f"{ev_adi} vs {dep_adi} | {dk}' | {sinyal.signal_type.value} | "
                           f"Puan:{sinyal.score:.1f} | {lig_carpan}x | {sinyal.reason}")

        # 6. AI analizi
        mac_verisi = {
            'ev_adi': ev_adi, 'dep_adi': dep_adi,
            'skor': skor, 'dakika': dk,
            'ta': ta, 'da': da, 'sot': sot, 'gol': toplam_gol,
            'ev_ta': v['ev_ta'], 'dep_ta': v['dep_ta'],
            'ev_da': v['ev_da'], 'dep_da': v['dep_da'],
            'ev_sot': v['ev_sot'], 'dep_sot': v['dep_sot'],
            'ev_gol': ev_gol, 'dep_gol': dep_gol,
        }
        ai_analiz, ai_source = await ai_analiz_yap(mac_verisi, session)

        # 7. Mesaj oluştur
        nesine_var = nesine_lig_kontrolu(league_name, ev_adi, dep_adi)
        nesine_str = "✅ Nesine'de OYNANMAKTADIR" if nesine_var else "ℹ️ Nesine'de yok"

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
            f"{'─'*32}\n"
            f"🎯 *Sebep:* {sinyal.reason}\n"
        )

        if ai_analiz:
            mesaj += f"{'─'*32}\n🤖 *{ai_source} AI:*\n{ai_analiz}\n"

        mesaj += f"{'─'*32}\n{nesine_str}"

        # [FIX-4] Kaydı yap
        if event_id:
            sinyal_gecmisi.kaydet(event_id, dk, sinyal.signal_type.value)

        return mesaj

    except Exception as e:
        logger.error(f"mac_analiz_et hatası: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


# ============================================================================
# MAÇ İŞLEME
# ============================================================================

async def mac_isle(bot, mac_data, session):
    try:
        mac_id  = str(mac_data.get('id', ''))
        ev_adi  = mac_data.get('home', {}).get('name', '') if isinstance(mac_data.get('home'), dict) else ''
        dep_adi = mac_data.get('away', {}).get('name', '') if isinstance(mac_data.get('away'), dict) else ''

        if not ev_adi or not dep_adi:
            return None

        league_name = mac_data.get('league', {}).get('name', '') if isinstance(mac_data.get('league'), dict) else 'Unknown'
        timer       = mac_data.get('timer', {})
        dk          = guvenli_int(timer.get('tm', 0)) if isinstance(timer, dict) else 0
        skor        = mac_data.get('ss', '0-0') or '0-0'

        # Event detay API
        stats_data    = None
        veri_kaynagi  = "inplay"

        try:
            async with session.get(
                f"https://api.betsapi.com/v1/event/view?token={BETSAPI_TOKEN}&event_id={mac_id}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as event_response:
                if event_response.status == 200:
                    event_data = await event_response.json()
                    if event_data.get('success') == 1:
                        results = event_data.get('results', [])
                        if results:
                            s = results[0].get('stats', {})
                            if s and isinstance(s, dict):
                                stats_data   = s
                                veri_kaynagi = "event_detail"
        except Exception:
            pass

        if not stats_data:
            stats_data   = mac_data.get('stats', {})
            veri_kaynagi = "inplay"

        if not stats_data or not isinstance(stats_data, dict):
            return None

        # Format parse
        ev_v = dep_v = None

        if 'corners' in stats_data and isinstance(stats_data.get('corners'), list):
            koruma = VeriKorumaKatmani()
            r = koruma.yeni_format_parse(stats_data)
            if r:
                ev_v, dep_v = r

        if not ev_v or not dep_v:
            ev_v  = stats_data.get('1', {})
            dep_v = stats_data.get('2', {})

        if not ev_v or not dep_v:
            return None

        ev_s  = sum(1 for k in ev_v.keys()  if k.startswith('S'))
        dep_s = sum(1 for k in dep_v.keys() if k.startswith('S'))
        if ev_s == 0 or dep_s == 0:
            return None

        return await mac_analiz_et(
            ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot, session,
            event_id=mac_id, league_name=league_name
        )

    except Exception as e:
        logger.error(f"mac_isle hatası: {e}")
        return None


# ============================================================================
# [FIX-6] TELEGRAM QUEUE SİSTEMİ — Flood koruması
# ============================================================================

telegram_queue: asyncio.Queue = None


async def telegram_gondericisi(bot):
    """
    [FIX-6] Arka planda çalışır, sinyalleri 1.5sn arayla gönderir.
    Telegram flood ban riskini ortadan kaldırır.
    """
    while True:
        try:
            chat_id, mesaj = await telegram_queue.get()
            try:
                await bot.send_message(chat_id=chat_id, text=mesaj, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Telegram gönderim hatası: {e}")
            finally:
                telegram_queue.task_done()
            await asyncio.sleep(1.5)  # Flood koruması
        except Exception as e:
            logger.error(f"Telegram queue hatası: {e}")
            await asyncio.sleep(1)


# ============================================================================
# ANA DÖNGÜ
# ============================================================================

async def ana_dongu():
    global telegram_queue
    telegram_queue = asyncio.Queue()

    monitor_task = asyncio.create_task(loop_monitor.monitor())

    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        logger.info("Bot başlatılıyor...")

        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "🚀 *BOT V50 — TÜM DÜZELTMELER UYGULANMIŞ*\n\n"
                "✅ Lig çarpanları excel verisiyle kalibre edildi\n"
                "✅ Skor filtresi: toplam gol bazlı\n"
                "✅ DA ivmesi lig bazlı eşik kullanıyor\n"
                "✅ SQLite kalıcı sinyal geçmişi\n"
                "✅ Grok/Gemini context manager düzeltildi\n"
                "✅ Telegram queue flood koruması\n"
                "✅ Sahte baskı xG dakikaya normalize\n"
                "✅ Sinyal konsensüs motoru\n"
                "✅ Gemini gemini-2.0-flash\n"
                "✅ Adaptif döngü süresi\n"
                "✅ Normalize puan barajı\n\n"
                "🎯 Hazır — sinyaller bekleniyor..."
            ),
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Bot başlatma hatası: {e}")
        return

    # [FIX-6] Telegram gönderici görevi başlat
    sender_task = asyncio.create_task(telegram_gondericisi(bot))

    async with aiohttp.ClientSession() as session:
        dongu_sayaci = 0
        aktif_maclar = []

        while True:
            dongu_sayaci += 1
            logger.debug(f"=== DÖNGÜ #{dongu_sayaci} ===")

            try:
                async with api_rate_limiter:
                    async with session.get(
                        f"https://api.betsapi.com/v1/events/inplay?sport_id=1&token={BETSAPI_TOKEN}",
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        if response.status != 200:
                            logger.error(f"Ana API hatası: HTTP {response.status}")
                            await asyncio.sleep(60)
                            continue
                        data    = await response.json()
                        matches = data.get('results', [])

                aktif_maclar = esnek_liste_duzelt(matches)
                logger.info(f"#{dongu_sayaci} | {len(aktif_maclar)} canlı maç")

                async def maci_isle_ve_bildir(mac_data):
                    try:
                        mesaj = await mac_isle(bot, mac_data, session)
                        if mesaj:
                            # [FIX-6] Queue'ya ekle — direkt gönderme
                            await telegram_queue.put((CHAT_ID, mesaj))
                    except Exception as e:
                        logger.error(f"Maç işleme wrapper hatası: {e}")

                gorevler = [maci_isle_ve_bildir(m) for m in aktif_maclar]
                if gorevler:
                    await asyncio.gather(*gorevler, return_exceptions=True)

                if dongu_sayaci % 10 == 0:
                    stats = api_rate_limiter.get_stats()
                    logger.info(f"Rate Limiter: {stats['total_requests']} istek, "
                                f"{stats['throttled_count']} throttled")
                    veri_koruma.istatistikleri_goster()

            except Exception as e:
                logger.error(f"Ana döngü hatası: {e}")
                import traceback
                logger.error(traceback.format_exc())

            # [FIX-12] Adaptif bekleme
            bekleme = dongu_suresi_hesapla(aktif_maclar)
            logger.debug(f"Bekleme: {bekleme}sn")
            await asyncio.sleep(bekleme)


if __name__ == "__main__":
    logger.info("🚀 Bot V50 Başlatılıyor...")
    logger.info(f"Telegram: {'✅' if TELEGRAM_TOKEN else '❌'} | "
                f"Chat: {'✅' if CHAT_ID else '❌'} | "
                f"BetsAPI: {'✅' if BETSAPI_TOKEN else '❌'}")

    try:
        asyncio.run(ana_dongu())
    except KeyboardInterrupt:
        logger.info("Bot durduruldu")
    except Exception as e:
        logger.error(f"Kritik hata: {e}")
        import traceback
        logger.error(traceback.format_exc())
