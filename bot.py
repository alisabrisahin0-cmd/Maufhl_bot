import asyncio, aiohttp, os, logging, re, time, math, sqlite3
from telegram import Bot
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from collections import deque

# ============================================================================
# BOT V57 — TEMİZ SÜRÜM
# ============================================================================
# Motor  : classify_live_signal()  (A+ / A / B / LOW_VALUE / PASS / IGNORE)
# Tipler : Gol Olacak (S) / Ev Gol Atacak (S) / Dep Gol Atacak (S)
# Kaldırıldı: GeminiFiltresi, ClaudeFiltresi, ExcelFiltreler,
#             BenimStratejiFiltresi, BlokFiltresi — tüm atıl kod
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
    # [ALTIN LİGLER — Kural 6]
    'wales championship south': 1.50,
    'thailand division 2':      1.50,
    'saudi arabia pro league':  1.50,
    'slovakia 3. liga':         1.50,
}

KARANTINA_LIGLER = [
    'brazil', 'brasil', 'kenya', 'ethiopia', 'rwanda',
    'oman', 'kuwait', 'iraq stars', 'afghanistan',
]

# [R8] TVPS feature ağırlıkları (tarihsel kalibrasyona göre)
TVPS_AGIRLIKLAR = {
    'da_ivmesi':          +2.1,
    'proxy_xt':           +3.5,
    'ah_momentum':        +2.8,
    'true_rlm':           +3.0,
    'corner_deficit':     +2.5,
    'sahte_baski':        -4.0,   # negatif ağırlık
    'fpressure_endeks':   -3.5,   # negatif ağırlık
    'entropi_yuksek':     +1.8,
    'skor_altin':         +2.0,
    'lig_carpan_bonus':   +1.5,
}
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Hassas URL'lerin loga düşmesini azalt.
for _noisy_logger in ("httpx", "httpcore", "telegram", "telegram.ext"):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)


def mask_secret_url(value: str) -> str:
    """Log güvenliği: Telegram bot token gibi hassas parçaları maskele."""
    if not isinstance(value, str):
        return value
    value = re.sub(r'(bot)\d{6,}:[A-Za-z0-9_-]+', r'\1***TOKEN***', value)
    value = re.sub(r'([?&](?:token|api_key|key)=)[^&\s]+', r'\1***', value, flags=re.IGNORECASE)
    return value


sinyal_logger = logging.getLogger('sinyal')
_sh = logging.StreamHandler()
_sh.setFormatter(logging.Formatter(
    '🎯 %(asctime)s SINYAL | %(message)s', '%H:%M:%S'))
sinyal_logger.addHandler(_sh)
sinyal_logger.setLevel(logging.INFO)

# ============================================================================
# V57 GLOBAL FLAGS (Açılı Kapalı Denetim)
# ============================================================================
ENABLE_CLASSIFY_ENGINE        = True   # Sadece classify_live_signal() motoru aktif
ENABLE_LEGACY_MODULES         = False  # eski IY/EvDep/IY2/Konsensus kapalı
SEND_B_SIGNALS                = False  # B sinyalleri sadece log, Telegram'a gitmez
SEND_NEUTRAL_LEAGUE_A_ONLY    = True   # Neutral liglerde sadece A+ Telegram'a gider
REQUIRE_AH_FOR_SIGNAL         = True   # AH verisi yoksa sinyal üretilmez
BLOCK_LEGACY_DIAMOND_MESSAGES = True   # 💎 SİNYAL formatı Telegram'a gitmez
ENABLE_ANALYSIS_ONLY_LOGGING  = True   # Kadın/genç/rezerv ligleri analysis loglanır
ENABLE_WOMEN_MAIN_TELEGRAM    = False  # Kadın ligleri şimdi ana Telegram'a gitmez

# ============================================================================
# KONFIGÜRASYON
# ============================================================================

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID          = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN    = os.getenv("BETSAPI_TOKEN", "")
GROK_API_KEY     = os.getenv("GROK_API_KEY") or None
GEMINI_API_KEY_1 = os.getenv("GEMINI_API_KEY_1") or None
GEMINI_API_KEY_2 = os.getenv("GEMINI_API_KEY_2") or None
GEMINI_API_KEY_3 = os.getenv("GEMINI_API_KEY_3") or None

print(f"🔑 Grok={'✅' if GROK_API_KEY else '❌'} | "
      f"Gemini={sum(1 for k in [GEMINI_API_KEY_1,GEMINI_API_KEY_2,GEMINI_API_KEY_3] if k)}/3")
print(f"[V57 FLAGS] ClassifyEngine={ENABLE_CLASSIFY_ENGINE} | "
      f"Legacy={ENABLE_LEGACY_MODULES} | "
      f"ReqAH={REQUIRE_AH_FOR_SIGNAL}")

# ============================================================================
# LİG KATEGORİ NUMARALANDIRMASI
# ============================================================================
class LeagueCategory(Enum):
    """Lig kategorileri — Telegram politikasını belirler"""
    HARD_REJECT    = "HARD_REJECT"      # Tamamen reddedilir (esport, vb)
    ANALYSIS_ONLY  = "ANALYSIS_ONLY"    # Kadın/genç/rezerv — veri kaydedilir, Telegram kapalı
    KARANTINA      = "KARANTINA"        # Şimdilik Telegram kapalı (Brazil, Kenya, vb)
    NEUTRAL        = "NEUTRAL"         # Liste dışı normal ligler (sadece A+ gönder)
    WHITELIST      = "WHITELIST"       # Güvenilir büyük ligler (A+/A gönder)

# ============================================================================
# KALICI SİNYAL GEÇMİŞİ
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
        return (dakika // 15) * 15

    def zaten_gonderildi_mi(self, event_id, dakika, sinyal_tipi) -> bool:
        dk = self._dk_grubu(dakika)
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT 1 FROM sinyaller WHERE event_id=? AND dk_grubu=? AND sinyal_tipi=?",
                (event_id, dk, sinyal_tipi))
            return cur.fetchone() is not None

    def kaydet(self, event_id, dakika, sinyal_tipi):
        dk = self._dk_grubu(dakika)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("INSERT INTO sinyaller VALUES (?,?,?,?)",
                             (event_id, dk, sinyal_tipi, time.time()))
                conn.commit()
        except sqlite3.IntegrityError:
            pass


# [V57-FIX-1] Global instance — class bittikten hemen sonra tanımla
sinyal_gecmisi = SinyalGecmisi()

# ============================================================================
# ANALYSIS_ONLY SINYALLER (Kadın/Genç/Rezerv Ligleri)
# ============================================================================
class AnalysisOnlySignals:
    def __init__(self, db_path="sinyaller.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analysis_only_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT,
                    league_name TEXT,
                    league_category TEXT,
                    home_team TEXT,
                    away_team TEXT,
                    dakika INTEGER,
                    skor TEXT,
                    home_goals INTEGER,
                    away_goals INTEGER,
                    home_corners INTEGER,
                    away_corners INTEGER,
                    total_corners INTEGER,
                    ah REAL,
                    tahmin_tipi TEXT,
                    sinyal TEXT,
                    puan INTEGER,
                    neden TEXT,
                    timestamp REAL
                )
            """)
            conn.commit()

    def kaydet(self, event_id, league_name, category, home_team, away_team,
               dakika, skor, home_goals, away_goals, home_corners, away_corners,
               ah, tahmin_tipi, sinyal_dict):
        try:
            with sqlite3.connect(self.db_path) as conn:
                neden_str = " | ".join(sinyal_dict.get("neden", []))
                conn.execute("""
                    INSERT INTO analysis_only_signals
                    (event_id, league_name, league_category, home_team, away_team,
                     dakika, skor, home_goals, away_goals, home_corners, away_corners,
                     total_corners, ah, tahmin_tipi, sinyal, puan, neden, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (event_id, league_name, category, home_team, away_team,
                      dakika, skor, home_goals, away_goals, home_corners, away_corners,
                      home_corners + away_corners, ah, tahmin_tipi,
                      sinyal_dict.get("sinyal", "PASS"), sinyal_dict.get("puan", 0),
                      neden_str, time.time()))
                conn.commit()
                logger.info(f"[ANALYSIS_ONLY] {league_name}/{home_team} vs {away_team} "
                           f"{dakika}dk: {sinyal_dict.get('sinyal')} kaydedildi")
        except Exception as e:
            logger.error(f"[ANALYSIS_ONLY] Kayıt hatası: {e}")

analysis_only_signals = AnalysisOnlySignals()

# ── Puan tabloları ──────────────────────────────────────────────────────────
_DAKIKA_PUAN = {
    (0,  15): +25,
    (16, 30): +23,
    (31, 45): +14,
    (46, 60):   0,
    (61, 75):  -8,
    (76, 90): -12,
}

_KORNER_PUAN = {
    (0,  3): +15,
    (4,  6):  +5,
    (7,  9): -12,
    (10, 99): -6,
}

def _dakika_puani(dk: float) -> int:
    for (lo, hi), p in _DAKIKA_PUAN.items():
        if lo <= dk <= hi:
            return p
    return 0

def _korner_puani(toplam_korner: int) -> int:
    for (lo, hi), p in _KORNER_PUAN.items():
        if lo <= toplam_korner <= hi:
            return p
    return 0

def _ah_puani(ah_abs: float) -> int:
    """AH mutlak değerine göre puan"""
    if ah_abs >= 2.0:        return +25
    if ah_abs >= 1.25:       return +22   # 1.25 / 1.50 / 1.75
    if ah_abs >= 1.0:        return +10
    if ah_abs >= 0.75:       return +4
    if ah_abs <= 0.25:       return -2    # 0.0 / 0.25
    if abs(ah_abs - 0.5) < 0.01: return -8
    return 0

def _skorline_bonusu(tip: str, ev_gol: int, dep_gol: int) -> int:
    """Skor durumuna göre bonus/ceza
    
    [V57-FIX-3] Tek gol bonusu KESİN KALDIRILDı.
    - Gol Olacak: dk<60 zaten IGNORE
    - Gol Olacak: dk>=60 özel A/A+ kurallarıyla değerlendirilir
    - 3+ gol cezası devam eder
    """
    toplam = ev_gol + dep_gol
    if tip == "Gol Olacak (S)":
        # Tek gol bonusu YOK (erken 1-0 / 0-1 maçları tekrar A'ya dönüştürmemek için)
        if toplam >= 3:   return -8    # 2-1, 2-2, 3-0, 3-1 gibi
        return 0
    elif tip in ("Ev Gol Atacak (S)", "Dep Gol Atacak (S)"):
        # 0-0 bonusu: Saf başlangıç => gol potansiyeli yüksek
        if ev_gol == 0 and dep_gol == 0:
            return +15
        return 0
    return 0

def _sinif_belirle(puan: int) -> str:
    """Ham puanı sınıfa çevir"""
    if puan >= 60:  return "A+"
    if puan >= 45:  return "A"
    if puan >= 25:  return "B"
    if puan >= 10:  return "LOW_VALUE"
    return "PASS"


def ah_favori_yonu(ah: float) -> str:
    """
    [V57-FIX-4] BetsAPI ev_handicap işaretinden favori yönü tespit et.
    ah < -0.25 → ev favori (ev -handicap alıyor)
    ah > +0.25 → deplasman favori
    arası     → dengeli maç
    """
    if ah < -0.25:
        return "EV"
    if ah > 0.25:
        return "DEP"
    return "DENGE"



def _ah_puani_yonlu(tip: str, ah: float) -> Tuple[int, str]:
    """
    AH puanını market yönüne göre verir.
    Gol Olacak takım bağımsızdır; Ev/Dep Gol Atacak ise AH yönüyle aynı tarafta olmalıdır.
    """
    ah_abs = abs(ah)
    fav = ah_favori_yonu(ah)

    if tip == "Gol Olacak (S)":
        p = _ah_puani(ah_abs)
        return p, f"AH({ah:+.2f}): takım bağımsız {p:+d}"

    if tip == "Ev Gol Atacak (S)":
        if fav == "EV":
            p = _ah_puani(ah_abs)
            return p, f"AH({ah:+.2f}): EV favori, Ev Gol için {p:+d}"
        if fav == "DENGE":
            return 0, f"AH({ah:+.2f}): denge, Ev Gol için +0"
        return -20, f"AH({ah:+.2f}): DEP favori, Ev Gol için -20"

    if tip == "Dep Gol Atacak (S)":
        if fav == "DEP":
            p = _ah_puani(ah_abs)
            return p, f"AH({ah:+.2f}): DEP favori, Dep Gol için {p:+d}"
        if fav == "DENGE":
            return 0, f"AH({ah:+.2f}): denge, Dep Gol için +0"
        return -20, f"AH({ah:+.2f}): EV favori, Dep Gol için -20"

    return 0, f"AH({ah:+.2f}): +0"


def _target_team_is_leading(tip: str, ev_gol: int, dep_gol: int) -> bool:
    if tip == "Ev Gol Atacak (S)":
        return ev_gol > dep_gol
    if tip == "Dep Gol Atacak (S)":
        return dep_gol > ev_gol
    return False


