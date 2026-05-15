"""
Excel Strategy Bot - Clean V1
--------------------------------
Sıfırdan, sadece Excel analizinden çıkan stratejiler üzerine kurulmuş canlı futbol botu.

Gereken env değişkenleri:
    TELEGRAM_TOKEN=...
    CHAT_ID=...
    BETSAPI_TOKEN=...

Kurulum:
    pip install aiohttp python-telegram-bot

Çalıştırma:
    python excel_strategy_bot_clean.py

Notlar:
- Gerçek ROI yazmaz; gerçek entry/closing odds yoksa break-even oran yazar.
- AH negatifse ev sahibi favori varsayılır. AH pozitifse ev sahibi underdog/deplasman tarafı favori/üstün varsayımı yapılmaz; strateji kuralları Excel'deki işarete göre çalışır.
- S1 ve S10 aynı koşulu paylaşır: S1 güvenli X2, S10 agresif MS2 alternatifidir.
"""

from __future__ import annotations

import asyncio
import aiohttp
import logging
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Iterable

from telegram import Bot

# =============================================================================
# CONFIG
# =============================================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "45"))
MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", "8"))
API_TIMEOUT_SECONDS = int(os.getenv("API_TIMEOUT_SECONDS", "15"))
SEND_BLOCK_MESSAGES = os.getenv("SEND_BLOCK_MESSAGES", "0") == "1"

# 1 ise her maç için neden strateji düşmediğini loglar; canlıda fazla log basabilir.
DEBUG_NO_SIGNAL = os.getenv("DEBUG_NO_SIGNAL", "0") == "1"

# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("excel_strategy_bot")

# =============================================================================
# UTILS
# =============================================================================


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if isinstance(value, str):
            value = value.replace(",", ".").strip()
            value = re.sub(r"[^0-9.\-+]", "", value)
            if value in {"", "+", "-", "."}:
                return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", ".").strip()))
    except Exception:
        return default


def parse_score(score: str) -> tuple[int, int]:
    try:
        left, right = str(score or "0-0").split("-")[:2]
        return safe_int(left), safe_int(right)
    except Exception:
        return 0, 0


def wilson_low(p: float, n: int, z: float = 1.96) -> float:
    """p 0-1 aralığında; çıktı 0-1."""
    if n <= 0:
        return 0.0
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) / n) + (z * z / (4 * n * n)))
    return max(0.0, (centre - margin) / denom)


def pct(x: float) -> str:
    return f"%{x * 100:.1f}"


def md_escape_light(text: str) -> str:
    """Markdown fallback için basit temizlik. Telegram Markdown çok hassas olduğu için minimal kullanıyoruz."""
    return str(text).replace("*", "").replace("_", "")

# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class MatchSnapshot:
    event_id: str
    league: str
    home_name: str
    away_name: str
    minute: int
    home_goals: int
    away_goals: int
    home_corners: int = 0
    away_corners: int = 0
    ah: Optional[float] = None
    ah_home_odds: Optional[float] = None
    ah_away_odds: Optional[float] = None
    ht_total_goals: Optional[int] = None

    @property
    def score(self) -> str:
        return f"{self.home_goals}-{self.away_goals}"

    @property
    def score_diff(self) -> int:
        return self.home_goals - self.away_goals

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals

    @property
    def corner_diff_home(self) -> int:
        return self.home_corners - self.away_corners

    @property
    def corner_diff_away(self) -> int:
        return self.away_corners - self.home_corners

    @property
    def cpd(self) -> float:
        return (self.home_corners + self.away_corners) / max(self.minute, 1)


@dataclass
class StrategyResult:
    strategy_id: str
    name: str
    category: str  # PRIMARY, AGGRESSIVE, CONFIRMATION, BLOCK
    market: str
    win_rate: float
    n: int
    ci_low: float
    baseline: float
    min_odds: float
    min_odds_wilson: float
    risk: str
    priority: int
    reason: str
    blocked: bool = False
    confirmations: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)

# =============================================================================
# STRATEGY ENGINE - SADECE EXCEL'DEN ÇIKAN TEMİZ STRATEJİLER
# =============================================================================

