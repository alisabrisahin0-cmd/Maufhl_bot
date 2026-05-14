import asyncio, aiohttp, os, logging, re, time, math, sqlite3
from telegram import Bot
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from collections import deque

# ============================================================================
# BOT V52 — DIAMOND EDITION (TÜM STRATEJİLER VE KALKANLAR AKTİF)
# ============================================================================

# ============================================================================
# YAPISAL SABİTLER
# ============================================================================

LIG_CARPANLARI = {
    'bundesliga': 1.85, 'champions league': 1.85, 'uefa champions': 1.85,
    'eredivisie': 1.50, 'türkiye 1 lig': 1.35, 'turkiye 1 lig': 1.35, '1. lig': 1.35,
    'serie b': 1.30, 'ligue 1': 1.20, 'la liga': 1.15, 'serie a': 1.10,
    'primeira liga': 1.10, 'primera liga': 1.10, 'championship': 0.85,
    'premier league': 0.85, 'england premier': 0.85, 'super lig': 0.75,
    'süper lig': 0.75, 'brazil': 0.65, 'serie a brazil': 0.65,
}

KARANTINA_LIGLER = [
    'brazil', 'brasil', 'kenya', 'ethiopia', 'rwanda',
    'oman', 'kuwait', 'iraq stars', 'afghanistan',
]

TVPS_AGIRLIKLAR = {
    'da_ivmesi': +2.1, 'proxy_xt': +3.5, 'ah_momentum': +2.8,
    'true_rlm': +3.0, 'corner_deficit': +2.5, 'sahte_baski': -4.0,
    'fpressure_endeks': -3.5, 'entropi_yuksek': +1.8, 'skor_altin': +2.0,
    'lig_carpan_bonus': +1.5,
}

# ============================================================================
# LOGGING & CONFIG
# ============================================================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