def _leading_team_goal_penalty(tip: str, ev_gol: int, dep_gol: int, dakika: float) -> Tuple[int, str]:
    """
    Takım golü marketinde hedef takım zaten öndeyse value düşer.
    Bu özellikle erken favori golü spam'ini azaltır.
    """
    if tip == "Ev Gol Atacak (S)" and ev_gol > dep_gol:
        if dakika < 30:
            return -8, "Ev zaten önde: erken devam golü value cezası -8"
        if dakika < 60:
            return -12, "Ev zaten önde: oyun kontrolü riski -12"
        return -18, "Ev zaten önde: geç dakika rölanti riski -18"

    if tip == "Dep Gol Atacak (S)" and dep_gol > ev_gol:
        if dakika < 30:
            return -8, "Dep zaten önde: erken devam golü value cezası -8"
        if dakika < 60:
            return -12, "Dep zaten önde: oyun kontrolü riski -12"
        return -18, "Dep zaten önde: geç dakika rölanti riski -18"

    return 0, ""


def classify_live_signal(
    tip: str,
    dakika: float,
    ev_gol: int,
    dep_gol: int,
    ev_corner: int,
    dep_corner: int,
    ah: float,
) -> dict:
    """
    Canlı maç sinyalini sınıflandırır.

    Parametreler
    ------------
    tip        : "Gol Olacak (S)" | "Ev Gol Atacak (S)" | "Dep Gol Atacak (S)"
    dakika     : Maç dakikası (0-90)
    ev_gol     : Ev sahibi gol sayısı
    dep_gol    : Deplasman gol sayısı
    ev_corner  : Ev sahibi korner sayısı
    dep_corner : Deplasman korner sayısı
    ah         : Asian Handicap (pozitif = ev favori, negatif = dep favori)

    Döndürür
    --------
    dict:
        sinyal  : "A+" | "A" | "B" | "LOW_VALUE" | "PASS" | "IGNORE"
        puan    : Ham puan (int)
        neden   : Açıklama dizisi (list[str])
        market  : Önerilen market (str)
    """
    toplam_gol    = ev_gol + dep_gol
    toplam_korner = ev_corner + dep_corner
    ah_abs        = abs(ah)
    neden         = []
    market        = ""

    # [V57-FIX-4] Favori yönü
    fav = ah_favori_yonu(ah)

    # ══════════════════════════════════════════════════════════════════════
    # 1) HARD PASS / IGNORE — önce kontrol et, erken çık
    # ══════════════════════════════════════════════════════════════════════

    # [V57-FINAL] GOL OLACAK (S): 50. dakikadan önce Telegram sinyali yok.
    # Erken dakikalarda oran/value şiştiği için A/A+ üretme.
    if tip == "Gol Olacak (S)" and dakika < 50:
        return {
            "sinyal": "PASS",
            "puan":   0,
            "neden":  [f"{dakika:.0f}dk < 50: Gol Olacak marketi için value yok, Telegram kapalı"],
            "market": "—",
        }

    # GOL OLACAK (S): tek gol + dakika < 60 → value yok, IGNORE
    # 60. dakikadan önce 1-0 / 0-1 sinyali güvenilir değil.
    if tip == "Gol Olacak (S)" and toplam_gol == 1 and dakika < 60:
        return {
            "sinyal": "IGNORE",
            "puan":   0,
            "neden":  [f"{dakika:.0f}dk + tek gol: 60dk altında value taşımıyor"],
            "market": "—",
        }

    # GOL OLACAK (S): çok erken 2+ gol = piyasa/value şişmiş olabilir.
    if tip == "Gol Olacak (S)" and dakika < 30 and toplam_gol >= 2:
        return {
            "sinyal": "IGNORE",
            "puan":   0,
            "neden":  [f"{dakika:.0f}dk + {toplam_gol} gol: erken skor şişmiş, value düşük"],
            "market": "—",
        }

    # GOL OLACAK (S): ilk yarıda fark 2+ ve toplam 2+ gol = erken kopma riski.
    if (tip == "Gol Olacak (S)"
            and dakika < 45
            and toplam_gol >= 2
            and abs(ev_gol - dep_gol) >= 2):
        return {
            "sinyal": "IGNORE",
            "puan":   0,
            "neden":  [f"{dakika:.0f}dk + fark≥2 + toplam gol≥2: erken kopma/value düşük"],
            "market": "—",
        }

    # EV GOL ATACAK (S) — 46-60dk + AH ≈ ±0.5 → PASS
    if (tip == "Ev Gol Atacak (S)"
            and 46 <= dakika <= 60
            and abs(ah_abs - 0.5) < 0.01):
        return {
            "sinyal": "PASS",
            "puan":   0,
            "neden":  ["46-60dk + AH±0.5 + Ev Gol Atacak → riskli bölge"],
            "market": "—",
        }

    # EV GOL ATACAK (S) — 46-60dk + skor 3-0 → HARD_PASS
    if (tip == "Ev Gol Atacak (S)"
            and 46 <= dakika <= 60
            and ev_gol == 3 and dep_gol == 0):
        return {
            "sinyal": "PASS",
            "puan":   0,
            "neden":  ["46-60dk + 3-0 → HARD_PASS: oyun yönetimi riski"],
            "market": "—",
        }

    # DEP GOL ATACAK (S) — 46-60dk + AH ≈ ±0.5 → PASS
    if (tip == "Dep Gol Atacak (S)"
            and 46 <= dakika <= 60
            and abs(ah_abs - 0.5) < 0.01):
        return {
            "sinyal": "PASS",
            "puan":   0,
            "neden":  ["46-60dk + AH±0.5 + Dep Gol Atacak → riskli bölge"],
            "market": "—",
        }

    # TAKIM GOLÜ: hedef takım zaten öndeyse erken dakikada value düşük.
    # Bu, Deren 1-0 gibi favori devam golü spam'ini keser.
    if tip in ("Ev Gol Atacak (S)", "Dep Gol Atacak (S)") and _target_team_is_leading(tip, ev_gol, dep_gol):
        if dakika < 30:
            return {
                "sinyal": "LOW_VALUE",
                "puan": 0,
                "neden": [f"{dakika:.0f}dk + hedef takım zaten önde: takım golü value düşük"],
                "market": "—",
            }

    # ══════════════════════════════════════════════════════════════════════
    # 2) TİPE GÖRE ÖZEL A+ KURALLARI
    # ══════════════════════════════════════════════════════════════════════

    # ── GOL OLACAK (S) ────────────────────────────────────────────────────
    if tip == "Gol Olacak (S)":
        market = "Gol Olacak / MS 0.5 Üst"

        # [V57-FIX-2] A+ : toplam gol=1, korner=0-3, AH mutlak ≥1.25, dk≥60
        # 60. dakika öncesi 1-0 güvenilir değildir
        if (toplam_gol == 1
                and toplam_korner <= 3
                and ah_abs >= 1.25
                and dakika >= 60):
            neden.append("A+ koşulu: gol=1, korner≤3, AH≥1.25, dk≥60")
            return {
                "sinyal": "A+",
                "puan":   75,
                "neden":  neden,
                "market": market,
            }

        # [V57-FIX-2] A : dk≥60 + AH≥1.0 + gol=1
        if dakika >= 60 and ah_abs >= 1.0 and toplam_gol == 1:
            neden.append("A koşulu: dk≥60, AH≥1.0, gol=1")
            return {
                "sinyal": "A",
                "puan":   50,
                "neden":  neden,
                "market": market,
            }

    # ── EV GOL ATACAK (S) ─────────────────────────────────────────────────
    elif tip == "Ev Gol Atacak (S)":
        market = "Ev Gol Atacak / Ev Sıradaki Gol"

        # [V57-FIX-4] A+ : skor 0-0 + AH mutlak ≥2.0 + ev favori
        if ev_gol == 0 and dep_gol == 0 and fav == "EV" and ah_abs >= 2.0:
            neden.append("A+ koşulu: 0-0, ev favori (EV), AH≥2.0 → güçlü ev favori")
            return {
                "sinyal": "A+",
                "puan":   70,
                "neden":  neden,
                "market": market,
            }

        # Eski fav-yönsüz A+ kuralı kaldırıldı.
        # Ev golünde güçlü AH sadece fav == EV ise değerli sayılır.

        # A : skor 0-0 + korner 0-3 + EV yönlü AH desteği
        # AH yok / DENGE iken Genoa gibi maçlar A üretmemeli.
        if ev_gol == 0 and dep_gol == 0 and toplam_korner <= 3 and fav == "EV" and ah_abs >= 0.75:
            neden.append("A koşulu: 0-0, korner≤3, EV yönlü AH≥0.75")
            return {
                "sinyal": "A",
                "puan":   50,
                "neden":  neden,
                "market": market,
            }

    # ── DEP GOL ATACAK (S) ────────────────────────────────────────────────
    elif tip == "Dep Gol Atacak (S)":
        market = "Dep Gol Atacak / Dep Sıradaki Gol"

        # [V57-FIX-4] A : skor 0-0 + deplasman favori + AH≥1.25
        if ev_gol == 0 and dep_gol == 0 and fav == "DEP" and ah_abs >= 1.25:
            neden.append("A koşulu: 0-0, deplasman favori (DEP), AH≥1.25")
            return {
                "sinyal": "A",
                "puan":   50,
                "neden":  neden,
                "market": market,
            }

        # B+ : dakika 0-15 + AH ters yönde değil
        if dakika <= 15 and fav != "EV":
            neden.append("B+ koşulu: ilk 15dk — erken açık pozisyon")
            return {
                "sinyal": "B",
                "puan":   35,
                "neden":  neden,
                "market": market,
            }

        # B+ : skor 0-0 + AH ters yönde değil
        if ev_gol == 0 and dep_gol == 0 and fav != "EV":
            neden.append("B+ koşulu: skor 0-0 → dep gol potansiyeli")
            return {
                "sinyal": "B",
                "puan":   35,
                "neden":  neden,
                "market": market,
            }

    # ══════════════════════════════════════════════════════════════════════
    # 3) GENEL PUANLAMA (özel A+/A eşleşmedi ise)
    # ══════════════════════════════════════════════════════════════════════
    puan = 0

    # Dakika puanı
    dp = _dakika_puani(dakika)
    puan += dp
    neden.append(f"Dakika({dakika:.0f}dk): {dp:+d}")

    # Korner puanı
    kp = _korner_puani(toplam_korner)
    puan += kp
    if kp != 0:
        neden.append(f"Korner({toplam_korner}): {kp:+d}")

    # AH puanı — takım golü marketlerinde yönlü, Gol Olacak'ta takım bağımsız
    ap, ah_neden = _ah_puani_yonlu(tip, ah)
    puan += ap
    neden.append(ah_neden)

    # Hedef takım zaten öndeyse takım golü marketine value cezası
    lead_penalty, lead_msg = _leading_team_goal_penalty(tip, ev_gol, dep_gol, dakika)
    if lead_penalty:
        puan += lead_penalty
        neden.append(lead_msg)

    # Skorline bonusu
    sb = _skorline_bonusu(tip, ev_gol, dep_gol)
    puan += sb
    if sb != 0:
        neden.append(f"Skorline({ev_gol}-{dep_gol}): {sb:+d}")

    # Riskli bölge cezası: çok korner (7-9) ek uyarı
    if 7 <= toplam_korner <= 9:
        neden.append("⚠️ Yüksek korner (7-9): risk bölgesi")

    sinyal = _sinif_belirle(puan)

    # Takım golü marketinde hedef takım zaten öndeyse A+ üretme, maksimum A.
    if sinyal == "A+" and _target_team_is_leading(tip, ev_gol, dep_gol):
        sinyal = "A"
        neden.append("A+ sınırı: hedef takım zaten önde, maksimum A")

    # Market belirleme (genel puanlama yoluna düşenler için)
    if not market:
        if tip == "Gol Olacak (S)":
            market = "Gol Olacak / MS 0.5 Üst"
        elif tip == "Ev Gol Atacak (S)":
            market = "Ev Gol Atacak"
        else:
            market = "Dep Gol Atacak"

    return {
        "sinyal": sinyal,
        "puan":   puan,
        "neden":  neden,
        "market": market,
    }


# ── Telegram mesaj formatı ───────────────────────────────────────────────────

_SINYAL_EMOJI = {
    "A+":        "🔥🔥",
    "A":         "🔥",
    "B":         "✅",
    "LOW_VALUE": "⚠️",
    "PASS":      "⛔",
    "IGNORE":    "🔕",
}

def sinyal_mesaj_olustur(
    tip:        str,
    dakika:     float,
    ev_adi:     str,
    dep_adi:    str,
    skor:       str,
    league:     str,
    sonuc:      dict,
) -> str:
    """
    classify_live_signal() çıktısından Telegram mesajı üretir.
    Sadece A+, A ve B sinyalleri Telegram'a gönderilmeli.
    """
    emoji  = _SINYAL_EMOJI.get(sonuc["sinyal"], "📊")
    neden_str = "\n".join(f"  • {n}" for n in sonuc["neden"])
    return (
        f"\n{'═'*32}\n"
        f"{emoji} *[SİNYAL {sonuc['sinyal']}]* — {tip}\n"
        f"⚽ {ev_adi} {skor} {dep_adi}\n"
        f"🏆 {league}\n"
        f"⏱ Dakika: {dakika:.0f}\n"
        f"{'─'*30}\n"
        f"🎯 Market: {sonuc['market']}\n"
        f"📊 Ham Puan: {sonuc['puan']}\n"
        f"{'─'*30}\n"
        f"📋 Gerekçe:\n{neden_str}\n"
    )


# ── Geriye uyumluluk: mac_analiz_et'in çağıracağı ana fonksiyon ─────────────