class ExcelStrategyEngine:
    """
    Mevcut Gemini/Claude kodundan bağımsız, sadece Excel analizinden çıkan stratejiler.
    """

    MIN_SAMPLE_PRIMARY = 150

    @staticmethod
    def _need_ah(s: MatchSnapshot) -> bool:
        return s.ah is not None

    @staticmethod
    def check_block(s: MatchSnapshot) -> Optional[StrategyResult]:
        """
        B1 - Ağır favori 45+ trap.
        Gol Olacak/Sıradaki Gol marketi için baseline altı. Düşük oranlarda blok.
        """
        if s.ah is None:
            return None
        if s.ah <= -0.5 and s.minute >= 45:
            return StrategyResult(
                strategy_id="B1_HEAVY_FAV_45_PLUS_TRAP",
                name="Ağır Favori 45+ Trap Kontrol",
                category="BLOCK",
                market="Gol Olacak / Sıradaki Gol",
                win_rate=0.7739,
                n=3198,
                ci_low=0.7591,
                baseline=0.8426,
                min_odds=1.292,
                min_odds_wilson=1.317,
                risk="BLOCK",
                priority=1000,
                reason=(
                    f"AH {s.ah:+.2f}, dakika {s.minute}. Bu bölge Gol Olacak marketinde "
                    "genel baseline'ın altında; düşük oranlarda value yok."
                ),
                blocked=True,
            )
        return None

    @staticmethod
    def check_primary(s: MatchSnapshot) -> list[StrategyResult]:
        if s.ah is None:
            return []

        ah = s.ah
        minute = s.minute
        diff = s.score_diff
        total = s.total_goals
        out: list[StrategyResult] = []

        def add(**kw: Any) -> None:
            out.append(StrategyResult(**kw))

        # S1
        if ah >= 1.0 and minute <= 30 and diff < 0:
            add(
                strategy_id="S1_UNDERDOG_EARLY_AWAY_LEAD_X2",
                name="Underdog Erken Deplasman Önde",
                category="PRIMARY",
                market="Çifte Şans X2",
                win_rate=0.9760,
                n=459,
                ci_low=0.9576,
                baseline=0.5032,
                min_odds=1.0246,
                min_odds_wilson=1.0443,
                risk="LOW",
                priority=100,
                reason=f"AH {ah:+.2f}, {minute}. dk, deplasman önde ({s.score}). X2 tarafı çok güçlü.",
            )
            # S10 aynı koşulun agresif market alternatifi
            add(
                strategy_id="S10_UNDERDOG_EARLY_AWAY_LEAD_MS2",
                name="Underdog Erken Deplasman Önde — MS2 Agresif",
                category="AGGRESSIVE",
                market="MS 2",
                win_rate=0.9216,
                n=459,
                ci_low=0.8933,
                baseline=0.3371,
                min_odds=1.0851,
                min_odds_wilson=1.1194,
                risk="MEDIUM",
                priority=91,
                reason=f"Aynı koşulda MS2 agresif alternatif. Oran yeterliyse değerlendirilebilir.",
            )

        # S2
        if ah <= -2.0 and minute <= 30:
            add(
                strategy_id="S2_HEAVY_FAV_EARLY_1X",
                name="Ezici Favori 1X Banko",
                category="PRIMARY",
                market="Çifte Şans 1X",
                win_rate=0.9714,
                n=699,
                ci_low=0.9562,
                baseline=0.6629,
                min_odds=1.0295,
                min_odds_wilson=1.0458,
                risk="LOW",
                priority=99,
                reason=f"AH {ah:+.2f}, ilk 30 dakika. Ezici favori erken bölümde 1X tarafını güçlendiriyor.",
            )

        # S3
        if ah <= -2.0 and minute <= 30 and diff == 0:
            add(
                strategy_id="S3_HEAVY_FAV_EARLY_DRAW_NEXT_GOAL",
                name="Ezici Favori Erken Eşit",
                category="PRIMARY",
                market="Gol Olacak / Sıradaki Gol",
                win_rate=0.9795,
                n=292,
                ci_low=0.9559,
                baseline=0.8426,
                min_odds=1.0210,
                min_odds_wilson=1.0461,
                risk="LOW_MEDIUM",
                priority=98,
                reason=f"Ezici favori var ama skor eşit ({s.score}); gol baskısı yüksek.",
            )

        # S4
        if ah <= -1.25 and minute <= 30 and total == 0:
            add(
                strategy_id="S4_STRONG_FAV_EARLY_0_GOAL",
                name="Çok Güçlü Favori Erken 0 Gol",
                category="PRIMARY",
                market="Gol Olacak / Sıradaki Gol",
                win_rate=0.9742,
                n=466,
                ci_low=0.9555,
                baseline=0.8426,
                min_odds=1.0264,
                min_odds_wilson=1.0465,
                risk="LOW_MEDIUM",
                priority=97,
                reason=f"AH {ah:+.2f}, {minute}. dk ve maç 0-0. Güçlü favori gol baskısı yaratıyor.",
            )

        # S5
        if ah <= -0.75 and minute <= 30 and total == 0:
            add(
                strategy_id="S5_FAV_EARLY_0_GOAL",
                name="Ağır Favori Erken 0 Gol",
                category="PRIMARY",
                market="Gol Olacak / Sıradaki Gol",
                win_rate=0.9650,
                n=657,
                ci_low=0.9480,
                baseline=0.8426,
                min_odds=1.0363,
                min_odds_wilson=1.0548,
                risk="LOW",
                priority=96,
                reason=f"Favori AH {ah:+.2f}, ilk 30 dakika ve gol yok; sonraki gol ihtimali yüksek.",
            )

        # S6
        if ah <= -1.0 and minute <= 30 and diff == 0:
            add(
                strategy_id="S6_FAV_EARLY_DRAW_NEXT_GOAL",
                name="Ağır Favori Erken Eşit",
                category="PRIMARY",
                market="Gol Olacak / Sıradaki Gol",
                win_rate=0.9634,
                n=711,
                ci_low=0.9470,
                baseline=0.8426,
                min_odds=1.0380,
                min_odds_wilson=1.0560,
                risk="LOW",
                priority=95,
                reason=f"AH {ah:+.2f}, ilk 30 dakika ve skor eşit ({s.score}). Favori gol baskısı.",
            )

        # S7
        if ah <= -2.5:
            add(
                strategy_id="S7_EXTREME_FAV_HOME_GOAL",
                name="Aşırı Favori Ev Gol",
                category="PRIMARY",
                market="Ev Gol Atacak",
                win_rate=0.9512,
                n=512,
                ci_low=0.9289,
                baseline=0.6256,
                min_odds=1.0513,
                min_odds_wilson=1.0765,
                risk="LOW_MEDIUM",
                priority=94,
                reason=f"AH {ah:+.2f}. Aşırı favori ev gol marketinde güçlü edge veriyor.",
            )

        # S8
        if ah <= -1.5 and minute <= 30 and diff == 0:
            add(
                strategy_id="S8_FAV_EARLY_DRAW_HOME_GOAL",
                name="Favori Erken Eşit → Ev Gol",
                category="PRIMARY",
                market="Ev Gol Atacak",
                win_rate=0.9521,
                n=438,
                ci_low=0.9278,
                baseline=0.6256,
                min_odds=1.0504,
                min_odds_wilson=1.0778,
                risk="MEDIUM",
                priority=93,
                reason=f"AH {ah:+.2f}, ilk 30 dakika ve skor eşit. Ev/favori gol yönü güçlü.",
            )

        # S9
        if -1.0 < ah <= -0.5 and minute <= 30 and diff < 0:
            add(
                strategy_id="S9_LIGHT_FAV_EARLY_BEHIND",
                name="Hafif Favori Erken Geride",
                category="PRIMARY",
                market="Gol Olacak / Sıradaki Gol",
                win_rate=0.9468,
                n=620,
                ci_low=0.9262,
                baseline=0.8426,
                min_odds=1.0562,
                min_odds_wilson=1.0797,
                risk="LOW_MEDIUM",
                priority=92,
                reason=f"Hafif favori AH {ah:+.2f}, ilk 30 dakika geride ({s.score}); reaksiyon golü beklenir.",
            )

        # S11 - E20'nin düzeltilmiş hali: KG Var değil, MS 2.5 Üst
        if abs(ah) <= 0.25 and minute <= 40 and diff == 0:
            add(
                strategy_id="S11_BALANCED_EARLY_DRAW_OVER25",
                name="Dengeli Maç Erken Eşit → MS 2.5 Üst",
                category="PRIMARY_MEDIUM",
                market="MS 2.5 Üst",
                win_rate=0.8519,
                n=540,
                ci_low=0.8194,
                baseline=0.7744,
                min_odds=1.1739,
                min_odds_wilson=1.2204,
                risk="MEDIUM",
                priority=80,
                reason=f"AH dengeli ({ah:+.2f}), {minute}. dk ve skor eşit. Over 2.5 baseline üstü.",
            )

        # S12 - şartlı, düşük öncelik
        if ah <= -1.0 and 46 <= minute <= 65 and (s.ht_total_goals is not None and s.ht_total_goals >= 1):
            add(
                strategy_id="S12_SECOND_HALF_FAV_CONTINUE",
                name="2. Yarı Favori Devam",
                category="CONDITIONAL",
                market="2Y 0.5 Üst",
                win_rate=0.8548,
                n=248,
                ci_low=0.8056,
                baseline=0.8146,
                min_odds=1.1698,
                min_odds_wilson=1.2413,
                risk="MEDIUM_HIGH",
                priority=70,
                reason=f"Favori AH {ah:+.2f}; ilk yarıda gol var, 2. yarı devam ihtimali.",
            )

        return out

    @staticmethod
    def check_confirmations(s: MatchSnapshot) -> list[str]:
        out: list[str] = []
        if 20 <= s.minute <= 50 and s.corner_diff_home >= 2:
            out.append(
                f"Ev korner baskısı: korner farkı +{s.corner_diff_home} → Ev Gol Atacak için yardımcı onay"
            )
        if 20 <= s.minute <= 50 and s.corner_diff_away >= 2:
            out.append(
                f"Deplasman korner baskısı: korner farkı +{s.corner_diff_away} → Dep Gol Atacak için yardımcı onay"
            )
        if s.ah is not None and s.ah <= -1.0 and s.minute <= 30 and s.cpd > 0.30:
            out.append(
                f"Yüksek korner temposu: CPD={s.cpd:.2f} → favori/gol marketine yardımcı onay"
            )
        return out

    @classmethod
    def evaluate(cls, s: MatchSnapshot) -> Optional[StrategyResult]:
        block = cls.check_block(s)
        if block:
            return block

        candidates = cls.check_primary(s)
        if not candidates:
            if DEBUG_NO_SIGNAL:
                logger.info("No strategy: %s %s %s AH=%s dk=%s", s.home_name, s.score, s.away_name, s.ah, s.minute)
            return None

        candidates.sort(key=lambda r: r.priority, reverse=True)
        main = candidates[0]

        # Aynı koşulda daha düşük öncelikli adaylar alternatif/onay olarak görünür.
        for other in candidates[1:]:
            if other.category == "AGGRESSIVE":
                main.alternatives.append(
                    f"{other.name}: {other.market} | Başarı {pct(other.win_rate)} | Wilson {pct(other.ci_low)} | Min oran {other.min_odds_wilson:.2f}"
                )
            else:
                main.confirmations.append(
                    f"{other.name}: {other.market} | {pct(other.win_rate)}"
                )

        main.confirmations.extend(cls.check_confirmations(s))
        return main

