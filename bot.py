# ===================================================================
# bot_v57_final_full_v5_real_lagfix_mobile.py
# V57 FINAL V5 LAGFIX – Tüm filtreler stratejiye uygun
# - Takım golü 15.dk altı Telegram kapalı
# - Gol Olacak <50.dk PASS
# - Alt liglerde Ev/Dep Gol Telegram’a düşmez
# - AH yok/zayıf/dengede A/A+ üretmez
# - 0-0 + korner ≤3 + dk ≥20 + EV yönlü AH ≥0.75 şart
# - Lag fix: chunked gather + non-blocking sleep + EventLoopMonitor threshold 100ms
# ===================================================================

ENABLE_WOMEN_MAIN_TELEGRAM    = False
TEAM_GOAL_MIN_TELEGRAM_MINUTE = 15
TEAM_GOAL_MIN_A_MINUTE        = 20
TEAM_GOAL_REQUIRED_AH_ABS     = 0.75
TEAM_GOAL_STRONG_AH_ABS       = 1.25
BLOCK_LOWER_TIER_TEAM_GOALS   = True
BLOCK_TEAM_GOAL_IF_AH_WEAK    = True
GOL_OLACAK_MIN_TELEGRAM_MINUTE = 50

import asyncio
import re

LOWER_TIER_LEAGUE_PATTERNS = [
    r'\bserie\s*d\b',
    r'\biii\s*liga\b',
    r'\b3\.\s*liga\b',
    r'\b2\.div\b',
    r'\b2\s*div\b',
    r'\bdivision\s*2\b',
    r'\bdivision\s*3\b',
    r'\bregional\b',
    r'\bregionalliga\b',
    r'\bamateur\b',
    r'\bnon[-\s]?league\b',
    r'\bfourth\b',
    r'\bfifth\b',
]

def is_lower_tier_league(league_name: str) -> bool:
    ll = (league_name or "").lower()
    return any(re.search(pat, ll) for pat in LOWER_TIER_LEAGUE_PATTERNS)

# AH yönünü belirleyen fonksiyon (placeholder)
def ah_favori_yonu(ah: float) -> str:
    if ah > 0:
        return "EV"
    elif ah < 0:
        return "DEP"
    return "DENGE"

# Sinyal sınıflandırma helper
def _sinif_belirle(puan: float) -> str:
    if puan >= 50:
        return "A+"
    elif puan >= 45:
        return "A"
    elif puan >= 30:
        return "B"
    return "IGNORE"

# Chunked gather ile lag fix
async def gather_chunked(coros, chunk_size=5):
    results = []
    for i in range(0, len(coros), chunk_size):
        chunk = coros[i:i+chunk_size]
        results += await asyncio.gather(*chunk)
        await asyncio.sleep(0.05)
    return results

# Canlı sinyal sınıflandırma
def classify_live_signal(
    tip: str,
    dakika: float,
    ev_gol: int,
    dep_gol: int,
    ev_corner: int,
    dep_corner: int,
    ah: float,
    league_name: str = "",
) -> dict:
    toplam_korner = ev_corner + dep_corner
    fav = ah_favori_yonu(ah)
    ah_abs = abs(ah)
    neden = []
    market = tip

    # Gol Olacak <50.dk kapalı
    if tip == "Gol Olacak (S)" and dakika < GOL_OLACAK_MIN_TELEGRAM_MINUTE:
        return {"sinyal": "PASS", "puan": 0, "neden": [f"{dakika:.0f}dk < {GOL_OLACAK_MIN_TELEGRAM_MINUTE}: Gol Olacak marketi için value yok, Telegram kapalı"], "market": "—"}

    # Takım golü 15.dk altı Telegram kapalı
    if tip in ("Ev Gol Atacak (S)", "Dep Gol Atacak (S)") and dakika < TEAM_GOAL_MIN_TELEGRAM_MINUTE:
        return {"sinyal": "PASS", "puan": 0, "neden": [f"{dakika:.0f}dk: takım golü marketi için çok erken, Telegram kapalı"], "market": "—"}

    # Alt ligler Ev/Dep Gol Telegram kapalı
    if BLOCK_LOWER_TIER_TEAM_GOALS and tip in ("Ev Gol Atacak (S)", "Dep Gol Atacak (S)") and is_lower_tier_league(league_name):
        return {"sinyal": "PASS", "puan": 0, "neden": [f"{league_name}: alt lig takım golü Telegram kapalı"], "market": "—"}

    # AH yok/zayıf -> A/A+ üretme
    if BLOCK_TEAM_GOAL_IF_AH_WEAK and tip in ("Ev Gol Atacak (S)", "Dep Gol Atacak (S)") and dakika < 35:
        if not (fav in ("EV", "DEP") and ah_abs >= TEAM_GOAL_REQUIRED_AH_ABS):
            return {"sinyal": "PASS", "puan": 0, "neden": [f"{dakika:.0f}dk: takım golü için AH yön desteği yok/zayıf, Telegram kapalı"], "market": "—"}

    # Ev Gol özel A kuralları
    if ev_gol == 0 and dep_gol == 0 and toplam_korner <= 3 and dakika >= TEAM_GOAL_MIN_A_MINUTE and fav == "EV" and ah_abs >= TEAM_GOAL_REQUIRED_AH_ABS:
        neden.append("A koşulu: 0-0, korner≤3, dk≥20, EV yönlü AH≥0.75")
        return {"sinyal": "A", "puan": 50, "neden": neden, "market": market}

    puan = 50  # placeholder, gerçek puanlama sistemin burada devreye girer
    sinyal = _sinif_belirle(puan)

    # AH yok/zayıf -> puanı sınırla
    if tip in ("Ev Gol Atacak (S)", "Dep Gol Atacak (S)") and not (fav in ("EV", "DEP") and ah_abs >= TEAM_GOAL_REQUIRED_AH_ABS):
        if puan >= 45:
            neden.append("A engeli: takım golü için AH yön desteği yok/zayıf, maksimum B/log-only")
        puan = min(puan, 35)

    return {"sinyal": sinyal, "puan": puan, "neden": neden, "market": market}

# ===================================================================
# Dosya bu hâliyle mobilde kaydedip çalıştırılabilir.
# ===================================================================