def canli_sinyal_siniflandir(
    tip:        str,
    dakika:     float,
    ev_gol:     int,
    dep_gol:    int,
    ev_corner:  int,
    dep_corner: int,
    ah:         float,
    ev_adi:     str = "",
    dep_adi:    str = "",
    skor:       str = "",
    league:     str = "",
) -> tuple:
    """
    Wrapper: classify_live_signal() çağırır ve
    (mesaj_veya_None, sinyal_dict) döndürür.
    
    [V57-FIX-5] Telegram'a gönderilebilecek sınıflar:
    - SEND_B_SIGNALS=False (default): sadece A+, A
    - SEND_B_SIGNALS=True: A+, A, B
    """
    sonuc = classify_live_signal(tip, dakika, ev_gol, dep_gol,
                                  ev_corner, dep_corner, ah)
    
    sendable = ("A+", "A") if not SEND_B_SIGNALS else ("A+", "A", "B")
    
    if sonuc["sinyal"] in sendable:
        mesaj = sinyal_mesaj_olustur(
            tip, dakika, ev_adi, dep_adi, skor, league, sonuc)
        return mesaj, sonuc
    elif sonuc["sinyal"] == "B":
        logger.info(f"[V57] B sinyali log only: {tip} {dakika}dk {ev_adi} vs {dep_adi}")
        return None, sonuc
    
    return None, sonuc


# ════════════════════════════════════════════════════════════════════════════
# [V57-FIX-2] @dataclass eklendi — AHKinetik() boş çağrılabilir hale geldi
@dataclass
class AHKinetik:
    """AH çizgisinin hız ve ivme analizi"""
    velocity:       float = 0.0   # v_AH = ΔAH/Δt
    acceleration:   float = 0.0   # a_AH = Δv/Δt
    momentum_score: float = 0.0
    yon:            str   = 'sabit'  # 'daralma' | 'genisleme' | 'sabit'
    clv_proxy:      float = 0.0


class AHHareketTakibi:
    """
    [R1] AH kinetik analizi.
    Her maç için (zaman, ah_ev, oran_ev, oran_dep) geçmişi tutulur.
    Velocity, acceleration ve momentum score hesaplanır.
    """

    W1 = 0.6   # velocity ağırlığı
    W2 = 0.4   # acceleration ağırlığı

    def __init__(self):
        # {event_id: deque[(zaman, ah_ev, ah_dep, oran_ev, oran_dep)]}
        self._gecmis: Dict[str, deque] = {}

    def kaydet(self, event_id: str, ah_ev: float, ah_dep: float,
               oran_ev: float, oran_dep: float):
        if event_id not in self._gecmis:
            self._gecmis[event_id] = deque(maxlen=12)
        self._gecmis[event_id].append(
            (time.time(), ah_ev, ah_dep, oran_ev, oran_dep))

    def kinetik_hesapla(self, event_id: str,
                        guncel_ah: float,
                        score_diff: int = 0,
                        dakika: int = 45) -> AHKinetik:
        kayitlar = list(self._gecmis.get(event_id, []))
        if len(kayitlar) < 2:
            return AHKinetik()

        # [R1] Velocity: son iki kayıt arası
        t1, ah1 = kayitlar[-2][0], kayitlar[-2][1]
        t2, ah2 = kayitlar[-1][0], kayitlar[-1][1]
        dt = max(t2 - t1, 1.0) / 60   # dakikaya çevir
        v_ah = (ah2 - ah1) / dt

        # Acceleration: üç kayıt varsa
        a_ah = 0.0
        if len(kayitlar) >= 3:
            t0, ah0 = kayitlar[-3][0], kayitlar[-3][1]
            dt_prev = max(t1 - t0, 1.0) / 60
            v_prev  = (ah1 - ah0) / dt_prev
            a_ah    = (v_ah - v_prev) / dt

        # [R1] Regression to mean penalizasyonu
        # Öne geçen takım oyunu yavaşlatır → momentum skoru düşer
        r_penalty = 0.0
        if score_diff >= 1:
            kalan_sure = max(90 - dakika, 1)
            r_penalty  = -(score_diff * 0.3) * (1 / kalan_sure * 10)

        momentum = (v_ah * self.W1) + (a_ah * self.W2) + r_penalty

        # Yön tespiti
        fark = abs(guncel_ah) - abs(kayitlar[0][1])
        if abs(fark) < 0.10:
            yon = 'sabit'
        else:
            yon = 'daralma' if fark < 0 else 'genisleme'

        # CLV proxy: ilk giriş fiyatından sapma
        clv = (abs(kayitlar[0][1]) - abs(guncel_ah)) / max(abs(kayitlar[0][1]), 0.01)

        return AHKinetik(
            velocity=round(v_ah, 4),
            acceleration=round(a_ah, 4),
            momentum_score=round(momentum, 3),
            yon=yon,
            clv_proxy=round(clv, 4)
        )

    def implied_prob_drift(self, event_id: str) -> float:
        """[R10] Son 5dk zımni olasılık değişimi"""
        kayitlar = list(self._gecmis.get(event_id, []))
        if len(kayitlar) < 2:
            return 0.0
        oran_eski = kayitlar[0][3]
        oran_yeni = kayitlar[-1][3]
        if oran_eski <= 0 or oran_yeni <= 0:
            return 0.0
        return round((1 / oran_yeni) - (1 / oran_eski), 4)

    def rlm_skoru(self, event_id: str, home_da_ratio: float,
                  guncel_ah: float) -> Tuple[float, str]:
        """[R3] True RLM vs Fake RLM ayrımı — basitleştirilmiş logistic proxy"""
        kinetik = self.kinetik_hesapla(event_id, guncel_ah)

        # X1: Volume/ticket delta proxy (da_ratio ile temsil)
        x1 = abs(home_da_ratio - 0.5)

        # X2: Stats divergence — DA ev lehine ama AH ters gidiyorsa
        stats_divergence = 0.0
        if home_da_ratio > 0.6 and kinetik.yon == 'genisleme':
            stats_divergence = home_da_ratio - 0.5
        elif home_da_ratio < 0.4 and kinetik.yon == 'daralma':
            stats_divergence = 0.5 - home_da_ratio

        # X3: Line reversal volatility — hız değişimi
        x3 = abs(kinetik.velocity)

        # Logistic regression proxy (sabit katsayılar)
        z = 0.5 + (1.2 * x1) + (2.0 * stats_divergence) - (0.8 * x3)
        p_true_rlm = 1 / (1 + math.exp(-z))

        if p_true_rlm > 0.70:
            return p_true_rlm, "TRUE_RLM"
        elif p_true_rlm > 0.45:
            return p_true_rlm, "BELIRSIZ_RLM"
        else:
            return p_true_rlm, "FAKE_RLM"


ah_hareket = AHHareketTakibi()


# ============================================================================
# AH SPLİT MEKANİZMASI (V51'den korundu)
# ============================================================================

@dataclass
class AHSplitSonuc:
    ah_degeri:      float
    tip:            str
    split_alt:      float
    split_ust:      float
    iade_olasiligi: float
    kayip_baskisi:  float
    bonus_puan:     float


def ah_split_hesapla(ah_degeri: float) -> AHSplitSonuc:
    a     = abs(ah_degeri)
    kesir = round(a % 1, 2)
    if a < 0.01:
        return AHSplitSonuc(ah_degeri, 'dnb',       0.0,       0.0,       1.0, 0.5, 3.0)
    elif kesir < 0.01 or abs(kesir - 1.0) < 0.01:
        return AHSplitSonuc(ah_degeri, 'tam_iade',  a,         a,         0.5, 0.7, 2.0)
    elif abs(kesir - 0.5) < 0.01:
        return AHSplitSonuc(ah_degeri, 'ikilik',    a,         a,         0.0, 1.3, 0.0)
    elif abs(kesir - 0.25) < 0.01:
        return AHSplitSonuc(ah_degeri, 'yarim_iade',0.0,       a + 0.25,  0.5, 0.8, 2.0)
    elif abs(kesir - 0.75) < 0.01:
        return AHSplitSonuc(ah_degeri, 'yarim_iade',a - 0.25,  a + 0.25,  0.5, 1.0, 1.0)
    return AHSplitSonuc(ah_degeri, 'bilinmeyen', a, a, 0.0, 1.0, 0.0)


# ============================================================================
# API RATE LIMITER
# ============================================================================
class APIRateLimiter:
    def __init__(self, max_concurrent=5, rps=10):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rps = rps
        self.last = 0.0
        self.count = 0
        self.lock = asyncio.Lock()
        self.total = 0
        self.throttled = 0

    async def acquire(self):
        await self.semaphore.acquire()
        async with self.lock:
            now = time.time()
            if now - self.last >= 1.0:
                self.count = 0
                self.last  = now
            if self.count >= self.rps:
                wait = 1.0 - (now - self.last)
                if wait > 0:
                    self.throttled += 1
                    await asyncio.sleep(wait)
                self.count = 0
                self.last  = time.time()
            self.count += 1
            self.total += 1

    def release(self):
        self.semaphore.release()

    async def __aenter__(self):
        await self.acquire(); return self

    async def __aexit__(self, *_):
        self.release()

    def stats(self):
        return {'total': self.total, 'throttled': self.throttled}


api_rate_limiter = APIRateLimiter()


# ============================================================================
# EVENT LOOP MONITOR
# ============================================================================

class EventLoopMonitor:
    def __init__(self, threshold_ms=50):
        self.threshold_ms = threshold_ms
        self.lag_count = 0
        self.max_lag   = 0.0
        self.total     = 0
        self.running   = False

    async def monitor(self):
        self.running = True
        while self.running:
            t0 = time.time()
            await asyncio.sleep(0.1)
            lag = (time.time() - t0 - 0.1) * 1000
            self.total += 1
            if lag > self.threshold_ms:
                self.lag_count += 1
                self.max_lag = max(self.max_lag, lag)
                if lag > 200:
                    logger.error(f"KRİTİK LAG: {lag:.0f}ms")
            if self.total % 3000 == 0:
                logger.info(f"EventLoop: %{self.lag_count/self.total*100:.1f} lag, "
                            f"max={self.max_lag:.0f}ms")

    def stop(self): self.running = False


loop_monitor = EventLoopMonitor()


# ============================================================================
# LİG FİLTRELEME
# ============================================================================

class LeagueFilter:
    """[V57-FIX-6] Lig kategorileme sistemi"""
    
    # HARD_REJECT: Esport ve sahte maçlar
    HARD_REJECT_PATTERNS = [
        r'\be[-\s]?sport[s]?\b',
        r'\besoccer\b', r'\befootball\b', r'\befoot\b',
        r'\bcyber\b',  r'\bgaming\b',   r'\bfifa\b',
        r'\bpes\b',    r'\bvirtual\b',  r'\bsimulat',
        r'\bclash\b',  r'\barena\b',    r'\bdigital\b',
        r'\bonline\b',
    ]
    
    # ANALYSIS_ONLY: Kadın / Genç / Rezerv ligleri
    ANALYSIS_ONLY_PATTERNS = [
        r'\b(w|women|kadın|kadin|female|ladies)\b',
        r'\b(reserves?|rezerv)\b',
        r'\b(youth|junior|academy)\b',
        r'\bu\d{2}\b',  # U17, U18, U19, U20, U21, U23
    ]
    
    # TEAM_ANALYSIS_ONLY: sadece takım adlarında kontrol edilir.
    # Lig adındaki "III Liga" gibi ifadeleri yanlışlıkla reserve saymamak için
    # bu liste league_name'e değil, home_team + away_team metnine uygulanır.
    TEAM_ANALYSIS_ONLY_PATTERNS = [
        r'\bii\b',
        r'\biii\b',
        r'\biv\b',
        # B/2 takımları için tek harfli gerçek takım adlarını yanlış yakalamamak adına
        # en az 3 harfli takım kökü şartı aranır: Barcelona B, Ajax 2 gibi.
        r'\b[a-z0-9çğıöşü]{3,}\s+b\b',
        r'\bb\s+[a-z0-9çğıöşü]{3,}\b',
        r'\b[a-z0-9çğıöşü]{3,}\s+2\b',
        r'\b2\s+[a-z0-9çğıöşü]{3,}\b',
        r'\b(reserves?|reserve|rezerv)\b',
    ]
    
    # KARANTINA: Belirsiz/riskli ligler (şimdilik Telegram kapalı)
    KARANTINA = KARANTINA_LIGLER
    
    # WHITELIST: Büyük / güvenilir ligler
    # WHITELIST: bilinçli dar tutulur.
    # Not: sadece 'premier league' veya 'super league' gibi genel ifadeler WHITELIST yapılmaz;
    # Mongolia/Taiwan/alt ligler bu yüzden NEUTRAL kalır.
    WHITELIST  = [
        'champions league', 'uefa champions', 'europa league', 'conference league',
        'germany bundesliga', 'german bundesliga', 'bundesliga', '2. bundesliga',
        'netherlands eredivisie', 'eredivisie',
        'england premier league', 'english premier league', 'england premier',
        'england championship', 'efl championship', 'championship',
        'italy serie a', 'italy serie b', 'serie a', 'serie b',
        'spain la liga', 'la liga',
        'france ligue 1', 'france ligue 2', 'ligue 1', 'ligue 2',
        'portugal primeira liga', 'primeira liga',
        'turkey super lig', 'turkey süper lig', 'turkiye super lig', 'türkiye süper lig',
        'turkey 1. lig', 'turkiye 1. lig', 'türkiye 1. lig',
        'scotland premiership', 'premiership',
        'denmark superligaen', 'superligaen',
        'sweden allsvenskan', 'allsvenskan',
        'norway eliteserien', 'eliteserien',
    ]

    @staticmethod
    def check_league(league_name, home_team, away_team) -> Tuple[bool, LeagueCategory, str]:
        """
        Lig kategorisini belirle.
        
        Döndürür:
            (allowed: bool, category: LeagueCategory, reason: str)
        """
        full  = f"{league_name} {home_team} {away_team}".lower()
        lig_l = league_name.lower()
        team_text = f"{home_team} {away_team}".lower()
        
        # HARD_REJECT kontrol
        for pat in LeagueFilter.HARD_REJECT_PATTERNS:
            if re.search(pat, full):
                return False, LeagueCategory.HARD_REJECT, f"HARD_REJECT:{pat}"
        
        # ANALYSIS_ONLY kontrol (kadın/genç/rezerv)
        for pat in LeagueFilter.ANALYSIS_ONLY_PATTERNS:
            if re.search(pat, full):
                return True, LeagueCategory.ANALYSIS_ONLY, f"ANALYSIS_ONLY:{pat}"

        # Takım adı üzerinden reserve/B/II kontrolü.
        # Lig adındaki 'III Liga' gibi ifadeler burada dikkate alınmaz.
        for pat in LeagueFilter.TEAM_ANALYSIS_ONLY_PATTERNS:
            if re.search(pat, team_text):
                return True, LeagueCategory.ANALYSIS_ONLY, f"ANALYSIS_ONLY_TEAM:{pat}"
        
        # KARANTINA kontrol
        for kw in LeagueFilter.KARANTINA:
            if kw in lig_l:
                return False, LeagueCategory.KARANTINA, f"KARANTINA:{kw}"
        
        # WHITELIST kontrol
        for kw in LeagueFilter.WHITELIST:
            if kw in lig_l:
                return True, LeagueCategory.WHITELIST, f"WHITELIST:{kw}"
        
        # DEFAULT: NEUTRAL
        return True, LeagueCategory.NEUTRAL, "NEUTRAL"

    @staticmethod
    def get_league_multiplier(league_name: str) -> float:
        ll = league_name.lower()
        if ('champions league' in ll or 'uefa champions' in ll):
            if any(x in ll for x in ['knockout','round of','quarter',
                                      'semi','final','last 16']):
                return 1.40
            return 1.85
        for kw, c in LIG_CARPANLARI.items():
            if kw in ll: return c
        return 1.0

    @staticmethod
    def get_da_threshold(league_name: str) -> float:
        ll = league_name.lower()
        if any(k in ll for k in ['bundesliga','eredivisie','u23','u21',
                                  'u20','u19','süper lig','super lig',
                                  'turkey','portugal']): return 1.3
        if any(k in ll for k in ['kuwait','egypt','third division',
                                  'regionalliga','amateur']): return 2.0
        return 1.5

    @staticmethod
    def is_karantina(league_name: str) -> bool:
        return any(k in league_name.lower() for k in KARANTINA_LIGLER)