# =============================================================================
# DUPLICATE STORAGE
# =============================================================================

class SignalHistory:
    def __init__(self, db_path: str = "excel_strategy_signals.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    event_id TEXT NOT NULL,
                    minute_group INTEGER NOT NULL,
                    strategy_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(event_id, minute_group, strategy_id)
                )
                """
            )
            conn.execute("DELETE FROM signals WHERE created_at < ?", (time.time() - 86400,))
            conn.commit()

    @staticmethod
    def minute_group(minute: int) -> int:
        return (int(minute) // 5) * 5

    def seen(self, event_id: str, minute: int, strategy_id: str) -> bool:
        if not event_id:
            return False
        mg = self.minute_group(minute)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM signals WHERE event_id=? AND minute_group=? AND strategy_id=?",
                (event_id, mg, strategy_id),
            ).fetchone()
            return row is not None

    def save(self, event_id: str, minute: int, strategy_id: str) -> None:
        if not event_id:
            return
        mg = self.minute_group(minute)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO signals VALUES (?, ?, ?, ?)",
                    (event_id, mg, strategy_id, time.time()),
                )
                conn.commit()
        except sqlite3.IntegrityError:
            pass

signal_history = SignalHistory()

# =============================================================================
# TELEGRAM FORMAT
# =============================================================================

class MessageBuilder:
    @staticmethod
    def build(snapshot: MatchSnapshot, result: StrategyResult) -> str:
        if result.blocked:
            return (
                f"════════════════════════════════\n"
                f"⛔ *[BLOK] STRATEJİ UYARISI*\n"
                f"⚽ {snapshot.home_name} {snapshot.score} {snapshot.away_name}\n"
                f"🏆 {snapshot.league}\n"
                f"⏱ Dakika: {snapshot.minute}\n"
                f"──────────────────────────────\n"
                f"📌 Filtre: {result.name}\n"
                f"🎯 Etkilenen Market: {result.market}\n"
                f"📊 Bölge Başarısı: {pct(result.win_rate)} | Baseline: {pct(result.baseline)}\n"
                f"📦 Örneklem: n={result.n}\n"
                f"🧪 Wilson CI Alt: {pct(result.ci_low)}\n"
                f"💱 Gerekli Min Oran: {result.min_odds_wilson:.2f}+\n"
                f"──────────────────────────────\n"
                f"📝 {result.reason}"
            )

        conf = ""
        if result.confirmations:
            conf = "\n✅ *Ek Onaylar:*\n" + "\n".join(f"- {c}" for c in result.confirmations)

        alt = ""
        if result.alternatives:
            alt = "\n🎲 *Alternatif / Agresif Market:*\n" + "\n".join(f"- {a}" for a in result.alternatives)

        edge = result.win_rate - result.baseline
        return (
            f"════════════════════════════════\n"
            f"📊 *[EXCEL] STRATEJİ SİNYALİ — {result.risk}*\n"
            f"⚽ {snapshot.home_name} {snapshot.score} {snapshot.away_name}\n"
            f"🏆 {snapshot.league}\n"
            f"⏱ Dakika: {snapshot.minute}\n"
            f"──────────────────────────────\n"
            f"📌 Filtre: {result.name}\n"
            f"🎯 Market: {result.market}\n"
            f"──────────────────────────────\n"
            f"📊 Başarı: {pct(result.win_rate)}\n"
            f"📦 Örneklem: n={result.n}\n"
            f"🧪 Wilson CI Alt: {pct(result.ci_low)}\n"
            f"📈 Baseline: {pct(result.baseline)} | Lift: {edge * 100:+.1f} puan\n"
            f"💱 Break-Even Min Oran: {result.min_odds:.2f}\n"
            f"🛡 Wilson Min Oran: {result.min_odds_wilson:.2f}\n"
            f"⚠️ Risk: {result.risk}\n"
            f"──────────────────────────────\n"
            f"📝 {result.reason}"
            f"{alt}"
            f"{conf}"
        )

# =============================================================================
# BETSAPI CLIENT
# =============================================================================

class BetsApiClient:
    BASE = "https://api.betsapi.com/v1"

    def __init__(self, token: str, session: aiohttp.ClientSession):
        self.token = token
        self.session = session
        self.sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def _get(self, path: str, params: dict[str, Any]) -> Optional[dict[str, Any]]:
        params = dict(params)
        params["token"] = self.token
        url = f"{self.BASE}{path}"
        async with self.sem:
            try:
                async with self.session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("BetsAPI HTTP %s for %s", resp.status, path)
                        return None
                    return await resp.json()
            except asyncio.TimeoutError:
                logger.warning("BetsAPI timeout: %s", path)
                return None
            except Exception as exc:
                logger.warning("BetsAPI error %s: %s", path, exc)
                return None

    async def inplay_events(self) -> list[dict[str, Any]]:
        data = await self._get("/events/inplay", {"sport_id": 1})
        if not data or data.get("success") != 1:
            return []
        results = data.get("results", [])
        return results if isinstance(results, list) else []

    async def event_view(self, event_id: str) -> Optional[dict[str, Any]]:
        data = await self._get("/event/view", {"event_id": event_id})
        if not data or data.get("success") != 1:
            return None
        results = data.get("results", [])
        if isinstance(results, list) and results:
            return results[0]
        if isinstance(results, dict):
            return results
        return None

    async def asian_handicap(self, event_id: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Dönüş: (home_handicap, home_odds, away_odds)
        AH negatif = ev sahibi favori.
        """
        data = await self._get("/event/odds", {"event_id": event_id})
        if not data or data.get("success") != 1:
            return None, None, None
        results = data.get("results", {})
        if not isinstance(results, dict):
            return None, None, None

        candidates: list[Any] = []
        for key in ("asian_handicap", "ah", "handicap", "1_2"):
            if key in results:
                candidates.append(results[key])
        for key, value in results.items():
            if any(x in str(key).lower() for x in ("asian", "handicap", "ah")):
                candidates.append(value)

        for item in candidates:
            ah, home_od, away_od = self._parse_ah_market(item)
            if ah is not None:
                return ah, home_od, away_od
        return None, None, None

    @staticmethod
    def _parse_ah_market(item: Any) -> tuple[Optional[float], Optional[float], Optional[float]]:
        # BetsAPI bazen liste, bazen dict döndürüyor.
        if isinstance(item, list) and item:
            # En güncel oran genelde listenin ilk elemanı oluyor; değilse yine de ilk okunabilir kaydı al.
            for row in item:
                if not isinstance(row, dict):
                    continue
                ah = safe_float(row.get("handicap", row.get("hdp", None)), None)  # type: ignore[arg-type]
                home_od = safe_float(row.get("home_od", row.get("home_odds", row.get("home", 0))))
                away_od = safe_float(row.get("away_od", row.get("away_odds", row.get("away", 0))))
                if ah is not None:
                    return ah, home_od or None, away_od or None

        if isinstance(item, dict):
            if "home" in item and "away" in item:
                h = item.get("home") or {}
                a = item.get("away") or {}
                if isinstance(h, dict) and isinstance(a, dict):
                    ah = safe_float(h.get("handicap", h.get("hdp", None)), None)  # type: ignore[arg-type]
                    home_od = safe_float(h.get("odds", h.get("od", 0)))
                    away_od = safe_float(a.get("odds", a.get("od", 0)))
                    if ah is not None:
                        return ah, home_od or None, away_od or None

            ah = safe_float(item.get("handicap", item.get("hdp", None)), None)  # type: ignore[arg-type]
            home_od = safe_float(item.get("home_od", item.get("home_odds", 0)))
            away_od = safe_float(item.get("away_od", item.get("away_odds", 0)))
            if ah is not None:
                return ah, home_od or None, away_od or None

        return None, None, None