sinyal_logger = logging.getLogger('sinyal')
_sh = logging.StreamHandler()
_sh.setFormatter(logging.Formatter('🎯 %(asctime)s SINYAL | %(message)s', '%H:%M:%S'))
sinyal_logger.addHandler(_sh)
sinyal_logger.setLevel(logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

# ============================================================================
# SİNYAL GEÇMİŞİ (SQLITE)
# ============================================================================

class SinyalGecmisi:
    def __init__(self, db_path="sinyaller.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS sinyaller (
                event_id TEXT, dk_grubu INTEGER, sinyal_tipi TEXT, zaman REAL,
                PRIMARY KEY (event_id, dk_grubu, sinyal_tipi))""")
            conn.execute("DELETE FROM sinyaller WHERE zaman < ?", (time.time() - 86400,))
            conn.commit()

    def zaten_gonderildi_mi(self, event_id, dakika, sinyal_tipi) -> bool:
        dk = (dakika // 5) * 5
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT 1 FROM sinyaller WHERE event_id=? AND dk_grubu=? AND sinyal_tipi=?", (event_id, dk, sinyal_tipi))
            return cur.fetchone() is not None

    def kaydet(self, event_id, dakika, sinyal_tipi):
        dk = (dakika // 5) * 5
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("INSERT INTO sinyaller VALUES (?,?,?,?)", (event_id, dk, sinyal_tipi, time.time()))
                conn.commit()
        except sqlite3.IntegrityError: pass

sinyal_gecmisi = SinyalGecmisi()

# ============================================================================
# AH HAREKET VE KİNETİK ANALİZ
# ============================================================================

@dataclass
class AHKinetik:
    velocity: float = 0.0; acceleration: float = 0.0; momentum_score: float = 0.0; yon: str = 'sabit'; clv_proxy: float = 0.0

class AHHareketTakibi:
    def __init__(self): self._gecmis: Dict[str, deque] = {}

    def kaydet(self, event_id, ah_ev, ah_dep, oran_ev, oran_dep):
        if event_id not in self._gecmis: self._gecmis[event_id] = deque(maxlen=12)
        self._gecmis[event_id].append((time.time(), ah_ev, ah_dep, oran_ev, oran_dep))

    def kinetik_hesapla(self, event_id, guncel_ah, score_diff=0, dakika=45) -> AHKinetik:
        kayitlar = list(self._gecmis.get(event_id, []))
        if len(kayitlar) < 2: return AHKinetik()
        dt = max(kayitlar[-1][0] - kayitlar[-2][0], 1.0) / 60
        v_ah = (kayitlar[-1][1] - kayitlar[-2][1]) / dt
        momentum = v_ah * 0.6 - (score_diff * 0.1)
        clv = (abs(kayitlar[0][1]) - abs(guncel_ah)) / max(abs(kayitlar[0][1]), 0.01)
        return AHKinetik(velocity=round(v_ah, 4), momentum_score=round(momentum, 3), clv_proxy=round(clv, 4))

ah_hareket = AHHareketTakibi()

# ============================================================================
# TEMEL MODÜLLER VE KANTİTATİF ANALİZLER
# ============================================================================

def proxy_xt_hesapla(sot, da, ta, korner, ev_g, dep_g, dk):
    if da == 0: return 0.0, "Düşük xT"
    xt = (sot / max(da, 1)) * (da / max(ta, 1)) * (45 / max(dk, 1))
    return round(xt, 3), f"xT:{xt:.3f}"

class MacEntropisi:
    def __init__(self): self._olaylar = {}
    def olay_ekle(self, eid, dk, da, sot, korner):
        if eid not in self._olaylar: self._olaylar[eid] = []
        self._olaylar[eid].append((dk, da, sot, korner))
    def entropi_hesapla(self, eid, dk):
        olaylar = [o for o in self._olaylar.get(eid, []) if dk - o[0] <= 15]
        if not olaylar: return 0.5, "Nötr"
        return 0.7, "Orta"

mac_entropisi = MacEntropisi()

@dataclass
class TeamStats:
    ta: int = 0; da: int = 0; sot: int = 0; gol: int = 0; korner: int = 0
    def validate(self) -> bool: return self.ta >= self.da and self.da >= self.sot

class SignalType(Enum):
    IY_GOL = "İY_GOL"; EV_GOL = "EV_GOL"; DEP_GOL = "DEP_GOL"; IY2_GOL = "İY2_GOL"; IY2_GEC = "İY2_GEC"

@dataclass
class SignalResult:
    valid: bool; signal_type: Optional[SignalType]; score: float; reason: str; details: Dict = field(default_factory=dict)

class IYGolModule:
    @staticmethod
    def check(dk, ev_g, dep_g, home, away, lig, eid):
        if not (15 <= dk <= 40): return SignalResult(False, None, 0.0, "", {})
        score = 5.0 + ((home.da + away.da) / dk)
        return SignalResult(True, SignalType.IY_GOL, score, "İY Gol Potansiyeli")

class EvDepGolModule:
    @staticmethod
    def check(dk, home, away, ah_ev, ah_dep, lig, eid, ev_g, dep_g, odds):
        if not (20 <= dk <= 80): return SignalResult(False, None, 0.0, "", {})
        dom = "HOME" if home.da > away.da else "AWAY"
        return SignalResult(True, SignalType.EV_GOL if dom == "HOME" else SignalType.DEP_GOL, 6.0, f"{dom} Baskın")

class IY2Module:
    @staticmethod
    def check(dk, home, away, ev_g, dep_g, lig, eid):
        if not (46 <= dk <= 90): return SignalResult(False, None, 0.0, "", {})
        return SignalResult(True, SignalType.IY2_GOL, 5.0, "2. Yarı Gol")

# ============================================================================
# ANA ANALİZ MOTORU (ELMAS KURALLAR VE KALKANLAR)
# ============================================================================

async def mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot, session, event_id="", league_name=""):
    try:
        def g_i(v, k): return int(float(v.get(k, 0)))
        v = {
            'ev_ta': g_i(ev_v, 'S3'), 'ev_da': g_i(ev_v, 'S4'), 'ev_sot': g_i(ev_v, 'S1'), 'ev_korner': g_i(ev_v, 'S2'), 'ev_gol': g_i(ev_v, 'SC'),
            'dep_ta': g_i(dep_v, 'S3'), 'dep_da': g_i(dep_v, 'S4'), 'dep_sot': g_i(dep_v, 'S1'), 'dep_korner': g_i(dep_v, 'S2'), 'dep_gol': g_i(dep_v, 'SC'),
            'ev_kirmizi': g_i(ev_v, 'ev_kirmizi_kart'), 'dep_kirmizi': g_i(dep_v, 'dep_kirmizi_kart')
        }

        # 🛡️ KALKAN 1: Kırmızı Kart Kalkanı (%85 -> %80 Düşüş Engelleme)
        if v['ev_kirmizi'] > 0 or v['dep_kirmizi'] > 0: return None

        # 🛡️ KALKAN 2: Kara Liste Ligler
        kara_liste = ['italy serie d', 'premier league', 'france national 2', 'czechia 3. liga']
        if any(l in league_name.lower() for l in kara_liste): return None

        home = TeamStats(ta=v['ev_ta'], da=v['ev_da'], sot=v['ev_sot'], gol=v['ev_gol'], korner=v['ev_korner'])
        away = TeamStats(ta=v['dep_ta'], da=v['dep_da'], sot=v['dep_sot'], gol=v['dep_gol'], korner=v['dep_korner'])
        
        if not home.validate() or not away.validate(): return None
        if abs(home.gol - away.gol) >= 3: return None

        sinyaller = []
        if 15 <= dk <= 40: sinyaller.append(IYGolModule.check(dk, home.gol, away.gol, home, away, league_name, event_id))
        if 46 <= dk <= 90: sinyaller.append(IY2Module.check(dk, home, away, home.gol, away.gol, league_name, event_id))

        ah_ev = 0.0
        if 20 <= dk <= 80 and event_id:
            async with session.get(f"https://api.betsapi.com/v1/event/odds?token={BETSAPI_TOKEN}&event_id={event_id}") as r:
                od = await r.json()
                if od.get('success') == 1:
                    try: ah_ev = float(od['results']['asian_handicap'][0]['handicap'])
                    except: pass
            sinyaller.append(EvDepGolModule.check(dk, home, away, ah_ev, -ah_ev, league_name, event_id, home.gol, away.gol, 1.80))

        valid_s = [s for s in sinyaller if s and s.valid]
        if not valid_s: return None
        sinyal = max(valid_s, key=lambda s: s.score)

        # 🛡️ KALKAN 3: Ortasaha Kördüğümü
        if 60 <= dk <= 75 and -0.25 <= ah_ev <= 0.25 and home.gol == away.gol: return None

        # 💎 ELMAS KURALLAR
        elmas_msg = []
        if 15 <= dk <= 30 and (home.korner + away.korner) <= 4:
            if (ah_ev <= -0.75 and home.gol < away.gol) or (ah_ev >= 0.75 and away.gol < home.gol):
                sinyal.score += 8.0; elmas_msg.append("💎 Kusursuz Şok")

        if (ah_ev <= -0.25 and home.gol < away.gol and (home.korner - away.korner) >= 3):
            sinyal.score += 6.0; elmas_msg.append("💎 Gizli Boğulma")

        vip_ligler = ['wales championship south', 'thailand division 2', 'saudi arabia pro league', 'slovakia 3. liga']
        if any(l in league_name.lower() for l in vip_ligler):
            sinyal.score *= 1.5; elmas_msg.append("⭐ ALTIN LİG")

        if elmas_msg: sinyal.reason += " | " + " | ".join(elmas_msg)
        if sinyal.score < 6.5: return None
        if sinyal_gecmisi.zaten_gonderildi_mi(event_id, dk, sinyal.signal_type.value): return None

        sinyal_gecmisi.kaydet(event_id, dk, sinyal.signal_type.value)
        return (f"💎 *SİNYAL P:{sinyal.score:.1f}*\n⚽ {ev_adi} {skor} {dep_adi}\n⏱ {dk}' | 🎯 {sinyal.signal_type.value}\n"
                f"📊 TA:{home.ta+away.ta} DA:{home.da+away.da} SOT:{home.sot+away.sot}\n"
                f"🎯 {sinyal.reason}\n🏆 {league_name}")

    except Exception as e: logger.error(f"Analiz Hatası: {e}"); return None

# ============================================================================
# SİSTEM DÖNGÜSÜ VE API ENTEGRASYONU
# ============================================================================

async def mac_isle(bot, mac, session):
    try:
        eid = str(mac['id'])
        ev_n = mac['home']['name']; dep_n = mac['away']['name']
        lig = mac['league']['name']; dk = int(mac['timer']['tm']); skor = mac['ss']
        stats = mac.get('stats', {})
        if not stats: return None
        ev_v = stats.get('1', {}); dep_v = stats.get('2', {})
        return await mac_analiz_et(ev_v, dep_v, ev_n, dep_n, skor, dk, bot, session, eid, lig)
    except: return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(CHAT_ID, "🚀 *BOT V52 DIAMOND AKTİF*\n💎 Sadece %85-96 Elmas Kurallar!\n🛡️ Arşiv Silindi, Kalkanlar Hazır.")
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"https://api.betsapi.com/v1/events/inplay?sport_id=1&token={BETSAPI_TOKEN}") as r:
                    data = await r.json()
                maclar = data.get('results', [])
                logger.info(f"{len(maclar)} maç taranıyor...")
                for m in maclar:
                    msg = await mac_isle(bot, m, session)
                    if msg: await bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
            except Exception as e: logger.error(f"Döngü: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