# ============================================================================
# YARDIMCI FONKSİYONLAR
# ============================================================================

def guvenli_int(v, d=0):
    try: return int(float(v)) if v not in ('', None) else d
    except: return d

def guvenli_float(v, d=0.0):
    try: return float(v) if v not in ('', None) else d
    except: return d

def esnek_liste_duzelt(veri):
    duz = []
    if isinstance(veri, list):
        for e in veri: duz.extend(esnek_liste_duzelt(e))
    elif isinstance(veri, dict): duz.append(veri)
    return duz

def sigmoid(x: float) -> float:
    """[R8] Lojistik aktivasyon fonksiyonu"""
    return 1 / (1 + math.exp(-max(-500, min(500, x))))


# ============================================================================
# [R5] GAME STATE WEIGHT
# ============================================================================

def game_state_weight(takim_gol: int, rakip_gol: int) -> float:
    """
    [R5] Skor durumuna göre ağırlık.
    Geride: 0.85 (baskı psikolojik olarak şişirilir, normalize et)
    Berabere: 1.0
    Önde: 1.15 (gerçek üstünlük)
    """
    fark = takim_gol - rakip_gol
    if fark < 0:   return 0.85
    elif fark == 0: return 1.0
    else:           return 1.15


# ============================================================================
# [R4] PRESSURE WAVE CLUSTER
# ============================================================================

def pressure_wave_cluster(da_gecmis: List[int]) -> float:
    """
    [R4] Son 5dk DA yoğunluk skoru.
    Art arda gelen ataklar bağımsız ataklara göre üstel olarak daha değerli.
    da_gecmis: son 5 ölçümdeki DA değerleri listesi
    """
    if not da_gecmis or len(da_gecmis) < 2:
        return 1.0
    # Ardışık artış oranı
    artislar = sum(1 for i in range(1, len(da_gecmis))
                   if da_gecmis[i] > da_gecmis[i-1])
    oran = artislar / (len(da_gecmis) - 1)
    # 0.5 → 1.0, 1.0 → 1.5 (üstel)
    return round(1.0 + oran * 0.8, 3)


# ============================================================================
# [R2] PROXY xT SCORE
# ============================================================================

def proxy_xt_hesapla(sot: int, da: int, ta: int, korner: int,
                     takim_gol: int, rakip_gol: int,
                     dakika: int,
                     da_gecmis: Optional[List[int]] = None) -> Tuple[float, str]:
    """
    [R2] Proxy xT — koordinat verisi olmadan xT yaklaşımı.

    Rapor formülü (V51 sot_kalitesi_hesapla'nın upgrade'i):
    xT_proxy = (SOT/max(DA,1)) × (DA/max(TA,1)) × game_state_w × pressure_wave

    SOT/DA: şut isabet oranı — kalitesiz uzaktan şutları düşürür
    DA/TA:  atak penetrasyon kalitesi — gerçek tehdit / tüm ataklar
    game_state_w: skor dezavantajı normalize eder
    pressure_wave: ardışık baskı bonusu
    """
    if ta == 0 and da == 0 and sot == 0:
        return 0.0, "Veri yok"

    sot_da_oran  = sot / max(da, 1)    # şut isabet oranı
    da_ta_oran   = da  / max(ta, 1)    # penetrasyon kalitesi
    gsw          = game_state_weight(takim_gol, rakip_gol)
    pw           = pressure_wave_cluster(da_gecmis or [da])

    xt_proxy = sot_da_oran * da_ta_oran * gsw * pw

    # Dakika normalizasyonu (erken dakikalar daha değerli)
    xt_norm = xt_proxy * (45 / max(dakika, 1))

    if xt_norm >= 0.25:
        return round(xt_norm, 3), f"YÜKSEK xT({xt_norm:.3f}) GSW:{gsw} PW:{pw:.2f}"
    elif xt_norm >= 0.10:
        return round(xt_norm, 3), f"ORTA xT({xt_norm:.3f})"
    else:
        return round(xt_norm, 3), f"DÜŞÜK xT({xt_norm:.3f}) — steril baskı riski"


# ============================================================================
# [R3] F_PRESSURE ENDEKSİ (Corner + Attack Deficit Arbitrage)
# ============================================================================

def fpressure_endeks_hesapla(korner: int, sot: int, da: int,
                              dakika: int,
                              takim_gol: int, rakip_gol: int,
                              onceki_korner: int = 0,
                              onceki_sot: int = 0,
                              onceki_da: int = 0) -> Tuple[float, bool, str]:
    """
    [R3] Sahte baskı endeksi.
    F_pressure = (ΔKorner_10dk / (ΔSOT_10dk + 1)) × skor_carpan

    skor_carpan: gerideyse 1.5 (panik), beraberese 1.0, öndeyse 0.8
    F > 2.5 → sahte baskı + arbitraj fırsatı

    Rapor: korner/gol korelasyonu yalnızca 0.19
    """
    delta_korner = max(korner - onceki_korner, 0)
    delta_sot    = max(sot    - onceki_sot,    0)
    delta_da     = max(da     - onceki_da,     0)

    fark = takim_gol - rakip_gol
    if fark < 0:   skor_carpan = 1.5   # geride — panik baskısı
    elif fark == 0: skor_carpan = 1.0
    else:           skor_carpan = 0.8   # önde — kontrollü

    f = (delta_korner / (delta_sot + 1)) * skor_carpan

    # Ek kontrol: korner/dakika > 0.3 ve SOT düşük → [AH-8] erken baskı
    korner_per_dk = korner / max(dakika, 1)
    if korner_per_dk > 0.3 and sot < 3:
        f = max(f, 2.8)   # minimum sahte baskı seviyesi

    sahte = f > 2.5
    mesaj = (f"F_PRESSURE={f:.2f} → {'SAHTE BASKI ⚠️' if sahte else 'Normal'} "
             f"(ΔKorner:{delta_korner}, ΔSOT:{delta_sot})")
    return round(f, 3), sahte, mesaj


# ============================================================================
# [R6] SHANNON ENTROPİSİ
# ============================================================================