# =============================================================================
# BETSAPI PARSING
# =============================================================================

class BetsApiParser:
    @staticmethod
    def extract_minute(event: dict[str, Any]) -> int:
        timer = event.get("timer", {})
        if isinstance(timer, dict):
            return safe_int(timer.get("tm", timer.get("minute", 0)))
        return 0

    @staticmethod
    def extract_teams(event: dict[str, Any]) -> tuple[str, str]:
        home = event.get("home", {})
        away = event.get("away", {})
        home_name = home.get("name", "") if isinstance(home, dict) else ""
        away_name = away.get("name", "") if isinstance(away, dict) else ""
        return home_name, away_name

    @staticmethod
    def extract_league(event: dict[str, Any]) -> str:
        league = event.get("league", {})
        if isinstance(league, dict):
            return str(league.get("name", "Unknown"))
        return "Unknown"

    @staticmethod
    def extract_corners(stats: Any) -> tuple[int, int]:
        """BetsAPI stats formatları değişken olduğu için esnek corner parse."""
        if not isinstance(stats, dict):
            return 0, 0

        # Format: {'corners': ['3','2']} veya [{'home':3,'away':2}]
        corners = stats.get("corners") or stats.get("Corners") or stats.get("corner")
        if isinstance(corners, list) and len(corners) >= 2:
            return safe_int(corners[0]), safe_int(corners[1])
        if isinstance(corners, dict):
            return safe_int(corners.get("home", corners.get("1", 0))), safe_int(corners.get("away", corners.get("2", 0)))

        # Format: stats['1'], stats['2'] dict
        h = stats.get("1") if isinstance(stats.get("1"), dict) else {}
        a = stats.get("2") if isinstance(stats.get("2"), dict) else {}
        corner_keys = ["corner", "corners", "Corner", "Corners", "Korner", "CK"]
        for key in corner_keys:
            hv = h.get(key) if isinstance(h, dict) else None
            av = a.get(key) if isinstance(a, dict) else None
            if hv is not None or av is not None:
                return safe_int(hv), safe_int(av)

        # Format: stat list items with name/value
        for key, value in stats.items():
            if "corner" in str(key).lower() or "korner" in str(key).lower():
                if isinstance(value, list) and len(value) >= 2:
                    return safe_int(value[0]), safe_int(value[1])
                if isinstance(value, dict):
                    return safe_int(value.get("home", value.get("1", 0))), safe_int(value.get("away", value.get("2", 0)))

        return 0, 0

    @staticmethod
    def extract_ht_total(event_or_view: dict[str, Any]) -> Optional[int]:
        """İlk yarı toplam golü yakalayabilirsek döndürür; yoksa None."""
        # Yaygın format denemeleri.
        scores = event_or_view.get("scores") or event_or_view.get("score") or {}
        if isinstance(scores, dict):
            for key in ("1", "1st", "1st_half", "first_half", "ht", "HT"):
                val = scores.get(key)
                if isinstance(val, dict):
                    return safe_int(val.get("home")) + safe_int(val.get("away"))
                if isinstance(val, str) and "-" in val:
                    h, a = parse_score(val)
                    return h + a
        # Bazı feedlerde ss_half gibi olabilir.
        for key in ("ss_half", "ht_score", "half_score"):
            val = event_or_view.get(key)
            if isinstance(val, str) and "-" in val:
                h, a = parse_score(val)
                return h + a
        return None

    @classmethod
    async def build_snapshot(cls, api: BetsApiClient, raw_event: dict[str, Any]) -> Optional[MatchSnapshot]:
        event_id = str(raw_event.get("id", ""))
        if not event_id:
            return None

        home_name, away_name = cls.extract_teams(raw_event)
        if not home_name or not away_name:
            return None

        minute = cls.extract_minute(raw_event)
        if minute <= 0 or minute > 95:
            return None

        score = raw_event.get("ss") or "0-0"
        home_goals, away_goals = parse_score(score)
        league = cls.extract_league(raw_event)

        # event/view stats ve HT bilgisi için daha dolu veri verir.
        view = await api.event_view(event_id)
        stats_source = None
        ht_total = None
        if view:
            stats_source = view.get("stats")
            ht_total = cls.extract_ht_total(view)
        if not stats_source:
            stats_source = raw_event.get("stats")
        if ht_total is None:
            ht_total = cls.extract_ht_total(raw_event)

        home_corners, away_corners = cls.extract_corners(stats_source)
        ah, home_odds, away_odds = await api.asian_handicap(event_id)

        # AH yoksa stratejiler çalışamaz; yine de debug için döndürmek yerine ele.
        if ah is None:
            if DEBUG_NO_SIGNAL:
                logger.info("AH yok: %s %s %s", home_name, score, away_name)
            return None

        return MatchSnapshot(
            event_id=event_id,
            league=league,
            home_name=home_name,
            away_name=away_name,
            minute=minute,
            home_goals=home_goals,
            away_goals=away_goals,
            home_corners=home_corners,
            away_corners=away_corners,
            ah=ah,
            ah_home_odds=home_odds,
            ah_away_odds=away_odds,
            ht_total_goals=ht_total,
        )