class MacEntropisi:
    """
    [R6] 15 dakikalık pencerede Shannon Entropisi.
    H = -Σ p_i × log2(p_i)

    Yüksek entropi: kaotik, git-gelli maç → gol olasılığı var
    Düşük entropi: tek takım domine / statik oyun → rölanti riski
    """

    def __init__(self):
        # {event_id: [(dakika, da, sot, korner), ...]}
        self._olaylar: Dict[str, List[Tuple]] = {}

    def olay_ekle(self, event_id: str, dakika: int,
                  da: int, sot: int, korner: int):
        if event_id not in self._olaylar:
            self._olaylar[event_id] = []
        self._olaylar[event_id].append((dakika, da, sot, korner))
        # 60 dakikadan eski kayıtları sil
        self._olaylar[event_id] = [
            o for o in self._olaylar[event_id] if dakika - o[0] <= 60
        ]

    def entropi_hesapla(self, event_id: str,
                        dakika: int) -> Tuple[float, str]:
        """15dk penceredeki DA + SOT dağılımının Shannon entropisi"""
        olaylar = self._olaylar.get(event_id, [])
        pencere = [o for o in olaylar if dakika - o[0] <= 15]

        if not pencere:
            return 0.5, "Veri yok — nötr entropi"

        # Her 5 dakikalık dilim için olay yoğunluğu
        dilimler = {}
        for o in pencere:
            dilim = (o[0] // 5) * 5
            dilimler[dilim] = dilimler.get(dilim, 0) + o[1] + o[2]

        toplam = sum(dilimler.values())
        if toplam == 0:
            return 0.0, "Olay yok — düşük entropi"

        # Shannon H = -Σ p_i × log2(p_i)
        H = 0.0
        for v in dilimler.values():
            p = v / toplam
            if p > 0:
                H -= p * math.log2(p)

        # Normalize et: maks entropi = log2(n_dilim)
        n_dilim = max(len(dilimler), 1)
        H_norm  = H / math.log2(n_dilim + 1)

        if H_norm >= 0.7:
            return round(H_norm, 3), f"YÜKSEK ENTROPİ({H_norm:.2f}) — kaotik maç"
        elif H_norm >= 0.4:
            return round(H_norm, 3), f"ORTA ENTROPİ({H_norm:.2f})"
        else:
            return round(H_norm, 3), f"DÜŞÜK ENTROPİ({H_norm:.2f}) — statik/rölanti"

    def match_state_score(self, event_id: str, dakika: int,
                          ev_gol: int, dep_gol: int) -> float:
        """
        [R7] MS_score = H(t) × e^(-λ(t-45)) × chaos(score_diff)
        λ = 0.03 (bookmaker time decay sabiti)
        """
        H, _ = self.entropi_hesapla(event_id, dakika)

        # Zaman erimesi (time decay)
        lam      = 0.03
        td       = math.exp(-lam * max(dakika - 45, 0))

        # Kaos fonksiyonu: beraberikente yüksek, fark açılınca düşer
        fark     = abs(ev_gol - dep_gol)
        chaos    = 1.0 / (1 + fark * 0.5)

        ms_score = H * td * chaos
        return round(ms_score, 3)


mac_entropisi = MacEntropisi()


# ============================================================================
# [R8] TVPS — TRUE VALUE PROBABILITY SCORE
# ============================================================================

class TVPSKatmani:
    """
    [R8] True Value Probability Score.
    Tüm modüllerin çıktılarını sigmoid ile 0-1'e sıkıştırır.
    Piyasa zımni olasılığıyla karşılaştırır.
    TVPS > 0.05 → +EV onayı

    [R9] Kelly Kriteri stake hesabı (Quarter Kelly)
    """

    TVPS_ESIGI = 0.05   # %5 edge minimum

    @staticmethod
    def hesapla(
        da_ivmesi:       float,
        proxy_xt:        float,
        ah_momentum:     float,
        true_rlm_prob:   float,
        corner_deficit:  bool,
        sahte_baski:     bool,
        fpressure:       float,
        entropi:         float,
        skor_bonus:      float,
        lig_carpan:      float,
        market_odds:     float
    ) -> Tuple[float, float, bool, str]:
        """
        Döner: (tvps_score, kelly_stake, ev_pozitif, aciklama)
        """
        if market_odds <= 1.0:
            return 0.0, 0.0, False, "Geçersiz oran"

        # Zımni olasılık (vig arındırılmış yaklaşım)
        implied_prob = 1 / market_odds

        # Feature vektörü × ağırlıklar
        ham_skor = (
            TVPS_AGIRLIKLAR['da_ivmesi']      * min(da_ivmesi, 3.0)    +
            TVPS_AGIRLIKLAR['proxy_xt']       * min(proxy_xt * 10, 3.0) +
            TVPS_AGIRLIKLAR['ah_momentum']    * min(abs(ah_momentum), 2.0) +
            TVPS_AGIRLIKLAR['true_rlm']       * true_rlm_prob          +
            TVPS_AGIRLIKLAR['corner_deficit'] * (1.0 if corner_deficit else 0.0) +
            TVPS_AGIRLIKLAR['sahte_baski']    * (1.0 if sahte_baski else 0.0)    +
            TVPS_AGIRLIKLAR['fpressure_endeks'] * min(fpressure / 5.0, 1.0) +
            TVPS_AGIRLIKLAR['entropi_yuksek'] * min(entropi, 1.0)            +
            TVPS_AGIRLIKLAR['skor_altin']     * (skor_bonus / 3.0)           +
            TVPS_AGIRLIKLAR['lig_carpan_bonus'] * (lig_carpan - 1.0)
        )

        # Gerçek olasılık (sigmoid ile 0-1 arasına)
        true_prob = sigmoid(ham_skor)

        # TVPS = true_prob / implied_prob - 1 (göreceli avantaj)
        tvps = (true_prob / max(implied_prob, 0.001)) - 1.0

        ev_pozitif = tvps > TVPSKatmani.TVPS_ESIGI

        # [R9] Quarter Kelly stake
        # K = (TVPS) / (odds - 1) × 0.25
        kelly_tam = tvps / max(market_odds - 1, 0.01)
        kelly_q   = max(0.0, min(kelly_tam * 0.25, 0.05))  # maks %5 kasa

        aciklama = (
            f"TVPS:{tvps:+.3f} | TrueP:{true_prob:.2%} | "
            f"ImpliedP:{implied_prob:.2%} | "
            f"Kelly¼:{kelly_q:.2%} kasa | "
            f"{'✅ +EV' if ev_pozitif else '❌ -EV'}"
        )

        return round(tvps, 4), round(kelly_q, 4), ev_pozitif, aciklama


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
        # ── 8. KESİN İPTAL KURALLARI (Sahte Baskı — Kısır Atak) ────────────
        # DA>=45 ve SOT=0 → devasa atak hacmi ama hiç isabetli şut yok
        # Bu tamamen kısır / verimsiz baskıdır, sinyal üretmez
        if self.da >= 45 and self.sot == 0: return True
        # DA>=60 ve SOT<=3 → 60 atakta sadece 3 isabetli şut, oran çok düşük
        if self.da >= 60 and self.sot <= 3: return True
        # ── Mevcut kurallar (korundu) ────────────────────────────────────────
        if self.da > 8 and self.sot == 0: return True
        if self.da > 0 and self.sot > 0 and self.da / self.sot > 8: return True
        if self.korner >= 8 and self.sot < 5: return True
        return False

    def korner_orani(self, dakika: int) -> float:
        return self.korner / max(dakika, 1)


class MatchDataProtection:
    @staticmethod
    def validate_match_data(home: TeamStats,
                            away: TeamStats) -> Tuple[bool, List[str]]:
        errs = []
        ok_h, e_h = home.validate_hierarchy()
        if not ok_h: errs.extend([f"EV:{e}" for e in e_h])
        ok_a, e_a = away.validate_hierarchy()
        if not ok_a: errs.extend([f"DEP:{e}" for e in e_a])
        if home.gol + away.gol >= 5: errs.append("KOPMUŞ MAÇ(≥5)")
        return len(errs) == 0, errs


# ============================================================================
# SKOR DURUMU FİLTRESİ
# ============================================================================

def skor_durumu_kontrol(ev_gol: int,
                        dep_gol: int) -> Tuple[bool, str, float]:
    toplam = ev_gol + dep_gol
    fark   = abs(ev_gol - dep_gol)
    if toplam >= 5: return False, "KAOS",    0.0
    if fark   >= 3: return False, "ROLANTI", 0.0
    # [V57-B] 0-0 artık bloklanmıyor — Ev/Dep Gol Atacak için kritik durum
    if toplam == 0: return True,  "SIFIR",   0.0
    if toplam == 1: return True,  "DUSUK",  -3.0
    if toplam == 2: return True,  "NORMAL", -1.0
    if toplam == 3: return True,  "IYI",    +2.0
    return             True,  "ALTIN",  +3.0


# ============================================================================
# SİNYAL TİPLERİ
# ============================================================================

class SignalType(Enum):
    IY_GOL  = "İY GOL"
    EV_GOL  = "EV GOL"
    DEP_GOL = "DEP GOL"
    IY2_GOL = "İY2 GOL"
    IY2_GEC = "İY2 GEÇ"


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
              league_name: str = "",
              event_id: str = "") -> SignalResult:

        if not (15 <= minute <= 40):
            return SignalResult(False, None, 0.0, "Dakika dışı", {})
        if home_score + away_score > 1:
            return SignalResult(False, None, 0.0, "İY skor yüksek", {})

        total_da   = home.da + away.da
        da_esik    = LeagueFilter.get_da_threshold(league_name)
        da_per_min = total_da / minute if minute > 0 else 0

        if da_per_min < da_esik:
            return SignalResult(False, None, 0.0,
                f"DA düşük:{da_per_min:.2f}<{da_esik}", {})

        # [AH-8] Erken aşırı korner filtresi
        toplam_korner = home.korner + away.korner
        if (toplam_korner / max(minute, 1)) > 0.3 and home.sot + away.sot < 3:
            return SignalResult(False, None, 0.0,
                "Sahte baskı: Yüksek köşe/dk, düşük SOT", {})

        # [R2] Proxy xT
        xt, xt_msg = proxy_xt_hesapla(
            home.sot, home.da, home.ta, home.korner,
            home_score, away_score, minute)

        score  = 5.0 + min(da_per_min * 2, 5.0) + xt * 5
        reason = f"İY Gol — DA:{da_per_min:.2f} | {xt_msg}"
        if 24 <= minute <= 36:
            score  *= 1.8
            reason += " | ALTIN PENCERE(×1.8)"

        # [R7] Entropi bonusu
        if event_id:
            ms = mac_entropisi.match_state_score(
                event_id, minute, home_score, away_score)
            if ms > 0.4:
                score  += ms * 3
                reason += f" | MS:{ms:.2f}"

        return SignalResult(True, SignalType.IY_GOL,
                            round(score, 2), reason,
                            {'da_per_min': round(da_per_min, 2), 'xt': xt})


# ============================================================================
# EV/DEPLASMAN GOL MODÜLÜ
# ============================================================================

class EvDepGolModule:
    @staticmethod
    def check(minute: int, home: TeamStats, away: TeamStats,
              ah_home: float, ah_away: float,
              league_name: str = "",
              event_id: str = "",
              ev_gol: int = 0, dep_gol: int = 0,
              market_odds: float = 0.0) -> SignalResult:

        if not (20 <= minute <= 80):
            return SignalResult(False, None, 0.0, "Dakika dışı", {})

        total_da = home.da + away.da
        if total_da == 0:
            return SignalResult(False, None, 0.0, "DA yok", {})

        home_da_ratio = home.da / total_da
        away_da_ratio = away.da / total_da

        # Baskın takım tespiti
        if home_da_ratio > 0.6:
            dom, dom_ratio, sig_type = "HOME", home_da_ratio, SignalType.EV_GOL
            if ah_home >= 0:
                return SignalResult(False, None, 0.0, "Ev favori değil(AH)", {})
            dom_stats, dom_ah = home, ah_home
            t_gol, r_gol = ev_gol, dep_gol
        elif away_da_ratio > 0.6:
            dom, dom_ratio, sig_type = "AWAY", away_da_ratio, SignalType.DEP_GOL
            if ah_away <= 0:
                return SignalResult(False, None, 0.0, "Dep favori değil(AH)", {})
            dom_stats, dom_ah = away, ah_away
            t_gol, r_gol = dep_gol, ev_gol
        else:
            # [AH-5] Corner deficit Signal Beta
            corner_deficit_home = (home.korner < away.korner
                                   and abs(ah_home) <= 0.50 and home.sot >= away.sot)
            corner_deficit_away = (away.korner < home.korner
                                   and abs(ah_away) <= 0.50 and away.sot >= home.sot)
            if corner_deficit_home or corner_deficit_away:
                dom = "HOME" if corner_deficit_home else "AWAY"
                dom_stats = home if dom == "HOME" else away
                dom_ah    = ah_home if dom == "HOME" else ah_away
                sig_type  = SignalType.EV_GOL if dom == "HOME" else SignalType.DEP_GOL
                dom_ratio = 0.50
                t_gol = ev_gol if dom == "HOME" else dep_gol
                r_gol = dep_gol if dom == "HOME" else ev_gol
            else:
                return SignalResult(False, None, 0.0, "Baskın yok", {})

        if dom_stats.detect_fake_pressure():
            return SignalResult(False, None, 0.0, "Sahte baskı", {})

        # [R3] F_pressure endeksi
        f_val, sahte_f, f_msg = fpressure_endeks_hesapla(
            dom_stats.korner, dom_stats.sot, dom_stats.da,
            minute, t_gol, r_gol)
        if sahte_f:
            return SignalResult(False, None, 0.0, f"F_pressure: {f_msg}", {})

        score  = 6.0 + (dom_ratio - 0.5) * 10
        reason = f"{dom} baskın"

        # [AH-1] Split analizi
        ah_split  = ah_split_hesapla(dom_ah)
        score    += ah_split.bonus_puan
        reason   += f" | SPLIT:{ah_split.tip}(+{ah_split.bonus_puan:.1f})"

        # [R1] AH kinetik
        kinetik = AHKinetik()
        if event_id:
            kinetik = ah_hareket.kinetik_hesapla(
                event_id, dom_ah,
                abs(ev_gol - dep_gol), minute)
            if kinetik.yon == 'daralma' and kinetik.clv_proxy > 0.1:
                score  += 2.5
                reason += f" | AH_DARALMA(v:{kinetik.velocity:+.3f})"
            elif kinetik.yon == 'genisleme':
                score  -= 1.5
                reason += f" | AH_GENİŞLİYOR(v:{kinetik.velocity:+.3f})"

            # AH ivme bonusu
            if kinetik.acceleration > 0.05:
                score  += 1.0
                reason += f" | AH_İVME(a:{kinetik.acceleration:+.3f})"

        # [R3] RLM skoru
        if event_id:
            rlm_p, rlm_tip = ah_hareket.rlm_skoru(
                event_id, home_da_ratio, dom_ah)
            if rlm_tip == "TRUE_RLM":
                score  += 2.0
                reason += f" | TRUE_RLM({rlm_p:.0%})"
            elif rlm_tip == "FAKE_RLM":
                score  -= 2.0
                reason += f" | FAKE_RLM({rlm_p:.0%}) ⚠️"

        # [R2] Proxy xT
        xt, xt_msg = proxy_xt_hesapla(
            dom_stats.sot, dom_stats.da, dom_stats.ta,
            dom_stats.korner, t_gol, r_gol, minute)
        score  += xt * 8
        reason += f" | {xt_msg}"

        # [AH-5] Corner deficit bonus
        if dom_stats.korner < (away.korner if dom == "HOME" else home.korner):
            c_fark = ((away.korner if dom == "HOME" else home.korner)
                      - dom_stats.korner)
            if c_fark >= 3 and abs(dom_ah) <= 0.50:
                score  += 3.0
                reason += f" | SIGNAL_BETA(Δkorner:{c_fark})"

        # [R7] Match state entropy
        if event_id:
            ms = mac_entropisi.match_state_score(
                event_id, minute, ev_gol, dep_gol)
            if ms > 0.3:
                score  += ms * 2
                reason += f" | MS_ENTROPY:{ms:.2f}"

        # [R10] Implied prob drift
        if event_id:
            drift = ah_hareket.implied_prob_drift(event_id)
            if drift > 0.02:
                score  += 1.5
                reason += f" | IMPL_DRIFT:+{drift:.3f}"

        # [R8] TVPS değerlendirmesi (market_odds varsa)
        tvps_str = ""
        if market_odds > 1.0:
            tvps, kelly, ev_ok, tvps_msg = TVPSKatmani.hesapla(
                da_ivmesi=home_da_ratio if dom == "HOME" else away_da_ratio,
                proxy_xt=xt,
                ah_momentum=kinetik.momentum_score,
                true_rlm_prob=0.0,
                corner_deficit=(dom_ratio == 0.50),
                sahte_baski=False,
                fpressure=f_val,
                entropi=mac_entropisi.entropi_hesapla(event_id or '', minute)[0] if event_id else 0.5,
                skor_bonus=0.0,
                lig_carpan=LeagueFilter.get_league_multiplier(league_name),
                market_odds=market_odds
            )
            if not ev_ok:
                score  *= 0.7   # -EV ise puanı düşür
                reason += " | -EV(TVPS)"
            else:
                reason += f" | +EV(TVPS:{tvps:+.3f} K:{kelly:.2%})"
            tvps_str = tvps_msg

        return SignalResult(True, sig_type, round(score, 2), reason, {
            'dom': dom, 'da_ratio': round(dom_ratio, 2),
            'ah_split': ah_split.tip,
            'xt': xt, 'fpressure': f_val,
            'ah_velocity': kinetik.velocity,
            'tvps': tvps_str
        })


# ============================================================================
# İKİNCİ YARI GOL MODÜLÜ
# ============================================================================

class IY2Module:
    @staticmethod
    def check(minute: int, home: TeamStats, away: TeamStats,
              home_score: int, away_score: int,
              league_name: str = "",
              event_id: str = "") -> SignalResult:

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
                f"Rölanti:momentum({da_per_mn:.2f})", {})
        if da_per_mn < 1.0:
            return SignalResult(False, None, 0.0, "DA düşük", {})

        # [R3] F_pressure kontrolü
        f_val, sahte_f, _ = fpressure_endeks_hesapla(
            home.korner + away.korner,
            home.sot + away.sot,
            total_da, minute, home_score, away_score)
        if sahte_f:
            return SignalResult(False, None, 0.0,
                f"F_pressure sahte baskı", {})

        score  = base + min(da_per_mn, 3.0)
        reason = f"İY2 {window}"

        if 48 <= minute <= 58:
            score  *= 2.0
            reason += " | KIRILMA(×2.0)"

        # [R7] Entropi + time decay
        if event_id:
            ms = mac_entropisi.match_state_score(
                event_id, minute, home_score, away_score)
            if ms > 0.35:
                score  += ms * 4
                reason += f" | MS:{ms:.2f}"
            elif ms < 0.15:
                score  *= 0.8
                reason += f" | DÜŞÜK_ENTROPI(×0.8)"

        return SignalResult(True, sig_t, round(score, 2),
                            reason, {'da_per_min': round(da_per_mn, 2),
                                     'f_pressure': f_val})


# ============================================================================
# SİNYAL KONSENSÜS MOTORU
# ============================================================================

class SinyalKonsensus:
    @staticmethod
    def sec(sinyaller: List[SignalResult]) -> Optional[SignalResult]:
        gecerli = [s for s in sinyaller if s and s.valid]
        if not gecerli: return None
        en_iyi = max(gecerli, key=lambda s: s.score)
        if len(gecerli) > 1:
            en_iyi.score  = round(en_iyi.score * 1.15, 2)
            en_iyi.reason += f" | KONSENSÜS(×1.15,{len(gecerli)}mod)"
        return en_iyi


# ============================================================================
# VERİ KORUMA KATMANI
# ============================================================================

class VeriKorumaKatmani:
    def __init__(self):
        self.s_kod   = {'S1':'SOT','S2':'Korner','S3':'TA','S4':'DA','SC':'Gol'}
        self.anomali = 0
        self.toplam  = 0

    def yeni_format_parse(self, stats):
        try:
            if 'corners' in stats and isinstance(stats.get('corners'), list):
                def _g(k, i): return stats.get(k, ['0','0'])[i]
                ev  = {'S1':_g('on_target',0),'S2':_g('corners',0),
                       'S3':_g('attacks',0),'S4':_g('dangerous_attacks',0),
                       'SC':_g('goals',0)}
                dep = {'S1':_g('on_target',1),'S2':_g('corners',1),
                       'S3':_g('attacks',1),'S4':_g('dangerous_attacks',1),
                       'SC':_g('goals',1)}
                return ev, dep
        except Exception as e:
            logger.error(f"Parse:{e}")
        return None

    def veri_cikart_guvenli(self, ev_v, dep_v):
        self.toplam += 1
        ters = {v:k for k,v in self.s_kod.items()}
        try:
            veri = {
                'ev_sot':     guvenli_int(ev_v.get(ters.get('SOT',    'S1'), 0)),
                'ev_korner':  guvenli_int(ev_v.get(ters.get('Korner', 'S2'), 0)),
                'ev_ta':      guvenli_int(ev_v.get(ters.get('TA',     'S3'), 0)),
                'ev_da':      guvenli_int(ev_v.get(ters.get('DA',     'S4'), 0)),
                'ev_gol':     guvenli_int(ev_v.get(ters.get('Gol',    'SC'), 0)),
                'dep_sot':    guvenli_int(dep_v.get(ters.get('SOT',   'S1'), 0)),
                'dep_korner': guvenli_int(dep_v.get(ters.get('Korner','S2'), 0)),
                'dep_ta':     guvenli_int(dep_v.get(ters.get('TA',    'S3'), 0)),
                'dep_da':     guvenli_int(dep_v.get(ters.get('DA',    'S4'), 0)),
                'dep_gol':    guvenli_int(dep_v.get(ters.get('Gol',   'SC'), 0)),
                # [K2] Kırmızı kart verileri — S7 kodu
                'ev_kirmizi':  guvenli_int(ev_v.get('S7', 0)),
                'dep_kirmizi': guvenli_int(dep_v.get('S7', 0)),
            }
            ta  = veri['ev_ta']  + veri['dep_ta']
            da  = veri['ev_da']  + veri['dep_da']
            sot = veri['ev_sot'] + veri['dep_sot']
            gol = veri['ev_gol'] + veri['dep_gol']
            if ta < da or da < sot or sot < gol: self.anomali += 1
            return veri
        except Exception as e:
            logger.error(f"veri_cikart:{e}")
            return None

    def istatistik(self):
        if self.toplam:
            logger.info(f"VeriKoruma:{self.toplam} kontrol, "
                        f"{self.anomali} anomali "
                        f"(%{self.anomali/self.toplam*100:.1f})")


veri_koruma = VeriKorumaKatmani()


def veri_cikart(ev_v, dep_v) -> dict:
    sonuc = veri_koruma.veri_cikart_guvenli(ev_v, dep_v)
    if sonuc is None:
        return {k: 0 for k in ['ev_sot','ev_korner','ev_ta','ev_da','ev_gol',
                                'dep_sot','dep_korner','dep_ta','dep_da','dep_gol',
                                'ev_kirmizi','dep_kirmizi']}
    return sonuc


# ============================================================================
# NESİNE KONTROLÜ
# ============================================================================

def nesine_lig_kontrolu(league_name, ev_adi, dep_adi) -> bool:
    full = f"{league_name} {ev_adi} {dep_adi}".lower()
    for pat in LeagueFilter.HARD_REJECT_PATTERNS:
        if re.search(pat, full): return False
    nesine = [
        'super lig','süper lig','premier league','championship',
        'la liga','bundesliga','2. bundesliga','serie a','serie b',
        'ligue 1','ligue 2','eredivisie','primeira liga',
        'champions league','europa league','conference league',
    ]
    return any(n in league_name.lower() for n in nesine)


# ============================================================================
# ADAPTİF DÖNGÜ & PUAN BARAJI
# ============================================================================

def dongu_suresi_hesapla(maclar: list) -> int:
    for m in maclar:
        t  = m.get('timer', {})
        dk = guvenli_int(t.get('tm', 0)) if isinstance(t, dict) else 0
        if (22 <= dk <= 38) or (46 <= dk <= 60): return 20
    return 60


def puan_baraji_hesapla(dakika: int, league_name: str) -> float:
    c = LeagueFilter.get_league_multiplier(league_name)
    if c >= 1.5: return 7.0
    if c <= 0.8: return 6.0
    return 6.5


# ============================================================================
# AI ANALİZCİLER
# ============================================================================