# =============================================================================
# LEAGUE FILTER
# =============================================================================

class LeagueFilter:
    REJECT_PATTERNS = [
        r"\be[-\s]?sport[s]?\b",
        r"\bvirtual\b",
        r"\bsimulat",
        r"\b(women|woman|kadın|kadin)\b",
        r"\b(reserve|reserves|rezerv)\b",
        r"\b(youth|junior|academy)\b",
        r"\bu\d{2}\b",
    ]
    QUARANTINE = [
        "brazil", "brasil", "kenya", "ethiopia", "rwanda", "oman", "kuwait", "iraq stars", "afghanistan"
    ]

    @classmethod
    def allowed(cls, snapshot: MatchSnapshot) -> bool:
        text = f"{snapshot.league} {snapshot.home_name} {snapshot.away_name}".lower()
        if any(re.search(p, text) for p in cls.REJECT_PATTERNS):
            return False
        if any(q in text for q in cls.QUARANTINE):
            # İstersen burada tamamen engellemek yerine risk yükseltebilirsin.
            return False
        return True

# =============================================================================
# BOT CORE
# =============================================================================

class ExcelStrategyBot:
    def __init__(self) -> None:
        if not TELEGRAM_TOKEN or not CHAT_ID or not BETSAPI_TOKEN:
            raise RuntimeError("TELEGRAM_TOKEN, CHAT_ID ve BETSAPI_TOKEN env değişkenleri zorunlu.")
        self.bot = Bot(token=TELEGRAM_TOKEN)
        self.queue: asyncio.Queue[str] = asyncio.Queue()

    async def send_loop(self) -> None:
        while True:
            msg = await self.queue.get()
            try:
                await self.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            except Exception:
                try:
                    await self.bot.send_message(chat_id=CHAT_ID, text=md_escape_light(msg), parse_mode=None)
                except Exception as exc:
                    logger.error("Telegram send failed: %s", exc)
            finally:
                self.queue.task_done()
            await asyncio.sleep(1.2)

    async def notify_start(self) -> None:
        text = (
            "🚀 Excel Strategy Bot Clean V1 aktif\n\n"
            "Kaynak: Excel strateji motoru\n"
            "Ana stratejiler: S1-S11\n"
            "Şartlı strateji: S12\n"
            "Blok: Ağır favori 45+ trap\n"
            "API: BetsAPI inplay + event/view + event/odds\n\n"
            "ROI yazılmaz; break-even ve Wilson min oran yazılır."
        )
        await self.bot.send_message(chat_id=CHAT_ID, text=text, parse_mode=None)

    async def process_event(self, api: BetsApiClient, raw_event: dict[str, Any]) -> None:
        snapshot = await BetsApiParser.build_snapshot(api, raw_event)
        if not snapshot:
            return
        if not LeagueFilter.allowed(snapshot):
            return
        if abs(snapshot.score_diff) >= 3:
            return

        result = ExcelStrategyEngine.evaluate(snapshot)
        if not result:
            return

        if result.blocked and not SEND_BLOCK_MESSAGES:
            return

        if signal_history.seen(snapshot.event_id, snapshot.minute, result.strategy_id):
            return

        msg = MessageBuilder.build(snapshot, result)
        signal_history.save(snapshot.event_id, snapshot.minute, result.strategy_id)
        logger.info("SIGNAL %s | %s %s %s | %s", result.strategy_id, snapshot.home_name, snapshot.score, snapshot.away_name, result.market)
        await self.queue.put(msg)

    async def run(self) -> None:
        await self.notify_start()
        asyncio.create_task(self.send_loop())

        async with aiohttp.ClientSession() as session:
            api = BetsApiClient(BETSAPI_TOKEN, session)
            loop_no = 0
            while True:
                loop_no += 1
                try:
                    events = await api.inplay_events()
                    logger.info("Loop #%s | inplay matches: %s", loop_no, len(events))
                    await asyncio.gather(*(self.process_event(api, e) for e in events), return_exceptions=True)
                except Exception as exc:
                    logger.exception("Main loop error: %s", exc)
                await asyncio.sleep(POLL_SECONDS)

# =============================================================================
# ENTRYPOINT
# =============================================================================

async def main() -> None:
    bot = ExcelStrategyBot()
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