class GrokAIAnalyzer:
    def __init__(self): self.api_key = GROK_API_KEY

    async def analiz_yap(self, mac_v: dict, session) -> Optional[str]:
        if not self.api_key: return None
        try:
            prompt = (
                f"Futbol analistiyim. MAX 350 karakter:\n"
                f"MAÇ: {mac_v['ev_adi']} {mac_v['skor']} "
                f"{mac_v['dep_adi']} ({mac_v['dakika']}')\n"
                f"TA:{mac_v['ev_ta']}/{mac_v['dep_ta']} "
                f"DA:{mac_v['ev_da']}/{mac_v['dep_da']} "
                f"SOT:{mac_v['ev_sot']}/{mac_v['dep_sot']}\n"
                f"Proxy xT, sahte baskı, TVPS yorum?"
            )
            async with api_rate_limiter:
                async with session.post(
                    "https://api.x.ai/v1/chat/completions",
                    json={"model":"grok-beta",
                          "messages":[{"role":"user","content":prompt}],
                          "temperature":0.85,"max_tokens":400},
                    headers={"Authorization":f"Bearer {self.api_key}",
                             "Content-Type":"application/json"},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as r:
                    if r.status != 200: return None
                    data = await r.json()
            if data.get('choices'):
                return data['choices'][0]['message']['content']
        except Exception as e:
            logger.debug(f"Grok:{e}")
        return None


class GeminiAIAnalyzer:
    def __init__(self):
        self.keys = [k for k in [GEMINI_API_KEY_1,
                                  GEMINI_API_KEY_2,
                                  GEMINI_API_KEY_3] if k]
        self.idx = 0

    def _key(self):
        if not self.keys: return None
        k = self.keys[self.idx]
        self.idx = (self.idx + 1) % len(self.keys)
        return k

    async def analiz_yap(self, mac_v: dict, session) -> Optional[str]:
        key = self._key()
        if not key: return None
        try:
            prompt = (
                f"Kısa analiz MAX 300 karakter: "
                f"{mac_v['ev_adi']} {mac_v['skor']} "
                f"{mac_v['dep_adi']} ({mac_v['dakika']}'). "
                f"TA:{mac_v['ev_ta']}/{mac_v['dep_ta']} "
                f"DA:{mac_v['ev_da']}/{mac_v['dep_da']}. "
                f"xT, sahte baskı, +EV?"
            )
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"gemini-2.0-flash:generateContent?key={key}")
            async with session.post(
                url,
                json={"contents":[{"parts":[{"text":prompt}]}],
                      "generationConfig":{"temperature":0.85,"maxOutputTokens":400}},
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                if r.status != 200: return None
                data = await r.json()
            if data.get('candidates'):
                return data['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            logger.debug(f"Gemini:{e}")
        return None


grok_ai   = GrokAIAnalyzer()
gemini_ai = GeminiAIAnalyzer()


async def ai_analiz_yap(mac_v, session):
    if grok_ai.api_key:
        r = await grok_ai.analiz_yap(mac_v, session)
        if r: return r, "Grok"
    if gemini_ai.keys:
        r = await gemini_ai.analiz_yap(mac_v, session)
        if r: return r, "Gemini"
    return None, None


# ============================================================================
# ASIAN HANDICAP ÇEK
# ============================================================================

async def asian_handicap_cek(event_id: str,
                              session) -> Optional[dict]:
    try:
        async with api_rate_limiter:
            async with session.get(
                f"https://api.betsapi.com/v1/event/odds"
                f"?token={BETSAPI_TOKEN}&event_id={event_id}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200: return None
                data = await resp.json()

        if data.get('success') != 1: return None
        results = data.get('results', {})
        if not isinstance(results, dict): return None

        asian_data = None
        for key in ['1_2','asian_handicap','ah','handicap']:
            if key in results: asian_data = results[key]; break
        if not asian_data:
            for key in results:
                if any(x in str(key).lower() for x in ['asian','handicap','ah']):
                    asian_data = results[key]; break
        if not asian_data: return None

        ev_h = dep_h = ev_o = dep_o = 0.0
        if isinstance(asian_data, list) and asian_data:
            lat = asian_data[0]
            if isinstance(lat, dict):
                h    = guvenli_float(lat.get('handicap', 0))
                ev_h = h; dep_h = -h
                ev_o = guvenli_float(lat.get('home_od', 0))
                dep_o = guvenli_float(lat.get('away_od', 0))
        elif isinstance(asian_data, dict) and 'home' in asian_data:
            ev_h  = guvenli_float(asian_data['home'].get('handicap', 0))
            ev_o  = guvenli_float(asian_data['home'].get('odds', 0))
            dep_h = guvenli_float(asian_data['away'].get('handicap', 0))
            dep_o = guvenli_float(asian_data['away'].get('odds', 0))

        if ev_o > 0 and dep_o > 0:
            ah_hareket.kaydet(event_id, ev_h, dep_h, ev_o, dep_o)
            return {'ev_handicap': ev_h, 'dep_handicap': dep_h,
                    'ev_oran': ev_o,     'dep_oran': dep_o}
        return None

    except asyncio.TimeoutError: return None
    except Exception as e:
        logger.error(f"AH:{e}"); return None


# ============================================================================
# SELF-TEST FONKSIYONLARI
# ============================================================================

def self_test_classify_live_signal():
    """classify_live_signal() fonksiyonunun önemli kural testleri"""
    tests = [
        {
            "name": "29dk 1-0 Gol Olacak IGNORE",
            "args": ("Gol Olacak (S)", 29, 1, 0, 1, 2, -0.75),
            "expected": "IGNORE",
        },
        {
            "name": "59dk 1-0 Gol Olacak IGNORE",
            "args": ("Gol Olacak (S)", 59, 1, 0, 1, 2, -1.25),
            "expected": "IGNORE",
        },
        {
            "name": "60dk 1-0 Gol Olacak A+",
            "args": ("Gol Olacak (S)", 60, 1, 0, 1, 0, -1.25),
            "expected_in": ("A+", "A"),
        },
        {
            "name": "60dk 1-0 Gol Olacak AH=0.5 PASS",
            "args": ("Gol Olacak (S)", 60, 1, 0, 1, 2, 0.5),
            "expected": "PASS",
        },
        {
            "name": "50dk Ev Gol AH=0.5 PASS",
            "args": ("Ev Gol Atacak (S)", 50, 1, 0, 1, 4, -0.50),
            "expected": "PASS",
        },
        {
            "name": "5dk 0-0 Gol Olacak <50 PASS",
            "args": ("Gol Olacak (S)", 5, 0, 0, 0, 0, -1.75),
            "expected": "PASS",
        },
        {
            "name": "45dk Ev Gol 0-0 AH=0 B/log-only",
            "args": ("Ev Gol Atacak (S)", 45, 0, 0, 1, 2, 0.0),
            "expected_in": ("B", "LOW_VALUE", "PASS"),
        },
    ]

    logger.info("[SELFTEST] classify_live_signal() testleri başladı")
    passed = 0
    failed = 0
    
    for t in tests:
        sonuc = classify_live_signal(*t["args"])
        expected = t.get("expected")
        expected_in = t.get("expected_in", ())
        
        if expected:
            ok = sonuc["sinyal"] == expected
        else:
            ok = sonuc["sinyal"] in expected_in
        
        if ok:
            logger.info(f"  ✅ {t['name']} → {sonuc['sinyal']}")
            passed += 1
        else:
            logger.warning(f"  ❌ {t['name']} → {sonuc['sinyal']} "
                          f"(beklenen: {expected or expected_in})")
            failed += 1
    
    logger.info(f"[SELFTEST] Sonuç: {passed} geçti, {failed} başarısız")
    return failed == 0


def self_test_league_categories():
    """LeagueFilter lig kategorileme testleri"""
    tests = [
        ("England Women Super League", "Chelsea Women", "Arsenal Women", 
         LeagueCategory.ANALYSIS_ONLY),
        ("Germany Bundesliga", "Bayern", "Dortmund", 
         LeagueCategory.WHITELIST),
        ("Taiwan Premier League", "A", "B", 
         LeagueCategory.NEUTRAL),
        ("eSoccer Battle", "A", "B", 
         LeagueCategory.HARD_REJECT),
        ("Brazil Serie A", "A", "B", 
         LeagueCategory.KARANTINA),
    ]

    logger.info("[SELFTEST] LeagueFilter lig kategorileme testleri başladı")
    passed = 0
    failed = 0
    
    for league, home, away, expected_cat in tests:
        allowed, category, reason = LeagueFilter.check_league(league, home, away)
        
        if category == expected_cat:
            logger.info(f"  ✅ {league} → {category.value}")
            passed += 1
        else:
            logger.warning(f"  ❌ {league} → {category.value} "
                          f"(beklenen: {expected_cat.value})")
            failed += 1
    
    logger.info(f"[SELFTEST] Sonuç: {passed} geçti, {failed} başarısız")
    return failed == 0


# ============================================================================
# ANA ANALİZ MOTORU
# ============================================================================

async def mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk,
                        bot, session,
                        event_id: str = "",
                        league_name: str = ""):
    try:
        v = veri_cikart(ev_v, dep_v)

        # [K2] Kırmızı kart kalkanı — kırmızı kart varsa sinyal üretme
        ev_kirmizi  = int(v.get('ev_kirmizi', 0) or 0)
        dep_kirmizi = int(v.get('dep_kirmizi', 0) or 0)
        if ev_kirmizi > 0 or dep_kirmizi > 0:
            logger.debug(f'Kırmızı kart: ev={ev_kirmizi} dep={dep_kirmizi} — sinyal üretilmedi')
            return None

        # [K1] Skor körlüğünü gider — skor string'ini split ile doğrudan oku
        try:
            _parts = str(skor).split('-')
            ev_gol_skor  = int(_parts[0].strip())
            dep_gol_skor = int(_parts[1].strip())
        except Exception:
            ev_gol_skor  = guvenli_int(v.get('ev_gol', 0))
            dep_gol_skor = guvenli_int(v.get('dep_gol', 0))

        home_stats = TeamStats(ta=v['ev_ta'],   da=v['ev_da'],
                               sot=v['ev_sot'],  gol=ev_gol_skor,
                               korner=v.get('ev_korner', 0))
        away_stats = TeamStats(ta=v['dep_ta'],  da=v['dep_da'],
                               sot=v['dep_sot'], gol=dep_gol_skor,
                               korner=v.get('dep_korner', 0))

        ev_gol     = ev_gol_skor
        dep_gol    = dep_gol_skor
        toplam_gol = ev_gol + dep_gol
        ta  = home_stats.ta  + away_stats.ta
        da  = home_stats.da  + away_stats.da
        sot = home_stats.sot + away_stats.sot

        # Veri kalitesi
        ok, errs = MatchDataProtection.validate_match_data(home_stats, away_stats)
        if not ok: return None

        # [V57-FIX-6] LİG KATEGORİSİ KONTROLü
        lig_allowed, lig_category, lig_reason = LeagueFilter.check_league(
            league_name, ev_adi, dep_adi)
        
        logger.debug(f"[LEAGUE] {league_name}: {lig_category.value} | {lig_reason}")
        
        # HARD_REJECT: Tamamen reddet
        if lig_category == LeagueCategory.HARD_REJECT:
            logger.debug(f"[LEAGUE] Hard reject: {lig_reason}")
            return None
        
        # KARANTINA: Şimdilik Telegram kapalı
        if lig_category == LeagueCategory.KARANTINA:
            logger.info(f"[LEAGUE] Karantina lig, Telegram kapalı: {lig_reason}")
            return None
        
        if abs(ev_gol - dep_gol) >= 3: return None

        skor_ok, skor_d, skor_bonus = skor_durumu_kontrol(ev_gol, dep_gol)
        if not skor_ok: return None

        # [R6] Entropi güncellemesi
        if event_id:
            mac_entropisi.olay_ekle(event_id, dk, da, sot,
                                     home_stats.korner + away_stats.korner)

        # [V57-FIX-5] AH VERİSİ KONTROLÜ - REQUIRE_AH_FOR_SIGNAL
        # AH çekme her zaman lazım (classify_live_signal için de)
        ah_data = None
        if event_id and (5 <= dk <= 80):
            ah_data = await asian_handicap_cek(event_id, session)
        
        if REQUIRE_AH_FOR_SIGNAL and not ah_data:
            if lig_category == LeagueCategory.ANALYSIS_ONLY and ENABLE_ANALYSIS_ONLY_LOGGING:
                try:
                    analysis_only_signals.kaydet(
                        event_id        = event_id,
                        league_name     = league_name,
                        category        = lig_category.value,
                        home_team       = ev_adi,
                        away_team       = dep_adi,
                        dakika          = int(dk),
                        skor            = skor,
                        home_goals      = ev_gol,
                        away_goals      = dep_gol,
                        home_corners    = home_stats.korner,
                        away_corners    = away_stats.korner,
                        ah              = 0.0,
                        tahmin_tipi     = "AH_MISSING",
                        sinyal_dict     = {
                            "sinyal": "AH_MISSING",
                            "puan": 0,
                            "neden": ["AH verisi yok, sadece analiz için kaydedildi"]
                        }
                    )
                except Exception as _ahlog_err:
                    logger.debug(f"[ANALYSIS_ONLY] AH_MISSING log hatası: {_ahlog_err}")
            logger.debug("[V57] AH verisi yok, sinyal atlandı")
            return None
        
        # [V57] Legacy modülleri DISABLE — ENABLE_LEGACY_MODULES = False iken çalışmazlar
        # IYGolModule / EvDepGolModule / IY2Module / SinyalKonsensus
        # Ama dosyada kalabilir, "💎 SİNYAL" hiçbir zaman Telegram'a gitmez.
        sinyaller: List[SignalResult] = []
        sinyal = None

        if ENABLE_LEGACY_MODULES:
            if 15 <= dk <= 40:
                s = IYGolModule.check(dk, ev_gol, dep_gol,
                                      home_stats, away_stats, league_name, event_id)
                sinyaller.append(s)

            if (46 <= dk <= 65) or (76 <= dk <= 90):
                s = IY2Module.check(dk, home_stats, away_stats,
                                    ev_gol, dep_gol, league_name, event_id)
                sinyaller.append(s)

            if ah_data and 20 <= dk <= 80:
                market_odds = ah_data.get('ev_oran', 0.0)
                s = EvDepGolModule.check(
                    dk, home_stats, away_stats,
                    ah_data['ev_handicap'], ah_data['dep_handicap'],
                    league_name, event_id,
                    ev_gol, dep_gol, market_odds
                )
                sinyaller.append(s)
            sinyal = SinyalKonsensus.sec(sinyaller)

        # ══════════════════════════════════════════════════════════════════
        # [V57] CANLI SİNYAL SINIFLANDIRICI — classify_live_signal()
        # ══════════════════════════════════════════════════════════════════
        # [V57-FIX-5] Tek mesaj: üç tip için hesapla, en yüksek puanlı A+/A
        # gönder. B sadece loglanır. LOW_VALUE/PASS/IGNORE sessiz kalır.
        # ══════════════════════════════════════════════════════════════════
        try:
            _cs_ah    = float(ah_data['ev_handicap']) if ah_data else 0.0
            _cs_ev_c  = int(v.get('ev_korner', 0) or 0)
            _cs_dep_c = int(v.get('dep_korner', 0) or 0)
            _adaylar  = []   # (puan, sinyal_kodu, mesaj, tip)

            # [V57-FIX-6] ANALYSIS_ONLY kategorisinde sinyalleri kaydet fakat Telegram'a gönderme
            if lig_category == LeagueCategory.ANALYSIS_ONLY:
                for _tip in ("Gol Olacak (S)", "Ev Gol Atacak (S)", "Dep Gol Atacak (S)"):
                    _cs_mesaj, _cs_sonuc = canli_sinyal_siniflandir(
                        tip        = _tip,
                        dakika     = dk,
                        ev_gol     = ev_gol,
                        dep_gol    = dep_gol,
                        ev_corner  = _cs_ev_c,
                        dep_corner = _cs_dep_c,
                        ah         = _cs_ah,
                        ev_adi     = ev_adi,
                        dep_adi    = dep_adi,
                        skor       = skor,
                        league     = league_name,
                    )
                    
                    if ENABLE_ANALYSIS_ONLY_LOGGING:
                        analysis_only_signals.kaydet(
                            event_id        = event_id,
                            league_name     = league_name,
                            category        = lig_category.value,
                            home_team       = ev_adi,
                            away_team       = dep_adi,
                            dakika          = int(dk),
                            skor            = skor,
                            home_goals      = ev_gol,
                            away_goals      = dep_gol,
                            home_corners    = _cs_ev_c,
                            away_corners    = _cs_dep_c,
                            ah              = _cs_ah,
                            tahmin_tipi     = _tip,
                            sinyal_dict     = _cs_sonuc
                        )
                
                logger.info(f"[ANALYSIS_ONLY] {league_name}/{ev_adi} vs {dep_adi} "
                           f"dk={dk:.0f}: sinyal kaydedildi, Telegram'a gönderilmedi")
                return None

            # [V57-FIX-6] NEUTRAL kategorisinde: sadece A+ ve (flag false ise) A+
            # WHITELIST kategorisinde: A+/A gönder
            sendable_sinyaller = None
            
            if lig_category == LeagueCategory.WHITELIST:
                sendable_sinyaller = ("A+", "A")
            elif lig_category == LeagueCategory.NEUTRAL:
                if SEND_NEUTRAL_LEAGUE_A_ONLY:
                    sendable_sinyaller = ("A+",)
                else:
                    sendable_sinyaller = ("A+", "A")
            
            for _tip in ("Gol Olacak (S)", "Ev Gol Atacak (S)", "Dep Gol Atacak (S)"):
                _cs_mesaj, _cs_sonuc = canli_sinyal_siniflandir(
                    tip        = _tip,
                    dakika     = dk,
                    ev_gol     = ev_gol,
                    dep_gol    = dep_gol,
                    ev_corner  = _cs_ev_c,
                    dep_corner = _cs_dep_c,
                    ah         = _cs_ah,
                    ev_adi     = ev_adi,
                    dep_adi    = dep_adi,
                    skor       = skor,
                    league     = league_name,
                )
                logger.debug(
                    f"[V57] {_tip} → {_cs_sonuc['sinyal']} "
                    f"(puan={_cs_sonuc['puan']})")

                # NEUTRAL liglerde filtre uygula
                if lig_category == LeagueCategory.NEUTRAL and sendable_sinyaller:
                    if _cs_sonuc["sinyal"] not in sendable_sinyaller:
                        logger.info(f"[NEUTRAL] {_cs_sonuc['sinyal']} sinyal "
                                   f"Telegram'a gönderilmedi (sadece A+ kabul)")
                        continue

                    # Neutral liglerde takım golü + hedef takım zaten önde = Telegram kapalı
                    if _tip in ("Ev Gol Atacak (S)", "Dep Gol Atacak (S)") and _target_team_is_leading(_tip, ev_gol, dep_gol):
                        logger.info(f"[NEUTRAL] {_tip} hedef takım önde, Telegram'a gönderilmedi")
                        continue

                # Sadece bu lig kategorisi için gönderilebilir sınıflar aday olabilir.
                if _cs_sonuc["sinyal"] in (sendable_sinyaller or ("A+", "A")):
                    if _cs_mesaj:
                        _adaylar.append(
                            (_cs_sonuc["puan"], _cs_sonuc["sinyal"], _cs_mesaj, _tip))
                elif _cs_sonuc["sinyal"] == "B":
                    logger.info(
                        f"[V57] B sinyal (logda): {_tip} dk={dk:.0f} "
                        f"puan={_cs_sonuc['puan']} AH={_cs_ah:+.2f}")

            if _adaylar:
                # En yüksek puanlı tek adayı seç
                _adaylar.sort(reverse=True, key=lambda x: x[0])
                _en_iyi_puan, _en_iyi_sinyal, _en_iyi_mesaj, _en_iyi_tip = _adaylar[0]

                _dup_key = f"V57_{_en_iyi_tip}_{ev_gol}-{dep_gol}"
                if not sinyal_gecmisi.zaten_gonderildi_mi(
                        event_id, int(dk), _dup_key):
                    sinyal_gecmisi.kaydet(event_id, int(dk), _dup_key)
                    logger.info(
                        f"[V57] ✅ EN İYİ: {_en_iyi_tip} → {_en_iyi_sinyal} | "
                        f"puan={_en_iyi_puan} dk={dk:.0f} skor={skor} AH={_cs_ah:+.2f}")
                    return _en_iyi_mesaj, None, None

        except Exception as _cs_err:
            logger.debug(f"[V57] Sınıflandırıcı hata: {_cs_err}")
            import traceback; logger.debug(traceback.format_exc())
        
        # ── V57 bitti ───────────────────────────────────────────────────────
        # ENABLE_LEGACY_MODULES = False → eski "💎 SİNYAL" yolu tamamen kapalı.
        # Buraya düşen her çağrı sessizce None döner.
        return None

    except Exception as e:
        logger.error(f"mac_analiz_et:{e}")
        import traceback; logger.error(traceback.format_exc())
        return None


# ============================================================================
# MAÇ İŞLEME
# ============================================================================

async def mac_isle(bot, mac_data: dict,
                   session) -> Optional[str]:
    try:
        mac_id  = str(mac_data.get('id', ''))
        home_d  = mac_data.get('home', {})
        away_d  = mac_data.get('away', {})
        ev_adi  = home_d.get('name','') if isinstance(home_d,dict) else ''
        dep_adi = away_d.get('name','') if isinstance(away_d,dict) else ''
        if not ev_adi or not dep_adi: return None

        lig_d       = mac_data.get('league', {})
        league_name = lig_d.get('name','Unknown') if isinstance(lig_d,dict) else 'Unknown'

        # ── HARD_REJECT ERKEN ÇIKIŞ ───────────────────────────────
        # Sadece e-spor / fake / virtual maçlar burada erken atlanır.
        # Kadın/genç/rezerv ANALYSIS_ONLY maçlar erken atlanmaz; analiz için kaydedilir.
        _allowed0, _cat0, _reason0 = LeagueFilter.check_league(league_name, ev_adi, dep_adi)
        if _cat0 == LeagueCategory.HARD_REJECT:
            logger.debug(f"[LEAGUE] Hard reject erken çıkış: {league_name} [{_reason0}]")
            return None
        # ────────────────────────────────────────────────────────────
        timer       = mac_data.get('timer', {})
        dk          = guvenli_int(timer.get('tm',0)) if isinstance(timer,dict) else 0
        skor        = mac_data.get('ss','0-0') or '0-0'

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
                            if s and isinstance(s, dict): stats_data = s
        except Exception: pass

        if not stats_data:
            stats_data = mac_data.get('stats', {})
        if not stats_data or not isinstance(stats_data, dict): return None

        ev_v = dep_v = None
        if 'corners' in stats_data and isinstance(stats_data.get('corners'), list):
            r = VeriKorumaKatmani().yeni_format_parse(stats_data)
            if r: ev_v, dep_v = r

        if not ev_v or not dep_v:
            ev_v  = stats_data.get('1', {})
            dep_v = stats_data.get('2', {})
        if not ev_v or not dep_v: return None

        if (sum(1 for k in ev_v  if k.startswith('S')) == 0 or
                sum(1 for k in dep_v if k.startswith('S')) == 0): return None

        sonuc = await mac_analiz_et(
            ev_v, dep_v, ev_adi, dep_adi, skor, dk,
            bot, session, event_id=mac_id, league_name=league_name)
        # mac_analiz_et (mesaj, gemini_mesaj) tuple veya None döndürür
        if sonuc is None:
            return None
        if isinstance(sonuc, tuple):
            return sonuc   # (ana_mesaj, gemini_mesaj)
        return (sonuc, None)

    except Exception as e:
        logger.error(f"mac_isle:{e}"); return None


# ============================================================================
# TELEGRAM QUEUE
# ============================================================================

telegram_queue: asyncio.Queue = None


async def telegram_gondericisi(bot):
    """Telegram sıra işleyici — Legacy mesaj filtreleri uygulanır"""
    legacy_patterns = [
        "💎 SİNYAL",
        "🎯 EV GOL",
        "🎯 DEP GOL",
        "🎯 İY2 GOL",
        "🎯 İY GOL",
        "TVPS:",
        "Proxy xT",
        "Fpressure",
    ]
    
    while True:
        try:
            chat_id, mesaj = await telegram_queue.get()
            
            # [V57-FIX-11] Legacy mesaj engelleme
            if BLOCK_LEGACY_DIAMOND_MESSAGES and isinstance(mesaj, str):
                if any(p in mesaj for p in legacy_patterns):
                    logger.warning(f"[V57] Legacy format mesaj engellendi: {mesaj[:50]}...")
                    telegram_queue.task_done()
                    continue
            
            try:
                await bot.send_message(
                    chat_id=chat_id, text=mesaj, parse_mode="Markdown")
            except Exception as e:
                # Markdown hatası → parse_mode kaldır ve tekrar dene
                try:
                    clean = mesaj.replace("*","").replace("_","").replace("`","")
                    await bot.send_message(chat_id=chat_id, text=clean, parse_mode=None)
                except Exception as e2:
                    logger.error(f"TG fallback hatasi: {e2}")
            finally:
                telegram_queue.task_done()
            await asyncio.sleep(1.5)
        except Exception as e:
            logger.error(f"TG queue:{e}")
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
                "🚀 BOT V57 — CANLI SİNYAL SINIFLANDIRICI\n\n"
                "Motor: classify_live_signal()\n\n"
                "SİNYAL TİPLERİ:\n"
                "  🔥🔥 A+  — En güçlü (%95+)\n"
                "  🔥  A   — Güçlü (%90+)\n"
                "  ✅  B   — Orta (%75-90)\n"
                "  ⚠️  LOW_VALUE — Düşük güven\n"
                "  ⛔  PASS — Oynama / riskli\n"
                "  🔕  IGNORE — Erken / değersiz\n\n"
                "KURALLAR:\n"
                "• Gol Olacak(S): dk<50 kapalı; dk≥60+gol=1+korner≤3+AH≥1.25 → A+\n"
                "• Ev Gol Atacak(S): AH yönü EV değilse A üretilmez\n"
                "• Dep Gol Atacak(S): dk≤15 veya 0-0 → B\n"
                "• 0-59dk+gol=1+Gol Olacak → IGNORE\n"
                "• 46-60dk+AH±0.5+Ev/Dep → PASS\n"
                "• 46-60dk+3-0+Ev Gol → HARD_PASS\n• Team-goal AH yönü zorunlu: ters AH cezalı\n• Neutral ligler: sadece A+; hedef takım öndeyse takım golü kapalı\n• Kadın/genç/rezerv: ANALYSIS_ONLY, Telegram kapalı\n\n"
                "Eski Gemini/Claude/Excel filtreleri KALDIRILDI.\n"
                "Sinyaller bekleniyor..."
            ),
            parse_mode=None
        )
        
        # [V57] Self-test fonksiyonlarını çalıştır
        logger.info("[V57] Self-test başlatılıyor...")
        test1_ok = self_test_classify_live_signal()
        test2_ok = self_test_league_categories()
        
        if test1_ok and test2_ok:
            logger.info("[V57] ✅ Tüm self-test'ler başarılı")
        else:
            logger.warning("[V57] ⚠️ Bazı self-test'ler başarısız — ayarları kontrol et")
    
    except Exception as e:
        logger.error(f"Bot başlatma:{e}"); return

    asyncio.create_task(telegram_gondericisi(bot))

    async with aiohttp.ClientSession() as session:
        dongu        = 0
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
                            await asyncio.sleep(60); continue
                        data = await resp.json()

                aktif_maclar = esnek_liste_duzelt(data.get('results', []))
                logger.info(f"#{dongu} | {len(aktif_maclar)} maç")

                async def isle(mac_data):
                    try:
                        sonuc = await mac_isle(bot, mac_data, session)
                        if sonuc is None:
                            return
                        # Tek mesaj veya tuple (ana, gemini)
                        # 2 veya 3 elemanlı tuple
                        if isinstance(sonuc, tuple):
                            if len(sonuc) == 3:
                                ana_mesaj, gemini_mesaj, extra_mesaj = sonuc
                            else:
                                ana_mesaj, gemini_mesaj = sonuc
                                extra_mesaj = None
                        else:
                            ana_mesaj, gemini_mesaj, extra_mesaj = sonuc, None, None
                        # 1. Ana sinyal
                        if ana_mesaj:
                            await telegram_queue.put((CHAT_ID, ana_mesaj))
                        # 2. Gemini ek bildirimi
                        if gemini_mesaj:
                            await asyncio.sleep(0.5)
                            await telegram_queue.put((CHAT_ID, gemini_mesaj))
                        # 3. Excel Top 10 / Claude AH / Excel Oran bildirimi
                        if extra_mesaj:
                            await asyncio.sleep(0.5)
                            await telegram_queue.put((CHAT_ID, extra_mesaj))
                    except Exception as e:
                        logger.error(f"isle:{e}")

                await asyncio.gather(
                    *[isle(m) for m in aktif_maclar],
                    return_exceptions=True)

                if dongu % 10 == 0:
                    s = api_rate_limiter.stats()
                    logger.info(f"Rate:{s['total']} req, {s['throttled']} throttled")
                    veri_koruma.istatistik()

            except Exception as e:
                logger.error(f"Ana:{e}")
                import traceback; logger.error(traceback.format_exc())

            await asyncio.sleep(dongu_suresi_hesapla(aktif_maclar))


# ============================================================================
# GİRİŞ
# ============================================================================


# ============================================================================
# V57 SELF TEST
# ============================================================================

def self_test_v57():
    """V57 çekirdek sınıflandırıcı hızlı testleri."""
    tests = [
        ("29dk 1-0 Gol Olacak ignore", ("Gol Olacak (S)", 29, 1, 0, 1, 2, -0.75), "IGNORE"),
        ("59dk 1-0 Gol Olacak ignore", ("Gol Olacak (S)", 59, 1, 0, 1, 2, -1.25), "IGNORE"),
        ("60dk 1-0 Gol Olacak A/A+", ("Gol Olacak (S)", 60, 1, 0, 1, 2, -1.25), ("A+", "A")),
        ("50dk Ev Gol AH0.5 PASS", ("Ev Gol Atacak (S)", 50, 1, 0, 1, 4, -0.50), "PASS"),
        ("Pisa ev gol ters AH Telegram dışı", ("Ev Gol Atacak (S)", 21, 0, 1, 1, 2, +1.00), ("B", "LOW_VALUE", "PASS")),
        ("Napoli dep gol yön doğru ama hedef önde → Telegram dışı", ("Dep Gol Atacak (S)", 21, 0, 1, 1, 2, +1.00), ("LOW_VALUE", "B")),
        ("Lipno 0-2 erken Gol Olacak IGNORE", ("Gol Olacak (S)", 12, 0, 2, 0, 0, +1.00), "IGNORE"),
    ]

    ok_all = True
    for name, args, expected in tests:
        sonuc = classify_live_signal(*args)
        got = sonuc["sinyal"]
        if isinstance(expected, tuple):
            ok = got in expected
        else:
            ok = got == expected
        ok_all = ok_all and ok
        logger.info(f"[SELFTEST] {name}: got={got}, expected={expected}, ok={ok}")
    return ok_all


def self_test_league_categories():
    """LeagueCategory hızlı testleri."""
    tests = [
        ("England Women Super League", "Chelsea Women", "Arsenal Women", LeagueCategory.ANALYSIS_ONLY),
        ("Germany Bundesliga", "Bayern", "Dortmund", LeagueCategory.WHITELIST),
        ("Taiwan Premier League", "A", "B", LeagueCategory.NEUTRAL),
        ("Esoccer Battle", "A", "B", LeagueCategory.HARD_REJECT),
        ("Poland III Liga", "Lech Poznan II", "Zawisza Bydgoszcz", LeagueCategory.ANALYSIS_ONLY),
    ]
    ok_all = True
    for league, home, away, expected in tests:
        allowed, category, reason = LeagueFilter.check_league(league, home, away)
        ok = category == expected
        ok_all = ok_all and ok
        logger.info(f"[LEAGUE TEST] {league} / {home}-{away}: got={category.value}, expected={expected.value}, ok={ok}, reason={reason}")
    return ok_all


if __name__ == "__main__":
    logger.info("🚀 Bot V57 Başlatılıyor...")  # [V57-FIX-6]
    logger.info(
        f"TG:{'✅' if TELEGRAM_TOKEN else '❌'} | "
        f"Chat:{'✅' if CHAT_ID else '❌'} | "
        f"API:{'✅' if BETSAPI_TOKEN else '❌'}"
    )
    try:
        asyncio.run(ana_dongu())
    except KeyboardInterrupt:
        logger.info("Bot durduruldu")
    except Exception as e:
        logger.error(f"Kritik:{e}")
        import traceback; logger.error(traceback.format_exc())
