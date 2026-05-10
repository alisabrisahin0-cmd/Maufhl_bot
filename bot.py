import asyncio, aiohttp, os, urllib.parse, logging, re, time
from telegram import Bot
from collections import deque
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple

# ============================================================================
# 🎯 TRADING BOT PHILOSOPHY - +EV FOCUSED
# ============================================================================
"""
FELSEFE DEĞİŞİMİ:
❌ ESKİ: "Maçta gol olur mu?" tahmini (Kumar)
✅ YENİ: Asya Handikapı merkezli, +EV odaklı trading (Matematik)

TEMEL PRENSİPLER:
1. Sürü psikolojisinin tersine işlem
2. Matematiksel olarak pozitif beklenen değer (+EV)
3. Veri odaklı, duygusuz karar verme
4. Risk yönetimi ve sermaye koruma
"""

# ============================================================================
# 📋 MODULE 1: LİG FİLTRELEME (League Filtering - Whitelist/Blacklist)
# ============================================================================

class LeagueFilter:
    """
    🎯 Lig Filtreleme Modülü
    
    Whitelist: Yüksek gol potansiyeli olan ligler
    Blacklist: Skoru koruma eğilimi yüksek ligler
    """
    
    # Whitelist: Yüksek gol potansiyeli (Almanya, Hollanda, U23/Gençlik)
    WHITELIST_KEYWORDS = [
        'bundesliga', 'germany', 'deutschland', 'german',
        'eredivisie', 'netherlands', 'holland', 'dutch',
        'u23', 'u21', 'u20', 'u19', 'u18',
        'youth', 'junior', 'academy',
        'premier league', 'championship',
        'serie a', 'la liga', 'ligue 1'
    ]
    
    # Blacklist: Düşük gol potansiyeli (Savunma odaklı alt ligler)
    BLACKLIST_KEYWORDS = [
        'third division', '3. liga', 'regionalliga',
        'amateur', 'lower league',
        'defensive league', 'low scoring'
    ]
    
    # Özel durumlar (her zaman reddet)
    ALWAYS_REJECT = [
        'e-sport', 'esport', 'virtual', 'simulation',
        'women', 'kadın', 'kadin', 'w ',
        'reserves', 'reserve'
    ]
    
    @staticmethod
    def check_league(league_name: str, home_team: str, away_team: str) -> Tuple[bool, str]:
        """
        Lig kontrolü yapar
        
        Returns:
            (bool, str): (Geçerli mi?, Sebep)
        """
        full_text = f"{league_name} {home_team} {away_team}".lower()
        
        # 1. Önce ALWAYS_REJECT kontrolü (en yüksek öncelik)
        for keyword in LeagueFilter.ALWAYS_REJECT:
            if keyword in full_text:
                return False, f"ALWAYS_REJECT: '{keyword}' tespit edildi"
        
        # 2. Whitelist kontrolü
        whitelist_match = False
        for keyword in LeagueFilter.WHITELIST_KEYWORDS:
            if keyword in full_text:
                whitelist_match = True
                break
        
        # 3. Blacklist kontrolü
        for keyword in LeagueFilter.BLACKLIST_KEYWORDS:
            if keyword in full_text:
                return False, f"BLACKLIST: '{keyword}' tespit edildi"
        
        # 4. Whitelist'te varsa kabul et
        if whitelist_match:
            return True, "WHITELIST: Yüksek gol potansiyeli"
        
        # 5. Whitelist'te yoksa ama blacklist'te de yoksa, nötr kabul et
        return True, "NEUTRAL: Standart lig"
    
    @staticmethod
    def get_league_multiplier(league_name: str) -> float:
        """
        Lig bazlı puan çarpanı
        
        Returns:
            float: 0.8 - 1.5 arası çarpan
        """
        league_lower = league_name.lower()
        
        # Premium ligler (1.5x)
        if any(k in league_lower for k in ['bundesliga', 'eredivisie', 'premier league']):
            return 1.5
        
        # İyi ligler (1.2x)
        if any(k in league_lower for k in ['championship', 'serie a', 'la liga']):
            return 1.2
        
        # U23/Gençlik ligleri (1.3x - yüksek gol)
        if any(k in league_lower for k in ['u23', 'u21', 'u20', 'youth']):
            return 1.3
        
        # Standart (1.0x)
        return 1.0

# ============================================================================
# 📋 MODULE 2: VERİ KORUMA KATMANI - TAKIM BAZLI (Team-based Data Protection)
# ============================================================================

@dataclass
class TeamStats:
    """Takım bazlı istatistikler"""
    ta: int = 0      # Toplam Atak
    da: int = 0      # Tehlikeli Atak
    sot: int = 0     # İsabetli Şut
    gol: int = 0     # Gol
    korner: int = 0  # Korner
    
    def validate_hierarchy(self) -> Tuple[bool, List[str]]:
        """
        Fiziksel hiyerarşi: TA >= DA >= SOT >= Gol
        
        Returns:
            (bool, List[str]): (Geçerli mi?, Hata listesi)
        """
        errors = []
        
        if self.ta < self.da:
            errors.append(f"TA ({self.ta}) < DA ({self.da})")
        if self.da < self.sot:
            errors.append(f"DA ({self.da}) < SOT ({self.sot})")
        if self.sot < self.gol:
            errors.append(f"SOT ({self.sot}) < Gol ({self.gol})")
        
        return len(errors) == 0, errors
    
    def calculate_xg(self) -> float:
        """
        xG (Beklenen Gol) hesapla
        
        Formül: xG = (SOT * 0.15) + (DA * 0.015) + (TA * 0.01) + (Korner * 0.03)
        
        🔧 FIX: DA katsayısı 0.05 → 0.015 (Sahte baskı yanlış tespit düzeltmesi)
        """
        # 🛡️ GÜVENLİK KONTROLLERI
        sot = max(0, self.sot)
        da = max(0, self.da)
        ta = max(0, self.ta)
        korner = max(0, self.korner)
        
        xg = (sot * 0.15) + (da * 0.015) + (ta * 0.01) + (korner * 0.03)
        return round(xg, 2)
    
    def detect_fake_pressure(self) -> bool:
        """
        Sahte baskı tespiti
        
        Mantık: Yüksek TA/DA ama düşük SOT = Sahte baskı
        """
        if self.da > 0 and self.sot > 0:
            da_sot_ratio = self.da / self.sot
            # DA/SOT oranı > 8 ise sahte baskı
            if da_sot_ratio > 8:
                return True
        
        # Yüksek korner ama düşük SOT
        if self.korner >= 8 and self.sot < 5:
            return True
        
        return False

class MatchDataProtection:
    """
    🛡️ Maç Veri Koruma Katmanı (Takım Bazlı)
    
    - Kopmuş maç filtresi (Toplam gol >= 5)
    - Takım bazlı fiziksel hiyerarşi
    - Sahte baskı tespiti
    """
    
    @staticmethod
    def check_broken_match(total_goals: int) -> Tuple[bool, str]:
        """
        Kopmuş maç kontrolü
        
        Returns:
            (bool, str): (Geçerli mi?, Durum)
        """
        if total_goals >= 5:
            return False, f"KOPMUŞ MAÇ: Toplam gol {total_goals} >= 5"
        return True, "OK"
    
    @staticmethod
    def validate_match_data(home: TeamStats, away: TeamStats) -> Tuple[bool, List[str]]:
        """
        Maç verisi doğrulama
        
        Returns:
            (bool, List[str]): (Geçerli mi?, Hata listesi)
        """
        errors = []
        
        # 1. Ev sahibi hiyerarşi
        home_ok, home_errors = home.validate_hierarchy()
        if not home_ok:
            errors.extend([f"EV: {e}" for e in home_errors])
        
        # 2. Deplasman hiyerarşi
        away_ok, away_errors = away.validate_hierarchy()
        if not away_ok:
            errors.extend([f"DEP: {e}" for e in away_errors])
        
        # 3. Kopmuş maç kontrolü
        total_goals = home.gol + away.gol
        broken_ok, broken_msg = MatchDataProtection.check_broken_match(total_goals)
        if not broken_ok:
            errors.append(broken_msg)
        
        # 4. Sahte baskı kontrolü
        if home.detect_fake_pressure():
            errors.append("EV: Sahte baskı tespit edildi")
        if away.detect_fake_pressure():
            errors.append("DEP: Sahte baskı tespit edildi")
        
        return len(errors) == 0, errors

# ============================================================================
# 📋 MODULE 3: SİNYAL SINIFLARI (Signal Classes - Modular)
# ============================================================================

class SignalType(Enum):
    """Sinyal tipleri"""
    IY_GOL = "İY_GOL"              # İlk yarı gol
    EV_GOL = "EV_GOL"              # Ev sahibi gol
    DEP_GOL = "DEP_GOL"            # Deplasman gol
    IY2_GOL = "İY2_GOL"            # İkinci yarı erken (46-65)
    IY2_GEC = "İY2_GEC"            # İkinci yarı geç (76-90)

@dataclass
class SignalResult:
    """Sinyal sonucu"""
    valid: bool
    signal_type: Optional[SignalType]
    score: float
    reason: str
    details: Dict

class IYGolModule:
    """
    🎯 İlk Yarı Gol Modülü
    
    Koşullar:
    - 15-40 dakika arası
    - 0-0 skor (veya 1-0, 0-1)
    - DA ivmesi yüksek
    """
    
    @staticmethod
    def check(minute: int, home_score: int, away_score: int,
              home: TeamStats, away: TeamStats) -> SignalResult:
        """İY Gol sinyali kontrolü"""
        
        # Dakika kontrolü
        if not (15 <= minute <= 40):
            return SignalResult(False, None, 0.0, "Dakika aralık dışı", {})
        
        # Skor kontrolü (0-0, 1-0, 0-1)
        total_goals = home_score + away_score
        if total_goals > 1:
            return SignalResult(False, None, 0.0, "Skor uygun değil", {})
        
        # DA ivmesi kontrolü
        total_da = home.da + away.da
        da_per_minute = total_da / minute if minute > 0 else 0
        
        # DA ivmesi >= 1.5 olmalı
        if da_per_minute < 1.5:
            return SignalResult(False, None, 0.0, f"DA ivmesi düşük: {da_per_minute:.2f}", {})
        
        # Puan hesapla
        score = 5.0  # Baz puan
        score += min(da_per_minute * 2, 5.0)  # DA ivmesi bonusu (max 5)
        
        # Altın pencere bonusu (24-36 dk)
        if 24 <= minute <= 36:
            score += 3.0
        
        return SignalResult(
            valid=True,
            signal_type=SignalType.IY_GOL,
            score=score,
            reason="İY Gol sinyali - DA ivmesi yüksek",
            details={
                'da_per_minute': da_per_minute,
                'total_da': total_da,
                'minute': minute
            }
        )

class EvDepGolModule:
    """
    🎯 Ev/Deplasman Gol Modülü
    
    Koşullar:
    - AH çizgisi: -0.75, -1.0, -1.5 (favoriler)
    - DA oranı > 1.8 (baskın takım)
    - SOT tuzağı yok
    """
    
    @staticmethod
    def check(minute: int, home: TeamStats, away: TeamStats,
              ah_home: float, ah_away: float) -> SignalResult:
        """Ev/Dep Gol sinyali kontrolü"""
        
        # Dakika kontrolü (20-80 arası)
        if not (20 <= minute <= 80):
            return SignalResult(False, None, 0.0, "Dakika aralık dışı", {})
        
        # DA oranı kontrolü
        total_da = home.da + away.da
        if total_da == 0:
            return SignalResult(False, None, 0.0, "DA verisi yok", {})
        
        home_da_ratio = home.da / total_da if total_da > 0 else 0
        away_da_ratio = away.da / total_da if total_da > 0 else 0
        
        # Hangi takım baskın?
        dominant_team = None
        dominant_ratio = 0
        signal_type = None
        
        if home_da_ratio > 0.6:  # Ev sahibi baskın
            dominant_team = "HOME"
            dominant_ratio = home_da_ratio
            signal_type = SignalType.EV_GOL
            
            # AH kontrolü (ev sahibi favori olmalı: negatif handikap)
            if ah_home >= 0:
                return SignalResult(False, None, 0.0, "Ev sahibi favori değil (AH)", {})
        
        elif away_da_ratio > 0.6:  # Deplasman baskın
            dominant_team = "AWAY"
            dominant_ratio = away_da_ratio
            signal_type = SignalType.DEP_GOL
            
            # AH kontrolü (deplasman favori olmalı: pozitif handikap)
            if ah_away <= 0:
                return SignalResult(False, None, 0.0, "Deplasman favori değil (AH)", {})
        
        else:
            return SignalResult(False, None, 0.0, "Baskın takım yok", {})
        
        # SOT tuzağı kontrolü
        dominant_stats = home if dominant_team == "HOME" else away
        if dominant_stats.detect_fake_pressure():
            return SignalResult(False, None, 0.0, "SOT tuzağı tespit edildi", {})
        
        # Puan hesapla
        score = 6.0  # Baz puan
        score += (dominant_ratio - 0.6) * 10  # Baskınlık bonusu
        
        # AH değerli çizgi bonusu
        ah_value = abs(ah_home) if dominant_team == "HOME" else abs(ah_away)
        if ah_value >= 1.0:
            score += 2.0
        
        return SignalResult(
            valid=True,
            signal_type=signal_type,
            score=score,
            reason=f"{dominant_team} baskın - AH değerli çizgi",
            details={
                'dominant_team': dominant_team,
                'da_ratio': dominant_ratio,
                'ah_value': ah_value
            }
        )

class IY2Module:
    """
    🎯 İkinci Yarı Gol Modülü
    
    İki pencere:
    1. Erken (46-65 dk): Yüksek enerji, savunma organize değil
    2. Geç (76-90 dk): Yorgunluk, açılan savunma
    """
    
    @staticmethod
    def check(minute: int, home: TeamStats, away: TeamStats,
              home_score: int, away_score: int) -> SignalResult:
        """İkinci yarı gol sinyali kontrolü"""
        
        # Pencere belirleme
        if 46 <= minute <= 65:
            window = "ERKEN"
            signal_type = SignalType.IY2_GOL
            base_score = 5.0
        elif 76 <= minute <= 90:
            window = "GEC"
            signal_type = SignalType.IY2_GEC
            base_score = 4.0
        else:
            return SignalResult(False, None, 0.0, "Dakika aralık dışı", {})
        
        # Skor farkı kontrolü (fark >= 3 ise rölanti)
        score_diff = abs(home_score - away_score)
        if score_diff >= 3:
            return SignalResult(False, None, 0.0, "Rölanti evresi (fark >= 3)", {})
        
        # SOT tuzağı kontrolü
        total_sot = home.sot + away.sot
        if total_sot > 15:  # Çok fazla şut = epilasyon
            return SignalResult(False, None, 0.0, f"SOT epilasyonu: {total_sot}", {})
        
        # DA momentum kontrolü
        total_da = home.da + away.da
        da_per_minute = total_da / minute if minute > 0 else 0
        
        if da_per_minute < 1.0:
            return SignalResult(False, None, 0.0, "DA momentum düşük", {})
        
        # Puan hesapla
        score = base_score
        score += min(da_per_minute, 3.0)  # DA momentum bonusu
        
        # Kırılma evresi bonusu (48-58 dk)
        if 48 <= minute <= 58:
            score += 5.0
        
        return SignalResult(
            valid=True,
            signal_type=signal_type,
            score=score,
            reason=f"İY2 {window} pencere - DA momentum yüksek",
            details={
                'window': window,
                'da_per_minute': da_per_minute,
                'total_sot': total_sot
            }
        )

# ============================================================================
# 📋 MODULE 4: AI PROMPT MÜHENDİSLİĞİ (Statistical, Context-focused)
# ============================================================================

class TradingPromptEngine:
    """
    🤖 Trading Bot için AI Prompt Mühendisliği
    
    Eski format: "Maçta gol olur mu?"
    Yeni format: İstatistiksel, bağlam odaklı, +EV analizi
    """
    
    @staticmethod
    def generate_trading_prompt(
        home_team: str,
        away_team: str,
        minute: int,
        score: str,
        home: TeamStats,
        away: TeamStats,
        signal_type: SignalType,
        ah_home: float,
        ah_away: float
    ) -> str:
        """
        Trading odaklı AI prompt oluştur
        
        Örnek:
        "Maçın 35. dakikası, skor 0-0, ev sahibi -1.0 handikaplı,
         DA ivmesi 2.3x, SOT tuzağı var mı? İY Gol olasılığı %?"
        """
        
        # DA ivmesi hesapla
        total_da = home.da + away.da
        da_momentum = total_da / minute if minute > 0 else 0
        
        # xG hesapla
        home_xg = home.calculate_xg()
        away_xg = away.calculate_xg()
        
        # Baskın takım
        if home.da > away.da * 1.5:
            dominant = "Ev sahibi"
        elif away.da > home.da * 1.5:
            dominant = "Deplasman"
        else:
            dominant = "Dengeli"
        
        prompt = f"""Sen bir TRADING BOT analistisin. Kumar değil, matematik yapıyorsun.

📊 MAÇ: {home_team} vs {away_team}
⏱️ Dakika: {minute}' | Skor: {score}
🎯 Sinyal Tipi: {signal_type.value}

📈 İSTATİSTİKLER (TAKIM BAZLI):
• Ev: TA={home.ta}, DA={home.da}, SOT={home.sot}, Gol={home.gol}, xG={home_xg}
• Dep: TA={away.ta}, DA={away.da}, SOT={away.sot}, Gol={away.gol}, xG={away_xg}
• DA Momentum: {da_momentum:.2f}x (dakika başı)
• Baskın Takım: {dominant}

💰 ASIAN HANDICAP:
• Ev: {ah_home} | Dep: {ah_away}

🎯 TRADING ANALİZİ (MAX 300 karakter):

1. **+EV VARLIK**: Bu çizgide matematiksel avantaj var mı?
2. **SÜRÜ PSİKOLOJİSİ**: Piyasa hangi tarafa yüklü? Ters işlem fırsatı?
3. **SOT TUZAĞI**: Yüksek DA ama düşük SOT = Sahte baskı mı?
4. **MOMENTUM**: DA ivmesi sürdürülebilir mi? Yorgunluk riski?
5. **SONUÇ**: Bu trade'i alır mısın? Neden?

⚠️ ÖNEMLİ:
- "Gol olur" deme, "+EV var" de
- Duygusal değil, matematiksel düşün
- Sürünün tersine git
- Risk/ödül oranını belirt
"""
        
        return prompt

# ============================================================================
# 🛡️ VERİ KORUMA KATMANI (Data Protection Layer) - EMBEDDED
# ============================================================================

def guvenli_int(deger, varsayilan=0):
    """Güvenli integer dönüşümü"""
    try:
        if deger == '' or deger is None:
            return varsayilan
        return int(float(deger))
    except:
        return varsayilan

def guvenli_float(deger, varsayilan=0.0):
    """Güvenli float dönüşümü"""
    try:
        if deger == '' or deger is None:
            return varsayilan
        return float(deger)
    except:
        return varsayilan

class VeriKorumaKatmani:
    """
    🛡️ Veri Koruma Katmanı (LİTERATÜR UYUMLU)
    
    Yeni stats formatını destekler:
    - stats['corners'][0] = ev sahibi
    - stats['corners'][1] = deplasman
    - Eski format fallback: stats['1']['S1']
    """
    
    def __init__(self):
        self.s_kod_mapping = {
            'S1': 'SOT',
            'S2': 'Korner',
            'S3': 'TA',
            'S4': 'DA',
            'SC': 'Gol'
        }
        self.anomali_sayaci = 0
        self.toplam_kontrol = 0
        
    def yeni_format_parse(self, stats):
        """
        🆕 YENİ FORMAT: API'den gelen liste formatı
        stats = {
            'corners': ['3', '2'],
            'yellowcards': ['2', '0'],
            'attacks': ['86', '103'],
            'dangerous_attacks': ['38', '45'],
            'on_target': ['3', '3'],
            'goals': ['0', '0']
        }
        """
        try:
            if not isinstance(stats, dict):
                return None
            
            # Yeni format kontrolü
            if 'corners' in stats and isinstance(stats.get('corners'), list):
                logger.info("✅ Yeni stats formatı tespit edildi")
                
                ev_v = {
                    'S1': stats.get('on_target', ['0', '0'])[0],  # SOT
                    'S2': stats.get('corners', ['0', '0'])[0],     # Korner
                    'S3': stats.get('attacks', ['0', '0'])[0],     # TA
                    'S4': stats.get('dangerous_attacks', ['0', '0'])[0],  # DA
                    'SC': stats.get('goals', ['0', '0'])[0]        # Gol
                }
                
                dep_v = {
                    'S1': stats.get('on_target', ['0', '0'])[1],
                    'S2': stats.get('corners', ['0', '0'])[1],
                    'S3': stats.get('attacks', ['0', '0'])[1],
                    'S4': stats.get('dangerous_attacks', ['0', '0'])[1],
                    'SC': stats.get('goals', ['0', '0'])[1]
                }
                
                return ev_v, dep_v
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Yeni format parse hatası: {str(e)}")
            return None
    
    def s_kodlari_tespit_et(self, ev_v, dep_v):
        """S-kodlarını dinamik olarak tespit eder"""
        s_kodlari = {}
        
        # Tüm S-kodlarını topla
        for key in list(ev_v.keys()) + list(dep_v.keys()):
            if key.startswith('S'):
                if key not in s_kodlari:
                    ev_val = guvenli_int(ev_v.get(key, 0))
                    dep_val = guvenli_int(dep_v.get(key, 0))
                    s_kodlari[key] = {
                        'ev': ev_val,
                        'dep': dep_val,
                        'toplam': ev_val + dep_val
                    }
        
        return s_kodlari
    
    def fiziksel_hiyerarsi_dogrula(self, ta, da, sot, gol):
        """
        Fiziksel hiyerarşi: TA >= DA >= SOT >= Gol
        
        Returns:
            (bool, list): (Geçerli mi?, Hata mesajları)
        """
        hatalar = []
        
        if ta < da:
            hatalar.append(f"TA ({ta}) < DA ({da})")
        if da < sot:
            hatalar.append(f"DA ({da}) < SOT ({sot})")
        if sot < gol:
            hatalar.append(f"SOT ({sot}) < Gol ({gol})")
        if ta < sot:
            hatalar.append(f"TA ({ta}) < SOT ({sot})")
        
        return len(hatalar) == 0, hatalar
    
    def veri_yapisini_kontrol_et(self, s_kodlari):
        """Veri yapısının değişip değişmediğini kontrol eder"""
        beklenen_kodlar = {'S1', 'S2', 'S3', 'S4', 'SC'}
        mevcut_kodlar = set(s_kodlari.keys())
        
        eksik_kodlar = beklenen_kodlar - mevcut_kodlar
        fazla_kodlar = mevcut_kodlar - beklenen_kodlar
        
        if eksik_kodlar or fazla_kodlar:
            logger.warning(f"⚠️ Veri yapısı değişikliği tespit edildi!")
            if eksik_kodlar:
                logger.warning(f"   Eksik kodlar: {eksik_kodlar}")
            if fazla_kodlar:
                logger.warning(f"   Yeni kodlar: {fazla_kodlar}")
            return False
        
        return True
    
    def akilli_s_kod_tespiti(self, s_kodlari):
        """
        S-kodlarını değerlerine göre akıllıca tespit eder
        
        Mantık:
        - En yüksek değer: TA (Toplam Atak)
        - İkinci yüksek: DA (Tehlikeli Atak)
        - Üçüncü: SOT veya Korner (değere göre)
        - En düşük: Gol
        """
        # SC'yi ayır (her zaman Gol)
        gol_kod = 'SC'
        gol_deger = s_kodlari.get(gol_kod, {}).get('toplam', 0)
        
        # Diğer S-kodlarını değere göre sırala
        diger_kodlar = {k: v['toplam'] for k, v in s_kodlari.items() if k != gol_kod}
        sirali_kodlar = sorted(diger_kodlar.items(), key=lambda x: x[1], reverse=True)
        
        if len(sirali_kodlar) >= 4:
            # En yüksek 4 değeri al
            ta_kod = sirali_kodlar[0][0]  # En yüksek = TA
            da_kod = sirali_kodlar[1][0]  # İkinci = DA
            
            # SOT ve Korner'i değere göre belirle
            # Genellikle SOT > Korner olur
            if sirali_kodlar[2][1] > sirali_kodlar[3][1]:
                sot_kod = sirali_kodlar[2][0]
                korner_kod = sirali_kodlar[3][0]
            else:
                sot_kod = sirali_kodlar[3][0]
                korner_kod = sirali_kodlar[2][0]
            
            yeni_mapping = {
                ta_kod: 'TA',
                da_kod: 'DA',
                sot_kod: 'SOT',
                korner_kod: 'Korner',
                gol_kod: 'Gol'
            }
            
            logger.info(f"🔍 Akıllı S-kod tespiti:")
            for kod, anlam in yeni_mapping.items():
                deger = s_kodlari.get(kod, {}).get('toplam', 0)
                logger.info(f"   {kod} = {anlam} (değer: {deger})")
            
            return yeni_mapping
        
        # Varsayılan mapping'i döndür
        return self.s_kod_mapping
    
    def veri_cikart_guvenli(self, ev_v, dep_v):
        """
        🛡️ Güvenli veri çıkarma (Koruma katmanlı)
        
        1. S-kodlarını tespit et
        2. Veri yapısını kontrol et
        3. Gerekirse akıllı tespit yap
        4. Fiziksel hiyerarşiyi doğrula
        5. Veriyi döndür
        """
        self.toplam_kontrol += 1
        
        # 1. S-kodlarını tespit et
        s_kodlari = self.s_kodlari_tespit_et(ev_v, dep_v)
        
        if not s_kodlari:
            logger.error("❌ S-kodları bulunamadı!")
            return None
        
        # 2. Veri yapısını kontrol et
        yapi_ok = self.veri_yapisini_kontrol_et(s_kodlari)
        
        # 3. Mapping'i belirle
        if not yapi_ok:
            logger.warning("⚠️ Veri yapısı standart değil, akıllı tespit yapılıyor...")
            mapping = self.akilli_s_kod_tespiti(s_kodlari)
        else:
            mapping = self.s_kod_mapping
        
        # 4. Ters mapping oluştur (TA -> S3 gibi)
        ters_mapping = {v: k for k, v in mapping.items()}
        
        # 5. Veriyi çıkar
        veri = {
            'ev_sot': guvenli_int(ev_v.get(ters_mapping.get('SOT', 'S1'), 0)),
            'ev_korner': guvenli_int(ev_v.get(ters_mapping.get('Korner', 'S2'), 0)),
            'ev_ta': guvenli_int(ev_v.get(ters_mapping.get('TA', 'S3'), 0)),
            'ev_da': guvenli_int(ev_v.get(ters_mapping.get('DA', 'S4'), 0)),
            'ev_gol': guvenli_int(ev_v.get(ters_mapping.get('Gol', 'SC'), 0)),
            'dep_sot': guvenli_int(dep_v.get(ters_mapping.get('SOT', 'S1'), 0)),
            'dep_korner': guvenli_int(dep_v.get(ters_mapping.get('Korner', 'S2'), 0)),
            'dep_ta': guvenli_int(dep_v.get(ters_mapping.get('TA', 'S3'), 0)),
            'dep_da': guvenli_int(dep_v.get(ters_mapping.get('DA', 'S4'), 0)),
            'dep_gol': guvenli_int(dep_v.get(ters_mapping.get('Gol', 'SC'), 0))
        }
        
        # 6. Fiziksel hiyerarşiyi doğrula
        ta = veri['ev_ta'] + veri['dep_ta']
        da = veri['ev_da'] + veri['dep_da']
        sot = veri['ev_sot'] + veri['dep_sot']
        gol = veri['ev_gol'] + veri['dep_gol']
        
        hiyerarsi_ok, hatalar = self.fiziksel_hiyerarsi_dogrula(ta, da, sot, gol)
        
        if not hiyerarsi_ok:
            self.anomali_sayaci += 1
            logger.warning(f"⚠️ Fiziksel hiyerarşi ihlali tespit edildi:")
            for hata in hatalar:
                logger.warning(f"   {hata}")
            logger.warning(f"📊 Anomali oranı: {self.anomali_sayaci}/{self.toplam_kontrol} ({(self.anomali_sayaci/self.toplam_kontrol)*100:.1f}%)")
        
        # 7. Veriyi döndür (hiyerarşi ihlali olsa bile, üst katman karar verecek)
        veri['hiyerarsi_ok'] = hiyerarsi_ok
        veri['hatalar'] = hatalar
        veri['s_kodlari'] = s_kodlari
        veri['mapping'] = mapping
        
        return veri
    
    def istatistikleri_goster(self):
        """Veri koruma katmanı istatistiklerini gösterir"""
        if self.toplam_kontrol > 0:
            basari_orani = ((self.toplam_kontrol - self.anomali_sayaci) / self.toplam_kontrol) * 100
            logger.info(f"📊 Veri Koruma İstatistikleri:")
            logger.info(f"   Toplam kontrol: {self.toplam_kontrol}")
            logger.info(f"   Anomali: {self.anomali_sayaci}")
            logger.info(f"   Başarı oranı: {basari_orani:.1f}%")

# ============================================================================
# KONFIGÜRASYON
# ============================================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

# 🔧 FIX V4: Gemini yerine Grok API kullan
# Grok API (xAI) - Daha güvenilir ve hızlı
GROK_API_KEY = os.getenv("GROK_API_KEY") or None

# Fallback: Gemini API Keys (3 adet rotasyon)
GEMINI_API_KEY_1 = os.getenv("GEMINI_API_KEY_1") or None
GEMINI_API_KEY_2 = os.getenv("GEMINI_API_KEY_2") or None
GEMINI_API_KEY_3 = os.getenv("GEMINI_API_KEY_3") or None

# 🔧 DEBUG: API key durumunu logla
print(f"🔑 DEBUG - AI API Keys:")
print(f"   Grok: {'✅ SET' if GROK_API_KEY else '❌ YOK'}")
print(f"   Gemini Key 1: {'✅ SET' if GEMINI_API_KEY_1 else '❌ YOK'}")
print(f"   Gemini Key 2: {'✅ SET' if GEMINI_API_KEY_2 else '❌ YOK'}")
print(f"   Gemini Key 3: {'✅ SET' if GEMINI_API_KEY_3 else '❌ YOK'}")

bildirim_gonderilen = deque(maxlen=1000)

# 🛡️ Veri Koruma Katmanı
veri_koruma = VeriKorumaKatmani()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================================================================
# 🎯 NESİNE LİG KONTROLÜ (LİTERATÜR) - V44 UPDATED
# ============================================================================

def nesine_lig_kontrolu(league_name, ev_adi, dep_adi):
    """
    🎯 V44: Lig bazlı Nesine kontrolü
    
    Nesine'de oynanan ligler:
    - Türkiye Süper Lig
    - İngiltere Premier League
    - İspanya La Liga
    - Almanya Bundesliga
    - İtalya Serie A
    - Fransa Ligue 1
    - Hollanda Eredivisie
    - Portekiz Primeira Liga
    - Belçika Pro League
    - İngiltere Championship
    - Almanya 2. Bundesliga
    - İspanya Segunda Division
    - İtalya Serie B
    - Fransa Ligue 2
    
    Returns:
        bool: True = Nesine'de oynanıyor, False = Oynanmıyor
    """
    # Tam metin (lig + takımlar)
    full_text = f"{league_name} {ev_adi} {dep_adi}".lower()
    
    # 1. ÖNCE ELEYİCİ FİLTRELER (U19, Reserves, E-spor, Women)
    # U19, U20, U21, U23 regex ile tespit
    if re.search(r'\bu\d{2}\b', full_text):
        logger.info(f"🚫 Nesine'de yok: U-yaş kategorisi")
        return False
    
    # Reserves
    if re.search(r'\breserve[s]?\b', full_text):
        logger.info(f"🚫 Nesine'de yok: Reserves")
        return False
    
    # E-spor
    if re.search(r'\be[-\s]?sport[s]?\b', full_text):
        logger.info(f"🚫 Nesine'de yok: E-spor")
        return False
    
    # Women/Kadınlar
    if re.search(r'\b(w|women|kadın|kadin)\b', full_text):
        logger.info(f"🚫 Nesine'de yok: Kadınlar ligi")
        return False
    
    # Youth/Junior/Academy
    if re.search(r'\b(youth|junior|academy)\b', full_text):
        logger.info(f"🚫 Nesine'de yok: Genç takım")
        return False
    
    # Virtual/Simulation
    if re.search(r'\b(virtual|simulation|sim)\b', full_text):
        logger.info(f"🚫 Nesine'de yok: Virtual maç")
        return False
    
    # E-spor takım isimleri
    esport_takimlar = [
        'kodak', 'kray', 'og', 'hotshot', 'andrew', 'professor',
        'carlos', 'ken', 'jetli', 'volvo', 'grellz', 'glory',
        'grimace', 'frantsuz', 'nekishka', 'eden', 'boom',
        'force', 'emperor', 'yerema', 'catalyst', 'pimchik',
        'koss', 'fantazer'
    ]
    
    for takim in esport_takimlar:
        if f'({takim})' in full_text:
            logger.info(f"🚫 Nesine'de yok: E-spor takımı '({takim})'")
            return False
    
    # 2. NESİNE LİGLERİ KONTROLÜ (Whitelist)
    nesine_ligler = [
        # Türkiye
        'super lig', 'süper lig', 'turkey', 'türkiye',
        
        # İngiltere
        'premier league', 'championship', 'england', 'efl',
        
        # İspanya
        'la liga', 'spain', 'segunda', 'segunda division',
        
        # Almanya
        'bundesliga', '2. bundesliga', 'germany', 'deutschland',
        
        # İtalya
        'serie a', 'serie b', 'italy', 'italia',
        
        # Fransa
        'ligue 1', 'ligue 2', 'france',
        
        # Hollanda
        'eredivisie', 'netherlands', 'holland', 'dutch',
        
        # Portekiz
        'primeira liga', 'portugal', 'portuguese',
        
        # Belçika
        'pro league', 'belgium', 'belgian',
        
        # Diğer major ligler
        'champions league', 'europa league', 'conference league',
        'scottish premiership', 'scotland',
        'austrian bundesliga', 'austria',
        'swiss super league', 'switzerland',
        'russian premier league', 'russia',
        'ukrainian premier league', 'ukraine',
        'greek super league', 'greece',
        'turkish cup', 'copa del rey', 'fa cup', 'dfb pokal',
        'coppa italia', 'coupe de france'
    ]
    
    # Lig adını kontrol et
    league_lower = league_name.lower()
    for nesine_lig in nesine_ligler:
        if nesine_lig in league_lower:
            logger.info(f"✅ Nesine'de oynanıyor: '{nesine_lig}' tespit edildi")
            return True
    
    # Whitelist'te yoksa Nesine'de yok
    logger.info(f"🚫 Nesine'de yok: Lig whitelist'te değil ('{league_name}')")
    return False

# ============================================================================
# ⭐⭐⭐ KRİTİK: ALTIN PENCERE VE SKOR DURUMU FİLTRELERİ
# ============================================================================

def altin_pencere_kontrol(dakika):
    """
    🎯 LİTERATÜR: Altın Pencereler (Akademik Rapor)
    
    🔧 FIX: Bonuslar azaltıldı (çok yüksekti)
    - 24-36 dk: İlk Yarı Olgunlaşma Evresi (+2.0, eskiden +3.5)
    - 48-58 dk: İkinci Yarı Kırılma Evresi (+3.0, eskiden +5.0)
    """
    if 24 <= dakika <= 36:
        return 2.0, "OLGUNLAŞMA EVRESİ"  # İlk yarı olgunlaşma
    elif 48 <= dakika <= 58:
        return 3.0, "KIRILMA EVRESİ"  # İkinci yarı kırılma
    elif 60 < dakika <= 75:
        return 1.0, "GECIS_OYUNU"
    else:
        return 0.0, "NORMAL"

def skor_durumu_kontrol(ev_gol, dep_gol):
    """
    ⭐⭐⭐ Toplam gol >= 5: Kaos bölgesi (%42.1 doğruluk)
    ⭐⭐⭐ Fark >= 3: Rölanti evresi (coasting phase)
    """
    toplam_gol = ev_gol + dep_gol
    fark = abs(ev_gol - dep_gol)
    
    # Kaos bölgesi kontrolü
    if toplam_gol >= 5:
        logger.warning(f"❌ Kaos bölgesi: Toplam gol {toplam_gol} >= 5")
        return False, "KAOS_BOLGESI"
    
    # Rölanti evresi kontrolü (3-0, 4-1 gibi)
    if fark >= 3:
        logger.warning(f"❌ Rölanti evresi: Fark {fark} >= 3")
        return False, "ROLANTI_EVRESI"
    
    # Optimum skor durumları: 2-1, 1-2, 3-1, 1-3
    if (ev_gol == 2 and dep_gol == 1) or \
       (ev_gol == 1 and dep_gol == 2) or \
       (ev_gol == 3 and dep_gol == 1) or \
       (ev_gol == 1 and dep_gol == 3):
        logger.info(f"✅ Optimum skor durumu: {ev_gol}-{dep_gol}")
        return True, "OPTIMUM"
    
    return True, "NORMAL"

def sot_epilasyon_kontrol(sot):
    """
    ⭐⭐⭐ İsabetli şut > 8: Hücum epilasyonu (attacking exhaustion)
    Literatür: Yüksek SOT = Düşük gol olasılığı
    """
    if sot <= 8:
        return sot * 0.25  # Marjinal fayda azaltılmış
    else:
        logger.warning(f"⚠️ Hücum epilasyonu: SOT {sot} > 8")
        return -1.0  # Ceza puanı

def xg_hesapla(sot, da, ta, korner):
    """
    🎯 xG (Beklenen Gol) Hesaplama (V44 - Kantitatif Model)
    
    Formül: xG = (SOT × 0.15) + (DA × 0.015) + (TA × 0.01) + (Korner × 0.03)
    
    🔧 FIX: DA katsayısı 0.05 → 0.015 (Sahte baskı yanlış tespit düzeltmesi)
    
    Mantık:
    - İsabetli şut en önemli faktör (0.15)
    - Tehlikeli atak düşürüldü (0.015) - Çok yüksek sahte baskı tespit ediyordu
    - Toplam atak düşük ağırlık (0.01)
    - Korner orta ağırlık (0.03)
    """
    # 🛡️ GÜVENLİK KONTROLLERI
    sot = max(0, sot if sot is not None else 0)
    da = max(0, da if da is not None else 0)
    ta = max(0, ta if ta is not None else 0)
    korner = max(0, korner if korner is not None else 0)
    
    # Hesaplama
    xg = (sot * 0.15) + (da * 0.015) + (ta * 0.01) + (korner * 0.03)
    return round(xg, 2)

def da_ivmesi_kontrol(da, dakika):
    """
    🎯 V44: DA İvmesi Kontrolü (Kantitatif Filtre)
    
    🔧 FIX: Eşik 1.5 → 1.0 (Çok sıkı, 98 maçtan 1'i geçiyordu)
    
    Filtre: DA ivmesi ≥ 1.0 DA/dakika
    Altında rölanti → elen
    
    Returns:
        (bool, float): (Geçerli mi?, DA ivmesi)
    """
    if dakika == 0:
        return False, 0.0
    
    da_ivmesi = da / dakika
    
    if da_ivmesi < 1.0:
        logger.warning(f"❌ DA ivmesi düşük: {da_ivmesi:.2f} < 1.0 (rölanti)")
        return False, da_ivmesi
    
    logger.info(f"✅ DA ivmesi yeterli: {da_ivmesi:.2f} ≥ 1.0")
    return True, da_ivmesi

def da_sot_oran_kontrol(da, sot):
    """
    🎯 V44: Sahte Baskı Kontrolü - DA/SOT Oranı (Kantitatif Filtre)
    
    Filtre: DA/SOT > 8 → Sahte baskı → Elen
    
    Returns:
        (bool, float): (Geçerli mi?, DA/SOT oranı)
    """
    if sot == 0:
        logger.warning(f"❌ SOT = 0, oran hesaplanamıyor")
        return False, 0.0
    
    oran = da / sot
    
    if oran > 8:
        logger.warning(f"❌ Sahte baskı: DA/SOT = {oran:.2f} > 8")
        return False, oran
    
    logger.info(f"✅ DA/SOT oranı normal: {oran:.2f} ≤ 8")
    return True, oran

def korner_sot_oran_kontrol(korner, sot):
    """
    🎯 V44: Korner Tuzağı Kontrolü (Kantitatif Filtre)
    
    🔧 FIX: Kural değişti - Literatürdeki kural kullanılıyor
    
    Filtre: Korner ≥ 8 VE SOT < 5 → Tuzak → Elen
    
    Returns:
        (bool, str): (Geçerli mi?, Durum)
    """
    if korner >= 8 and sot < 5:
        logger.warning(f"❌ Korner tuzağı: Korner={korner} ≥ 8 ve SOT={sot} < 5")
        return False, f"KORNER_TUZAGI (Korner={korner}, SOT={sot})"
    
    logger.info(f"✅ Korner oranı normal: Korner={korner}, SOT={sot}")
    return True, "OK"

def sahte_baski_eliminasyonu(ev_xg, dep_xg, ev_gol, dep_gol):
    """
    🎯 Sahte Baskı Eliminasyonu (LİTERATÜR) - FİX EDİLDİ
    
    xG ile gerçek gol arasındaki farkı analiz eder.
    Büyük fark = Sahte baskı veya şanssızlık
    
    Returns:
        (bool, str): (Geçerli mi?, Durum mesajı)
        - True, "YOK": Sahte baskı yok
        - False, "EV_SAHTE_BASKI": Ev sahibi sahte baskı
        - False, "DEP_SAHTE_BASKI": Deplasman sahte baskı
    """
    ev_fark = abs(ev_xg - ev_gol)
    dep_fark = abs(dep_xg - dep_gol)
    
    # Eşik: 1.5 xG farkı
    if ev_fark > 1.5:
        logger.warning(f"⚠️ Ev sahibi sahte baskı: xG={ev_xg}, Gol={ev_gol}, Fark={ev_fark:.2f}")
        return False, "EV_SAHTE_BASKI"
    
    if dep_fark > 1.5:
        logger.warning(f"⚠️ Deplasman sahte baskı: xG={dep_xg}, Gol={dep_gol}, Fark={dep_fark:.2f}")
        return False, "DEP_SAHTE_BASKI"
    
    logger.info(f"✅ Sahte baskı yok: Ev xG={ev_xg}, Dep xG={dep_xg}")
    return True, "YOK"

# ============================================================================
# ⭐⭐⭐ YENİ KRİTİK ÖZELLİKLER (FAZ 1)
# ============================================================================

def oyun_durumu_normalizasyonu(ev_gol, dep_gol, ev_da, dep_da, ev_sot, dep_sot, dakika):
    """
    🎯 Oyun Durumu Normalizasyonu
    Skor farkına göre istatistikleri normalize eder
    """
    skor_farki = ev_gol - dep_gol
    if skor_farki >= 2:
        return ev_da * 0.7, ev_sot * 0.7, dep_da, dep_sot
    elif skor_farki <= -2:
        return ev_da, ev_sot, dep_da * 0.7, dep_sot * 0.7
    elif dakika >= 80 and abs(skor_farki) >= 1:
        if skor_farki > 0:
            return ev_da * 0.5, ev_sot * 0.5, dep_da, dep_sot
        else:
            return ev_da, ev_sot, dep_da * 0.5, dep_sot * 0.5
    return ev_da, ev_sot, dep_da, dep_sot

def korner_tuzagi_kontrolu(ev_korner, dep_korner, ev_sot, dep_sot):
    """
    🎯 Korner Tuzağı Kontrolü
    Korner sayısı yüksek ama şut düşükse sahte baskı var demektir
    """
    toplam_korner = ev_korner + dep_korner
    toplam_sot = ev_sot + dep_sot
    if toplam_korner > 0 and toplam_sot > 0:
        if (toplam_korner / toplam_sot) > 1.5:
            logger.warning(f"⚠️ Korner tuzağı: Korner/SOT oranı yüksek ({toplam_korner}/{toplam_sot})")
            return False
    if ev_korner >= 8 and ev_sot < 5:
        logger.warning(f"⚠️ Ev sahibi korner tuzağı: {ev_korner} korner, {ev_sot} şut")
        return False
    if dep_korner >= 8 and dep_sot < 5:
        logger.warning(f"⚠️ Deplasman korner tuzağı: {dep_korner} korner, {dep_sot} şut")
        return False
    return True

class CokKatmanliDogrulama:
    """
    🎯 Çok Katmanlı Doğrulama Sistemi (LİTERATÜR)
    
    Bayraklar:
    - VU (Veri Uygunluğu): Kritik filtrelerden geçti mi?
    - VA (Veri Anomalisi): Normalizasyon farkı yüksek mi?
    - USA (Uzun Süreli Anomali): 80+ dakika + anomali?
    - MA (Master Algoritma): Ekstrem koşul şalteri
    
    Başarı Kuralı: VA ve USA her ikisi 0 veya her ikisi 1 olmalı
    """
    def __init__(self):
        self.VU = 0   # Veri Uygunluğu
        self.VA = 0   # Veri Anomalisi
        self.USA = 0  # Uzun Süreli Anomali
        self.MA = 0   # Master Algoritma
    
    def sinyal_uret(self):
        """
        LİTERATÜR: Sinyal üretim mantığı
        
        Başarı Kuralı: VA ve USA her ikisi 0 veya her ikisi 1 olmalı
        - VA=0, USA=0: İdeal durum ✅
        - VA=1, USA=1: Kabul edilebilir ✅
        - VA=0, USA=1: Reddedilir ❌
        - VA=1, USA=0: Reddedilir ❌
        """
        # Master Algoritma kontrolü (ekstrem koşul)
        if self.MA == 1:
            logger.warning("❌ Master Algoritma aktif: Ekstrem koşul tespit edildi")
            return False
        
        # VU kontrolü (temel gereksinim)
        if self.VU == 0:
            logger.warning("❌ Veri Uygunluğu başarısız")
            return False
        
        # VA ve USA senkronizasyon kontrolü
        if (self.VA == 0 and self.USA == 0) or (self.VA == 1 and self.USA == 1):
            logger.info(f"✅ Doğrulama başarılı: VU={self.VU}, VA={self.VA}, USA={self.USA}, MA={self.MA}")
            return True
        
        logger.warning(f"❌ Doğrulama başarısız: VA={self.VA}, USA={self.USA} senkronize değil")
        return False

# ============================================================================
# 🔧 FIX V4: GROK AI ENTEGRASYONu (Gemini yerine)
# ============================================================================

class GrokAIAnalyzer:
    """
    Grok AI (xAI) - Elon Musk'ın AI'ı
    Daha hızlı ve güvenilir
    """
    def __init__(self):
        self.api_key = GROK_API_KEY
        self.api_call_count = 0
        
        if self.api_key:
            logger.info(f"✅ Grok API key yüklendi")
        else:
            logger.warning("⚠️ Grok API key yok, AI analizi devre dışı")
    
    async def analiz_yap(self, mac_verisi, session):
        if not self.api_key:
            logger.warning("❌ Grok AI: API key yok, analiz yapılamıyor!")
            logger.warning(f"   GROK_API_KEY environment variable set edilmemiş")
            return None
        
        try:
            self.api_call_count += 1
            
            logger.info(f"🤖 Grok AI: API çağrısı başlıyor (#{self.api_call_count})")
            logger.info(f"   Maç: {mac_verisi['ev_adi']} vs {mac_verisi['dep_adi']}")
            
            prompt = f"""Sen deneyimli bir futbol analisti ve bahis uzmanısın.
İstatistiklerin ÖTESİNDE, sezgisel ve bağlamsal analiz yap.

MAÇ: {mac_verisi['ev_adi']} {mac_verisi['skor']} {mac_verisi['dep_adi']} ({mac_verisi['dakika']}')

İSTATİSTİKLER:
• Toplam Atak: {mac_verisi['ta']} (Ev:{mac_verisi['ev_ta']}, Dep:{mac_verisi['dep_ta']})
• Tehlikeli Atak: {mac_verisi['da']} (Ev:{mac_verisi['ev_da']}, Dep:{mac_verisi['dep_da']})
• İsabetli Şut: {mac_verisi['sot']} (Ev:{mac_verisi['ev_sot']}, Dep:{mac_verisi['dep_sot']})
• Gol: {mac_verisi['gol']} (Ev:{mac_verisi['ev_gol']}, Dep:{mac_verisi['dep_gol']})

SEZGİSEL ANALİZ (MAX 400 karakter):

1. **Skor Psikolojisi**: Bu skorda takımlar nasıl düşünür?
2. **GRİ ALAN**: İstatistikler aldatıcı mı? Sahte baskı var mı?
3. **KONTRA ATAK RİSKİ**: Hangi takım tehlikeli?
4. **SONUÇ**: Veriler ne derse desin, senin sezgin ne diyor?

⚠️ ÖNEMLİ: Sadece verileri tekrar etme!
"Ama" diyebilmelisin. Kontra atak riskini gör.
Ev sahibi baskılı ama deplasman 1 pozisyonda gol atabilir diyebilmelisin.

DOĞAL dille yaz. İnsan gibi düşün, makine gibi değil."""
            
            # Grok API endpoint
            url = "https://api.x.ai/v1/chat/completions"
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "grok-beta",
                "messages": [
                    {
                        "role": "system",
                        "content": "Sen deneyimli bir futbol analisti ve bahis uzmanısın. Sezgisel ve bağlamsal analiz yaparsın."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.9,
                "max_tokens": 500
            }
            
            logger.info(f"🤖 Grok AI: POST isteği gönderiliyor (URL: {url})")
            logger.info(f"   Timeout: 15 saniye")
            
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
                logger.info(f"🤖 Grok AI: Response alındı - Status: {response.status}")
                
                if response.status != 200:
                    response_text = await response.text()
                    logger.error(f"❌ Grok API HATASI: HTTP {response.status}")
                    logger.error(f"   Response body: {response_text[:500]}")
                    logger.error(f"   🔍 Olası nedenler:")
                    logger.error(f"      - API key geçersiz veya süresi dolmuş")
                    logger.error(f"      - API limiti aşıldı")
                    logger.error(f"      - Grok API servisi down")
                    return None
                
                data = await response.json()
                logger.info(f"✅ Grok AI: JSON parse başarılı")
                logger.info(f"   Response keys: {list(data.keys())}")
                
                if 'choices' in data and len(data['choices']) > 0:
                    text = data['choices'][0]['message']['content']
                    logger.info(f"✅✅✅ GROK AI YANITI ALINDI! ✅✅✅")
                    logger.info(f"   Karakter sayısı: {len(text)}")
                    logger.info(f"   İlk 150 karakter: {text[:150]}")
                    logger.info(f"   Son 50 karakter: {text[-50:]}")
                    return text
                else:
                    logger.error(f"❌ Grok AI: 'choices' yok veya boş!")
                    logger.error(f"   Full response: {str(data)[:500]}")
                    logger.error(f"   🔍 API yanıt verdi ama içerik yok")
                
                return None
                
        except asyncio.TimeoutError:
            logger.error(f"❌ Grok AI TIMEOUT: 15 saniye içinde yanıt gelmedi")
            logger.error(f"   🔍 Grok API yavaş veya erişilemiyor")
            return None
        except Exception as e:
            logger.error(f"❌ Grok AI EXCEPTION: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(f"   Traceback: {traceback.format_exc()[:500]}")
            return None

# Fallback: Gemini AI Analyzer
class GeminiAIAnalyzer:
    def __init__(self):
        self.api_keys = [k for k in [GEMINI_API_KEY_1, GEMINI_API_KEY_2, GEMINI_API_KEY_3] if k]
        self.current_key_index = 0
        self.api_call_count = 0
        
        if self.api_keys:
            logger.info(f"✅ {len(self.api_keys)} Gemini API key yüklendi (fallback)")
        else:
            logger.warning("⚠️ Gemini API key yok")
    
    def _get_next_api_key(self):
        if not self.api_keys:
            return None
        key = self.api_keys[self.current_key_index]
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        return key
    
    async def analiz_yap(self, mac_verisi, session):
        if not self.api_keys:
            logger.warning("❌ Gemini AI: API key yok, analiz yapılamıyor!")
            logger.warning(f"   GEMINI_API_KEY_1/2/3 environment variable'ları set edilmemiş")
            return None
        
        try:
            api_key = self._get_next_api_key()
            self.api_call_count += 1
            
            logger.info(f"🤖 Gemini AI: API çağrısı başlıyor (#{self.api_call_count})")
            logger.info(f"   Maç: {mac_verisi['ev_adi']} vs {mac_verisi['dep_adi']}")
            logger.info(f"   Kullanılan key index: {self.current_key_index}")
            
            prompt = f"""Sen deneyimli bir futbol analisti ve bahis uzmanısın.
İstatistiklerin ÖTESİNDE, sezgisel ve bağlamsal analiz yap.

MAÇ: {mac_verisi['ev_adi']} {mac_verisi['skor']} {mac_verisi['dep_adi']} ({mac_verisi['dakika']}')

İSTATİSTİKLER:
• Toplam Atak: {mac_verisi['ta']} (Ev:{mac_verisi['ev_ta']}, Dep:{mac_verisi['dep_ta']})
• Tehlikeli Atak: {mac_verisi['da']} (Ev:{mac_verisi['ev_da']}, Dep:{mac_verisi['dep_da']})
• İsabetli Şut: {mac_verisi['sot']} (Ev:{mac_verisi['ev_sot']}, Dep:{mac_verisi['dep_sot']})
• Gol: {mac_verisi['gol']} (Ev:{mac_verisi['ev_gol']}, Dep:{mac_verisi['dep_gol']})

SEZGİSEL ANALİZ (MAX 400 karakter):

1. **Skor Psikolojisi**: Bu skorda takımlar nasıl düşünür?
2. **GRİ ALAN**: İstatistikler aldatıcı mı? Sahte baskı var mı?
3. **KONTRA ATAK RİSKİ**: Hangi takım tehlikeli?
4. **SONUÇ**: Veriler ne derse desin, senin sezgin ne diyor?

DOĞAL dille yaz. İnsan gibi düşün, makine gibi değil."""
            
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={api_key}"
            
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.9,
                    "maxOutputTokens": 500
                }
            }
            
            logger.info(f"🤖 Gemini AI: POST isteği gönderiliyor")
            
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as response:
                logger.info(f"🤖 Gemini AI: Response alındı - Status: {response.status}")
                
                if response.status != 200:
                    response_text = await response.text()
                    logger.error(f"❌ Gemini API HATASI: HTTP {response.status}")
                    logger.error(f"   Response body: {response_text[:500]}")
                    return None
                
                data = await response.json()
                logger.info(f"✅ Gemini AI: JSON parse başarılı")
                
                if 'candidates' in data and len(data['candidates']) > 0:
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    logger.info(f"✅✅✅ GEMINI AI YANITI ALINDI! ✅✅✅")
                    logger.info(f"   Karakter sayısı: {len(text)}")
                    logger.info(f"   İlk 150 karakter: {text[:150]}")
                    return text
                else:
                    logger.error(f"❌ Gemini AI: 'candidates' yok veya boş!")
                    logger.error(f"   Full response: {str(data)[:500]}")
                
                return None
                
        except asyncio.TimeoutError:
            logger.error(f"❌ Gemini AI TIMEOUT: 15 saniye içinde yanıt gelmedi")
            return None
        except Exception as e:
            logger.error(f"❌ Gemini AI EXCEPTION: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(f"   Traceback: {traceback.format_exc()[:500]}")
            return None

# 🔧 FIX V4: Önce Grok, sonra Gemini fallback
grok_ai = GrokAIAnalyzer()
gemini_ai = GeminiAIAnalyzer()

async def ai_analiz_yap(mac_verisi, session):
    """
    AI analizi - Önce Grok, başarısız olursa Gemini
    """
    logger.info("=" * 60)
    logger.info("🤖 AI ANALİZ BAŞLIYOR")
    logger.info("=" * 60)
    
    # Önce Grok dene
    logger.info("🔍 1. Grok AI deneniyor...")
    if grok_ai.api_key:
        logger.info("   ✅ Grok API key mevcut, çağrı yapılıyor...")
        result = await grok_ai.analiz_yap(mac_verisi, session)
        if result:
            logger.info("   ✅✅✅ GROK AI BAŞARILI! Yanıt döndürülüyor")
            return result, "Grok"
        else:
            logger.warning("   ❌ Grok AI başarısız, Gemini'ye geçiliyor...")
    else:
        logger.warning("   ❌ Grok API key yok, Gemini'ye geçiliyor...")
    
    # Grok başarısız, Gemini dene
    logger.info("🔍 2. Gemini AI deneniyor (fallback)...")
    if gemini_ai.api_keys:
        logger.info(f"   ✅ Gemini API key mevcut ({len(gemini_ai.api_keys)} adet), çağrı yapılıyor...")
        result = await gemini_ai.analiz_yap(mac_verisi, session)
        if result:
            logger.info("   ✅✅✅ GEMINI AI BAŞARILI! Yanıt döndürülüyor")
            return result, "Gemini"
        else:
            logger.error("   ❌ Gemini AI de başarısız!")
    else:
        logger.error("   ❌ Gemini API key yok!")
    
    logger.error("=" * 60)
    logger.error("❌❌❌ TÜM AI SERVİSLERİ BAŞARISIZ!")
    logger.error("=" * 60)
    logger.error("🔍 Olası nedenler:")
    logger.error("   1. API key'ler Railway'e eklenmemiş")
    logger.error("   2. API key'ler geçersiz")
    logger.error("   3. API servisleri down")
    logger.error("   4. Network hatası")
    logger.error("=" * 60)
    
    return None, None

# ============================================================================
# 🎯 ASIAN HANDICAP ENTEGRASYONu (LİTERATÜR)
# ============================================================================

async def asian_handicap_cek(event_id, session):
    """
    🎯 Asian Handicap Çek (LİTERATÜR) - FİX EDİLDİ V3
    
    /event/odds endpoint'inden direkt Asian Handicap çeker
    - Buçuklu handikaplar: -0.5, +1.5
    - Çeyrekli handikaplar: -0.25, +0.75
    - Canlı handikap çizgisi takibi
    
    Returns:
        dict: {'ev_handicap': -0.5, 'dep_handicap': +0.5, 'ev_oran': 1.85, 'dep_oran': 2.05}
        None: Hata durumunda
    """
    try:
        logger.info(f"🐛 Asian Handicap: API çağrısı başlıyor (event_id: {event_id})")
        
        # 🔧 FIX V3: /event/odds endpoint kullan
        async with session.get(
            f"https://api.betsapi.com/v1/event/odds?token={BETSAPI_TOKEN}&event_id={event_id}",
            timeout=aiohttp.ClientTimeout(total=10)
        ) as response:
            
            logger.info(f"🐛 Asian Handicap: Response status: {response.status}")
            
            # Response validation
            if response.status != 200:
                response_text = await response.text()
                logger.warning(f"⚠️ Asian Handicap API hatası: HTTP {response.status}")
                logger.warning(f"   Response: {response_text[:200]}")
                return None
            
            # JSON parse validation
            try:
                data = await response.json()
                logger.info(f"🐛 Asian Handicap: JSON parse başarılı")
            except Exception as e:
                logger.error(f"❌ Asian Handicap JSON parse hatası: {str(e)}")
                return None
            
            # 🔧 FIX V3: FULL RESPONSE LOGLAMA
            logger.info(f"🐛 Asian Handicap: FULL API RESPONSE:")
            logger.info(f"   success: {data.get('success')}")
            logger.info(f"   pager: {data.get('pager')}")
            
            # Success kontrolü
            if data.get('success') != 1:
                logger.warning(f"⚠️ Asian Handicap API success=0")
                logger.warning(f"   Data keys: {list(data.keys())}")
                return None
            
            # Results validation
            results = data.get('results', {})
            logger.info(f"🐛 Asian Handicap: results type = {type(results)}")
            
            if not isinstance(results, dict):
                logger.warning(f"⚠️ Asian Handicap results geçersiz (dict değil)")
                return None
            
            # 🔧 FIX V3: TÜM KEYS'İ LOGLA
            all_keys = list(results.keys())
            logger.info(f"🐛 Asian Handicap: TÜM KEYS ({len(all_keys)} adet):")
            for i, key in enumerate(all_keys[:30]):  # İlk 30 key
                logger.info(f"   [{i}] {key}")
            
            if len(all_keys) > 30:
                logger.info(f"   ... ve {len(all_keys) - 30} key daha")
            
            # 🔧 FIX V4: Gerçek API response yapısına göre parse
            # API Response: results['1_2'] = [{'home_od': '1.675', 'handicap': '0', 'away_od': '2.150', ...}, ...]
            
            asian_handicap_data = None
            asian_handicap_key = None
            
            # 1. Önce '1_2' key'ini dene (Asian Handicap)
            if '1_2' in results:
                asian_handicap_data = results['1_2']
                asian_handicap_key = '1_2'
                logger.info(f"✅ Asian Handicap bulundu: key='1_2'")
            
            # 2. Bulunamadıysa, diğer olası key'leri dene
            if not asian_handicap_data:
                possible_keys = ['asian_handicap', 'ah', 'handicap', 'asian_lines', 'AsianHandicap']
                for key in possible_keys:
                    if key in results:
                        asian_handicap_data = results[key]
                        asian_handicap_key = key
                        logger.info(f"✅ Asian Handicap bulundu: key='{key}'")
                        break
            
            # 3. Hala bulunamadıysa, key isimlerinde 'asian' veya 'handicap' içeren ara
            if not asian_handicap_data:
                for key in results.keys():
                    key_lower = str(key).lower()
                    if 'asian' in key_lower or 'handicap' in key_lower or 'ah' in key_lower:
                        asian_handicap_data = results[key]
                        asian_handicap_key = key
                        logger.info(f"✅ Asian Handicap bulundu (arama ile): key='{key}'")
                        break
            
            if not asian_handicap_data:
                logger.warning(f"⚠️ Asian Handicap bulunamadı")
                logger.warning(f"   Mevcut keys: {list(results.keys())[:20]}")
                return None
            
            logger.info(f"🐛 Asian Handicap data type: {type(asian_handicap_data)}")
            logger.info(f"🐛 Asian Handicap data içeriği (ilk 300 karakter): {str(asian_handicap_data)[:300]}")
            
            # 🔧 FIX V4: Liste formatını parse et
            ev_handicap = 0.0
            dep_handicap = 0.0
            ev_oran = 0.0
            dep_oran = 0.0
            
            # Gerçek API yapısı: Liste formatı
            if isinstance(asian_handicap_data, list) and len(asian_handicap_data) > 0:
                logger.info(f"✅ Liste formatı tespit edildi, {len(asian_handicap_data)} odds var")
                
                # İlk eleman en güncel odds
                latest_odds = asian_handicap_data[0]
                
                if isinstance(latest_odds, dict):
                    logger.info(f"   En güncel odds: {latest_odds}")
                    
                    # Field'ları çıkar
                    handicap_value = guvenli_float(latest_odds.get('handicap', 0))
                    home_odds = guvenli_float(latest_odds.get('home_od', 0))
                    away_odds = guvenli_float(latest_odds.get('away_od', 0))
                    
                    # Ev sahibi handikap değeri
                    ev_handicap = handicap_value
                    ev_oran = home_odds
                    
                    # Deplasman handikap değeri (ters işaret)
                    dep_handicap = -handicap_value if handicap_value != 0 else 0
                    dep_oran = away_odds
                    
                    logger.info(f"   Parse sonucu:")
                    logger.info(f"      Ev: handicap={ev_handicap}, oran={ev_oran}")
                    logger.info(f"      Dep: handicap={dep_handicap}, oran={dep_oran}")
                else:
                    logger.warning(f"⚠️ Liste elemanı dict değil: {type(latest_odds)}")
                    return None
            
            # Fallback: Dict formatı (eski API yapısı)
            elif isinstance(asian_handicap_data, dict):
                logger.info(f"⚠️ Dict formatı tespit edildi (eski yapı)")
                dict_keys = list(asian_handicap_data.keys())
                logger.info(f"   Dict keys: {dict_keys[:10]}")
                
                # Yapı: {'home': {...}, 'away': {...}}
                if 'home' in asian_handicap_data and 'away' in asian_handicap_data:
                    home_data = asian_handicap_data['home']
                    away_data = asian_handicap_data['away']
                    
                    ev_handicap = guvenli_float(home_data.get('handicap', 0))
                    ev_oran = guvenli_float(home_data.get('odds', home_data.get('odd', 0)))
                    dep_handicap = guvenli_float(away_data.get('handicap', 0))
                    dep_oran = guvenli_float(away_data.get('odds', away_data.get('odd', 0)))
                else:
                    logger.warning(f"⚠️ Bilinmeyen dict yapısı")
                    return None
            
            else:
                logger.warning(f"⚠️ Bilinmeyen data tipi: {type(asian_handicap_data)}")
                return None
            
            logger.info(f"🐛 Asian Handicap: PARSE SONUÇLARI:")
            logger.info(f"   ev_handicap: {ev_handicap} (type: {type(ev_handicap)})")
            logger.info(f"   ev_oran: {ev_oran} (type: {type(ev_oran)})")
            logger.info(f"   dep_handicap: {dep_handicap} (type: {type(dep_handicap)})")
            logger.info(f"   dep_oran: {dep_oran} (type: {type(dep_oran)})")
            
            # Geçerlilik kontrolü
            if ev_oran > 0 and dep_oran > 0:
                logger.info(f"✅ Asian Handicap başarılı: Ev {ev_handicap} ({ev_oran}), Dep {dep_handicap} ({dep_oran})")
                
                return {
                    'ev_handicap': ev_handicap,
                    'dep_handicap': dep_handicap,
                    'ev_oran': ev_oran,
                    'dep_oran': dep_oran
                }
            else:
                logger.warning(f"⚠️ Asian Handicap oranları geçersiz (0 veya negatif)")
                logger.warning(f"   Muhtemel neden: API'den odds verisi gelmiyor veya yanlış field parse ediliyor")
                return None
            
    except asyncio.TimeoutError:
        logger.warning(f"⏱️ Asian Handicap API timeout (event_id: {event_id})")
        return None
    except Exception as e:
        logger.error(f"❌ Asian Handicap hatası: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None

def api_response_validate(response_data, required_fields):
    """
    🛡️ API Response Validation (LİTERATÜR)
    
    Tüm API çağrılarında kullanılacak doğrulama katmanı
    
    Args:
        response_data: API'den gelen data
        required_fields: Zorunlu alanlar listesi
    
    Returns:
        (bool, str): (Geçerli mi?, Hata mesajı)
    """
    if not response_data:
        return False, "Response data boş"
    
    if not isinstance(response_data, dict):
        return False, "Response data dict değil"
    
    for field in required_fields:
        if field not in response_data:
            return False, f"Zorunlu alan eksik: {field}"
    
    return True, "OK"

# ============================================================================
# YARDIMCI FONKSİYONLAR
# ============================================================================

def esnek_liste_duzelt(veri):
    duz = []
    if isinstance(veri, list):
        for e in veri: 
            duz.extend(esnek_liste_duzelt(e))
    elif isinstance(veri, dict): 
        duz.append(veri)
    return duz

def veri_cikart(ev_v, dep_v):
    """
    🛡️ Veri çıkarma (Koruma katmanlı)
    
    Veri koruma katmanını kullanarak güvenli veri çıkarımı yapar.
    Fallback olarak eski yöntemi kullanır.
    """
    sonuc = veri_koruma.veri_cikart_guvenli(ev_v, dep_v)
    
    if sonuc is None:
        # Fallback: Eski yöntem
        logger.warning("⚠️ Veri koruma katmanı başarısız, fallback kullanılıyor")
        return {
            'ev_sot': guvenli_int(ev_v.get('S1', 0)),
            'ev_korner': guvenli_int(ev_v.get('S2', 0)),
            'ev_ta': guvenli_int(ev_v.get('S3', 0)),
            'ev_da': guvenli_int(ev_v.get('S4', 0)),
            'ev_gol': guvenli_int(ev_v.get('SC', 0)),
            'dep_sot': guvenli_int(dep_v.get('S1', 0)),
            'dep_korner': guvenli_int(dep_v.get('S2', 0)),
            'dep_ta': guvenli_int(dep_v.get('S3', 0)),
            'dep_da': guvenli_int(dep_v.get('S4', 0)),
            'dep_gol': guvenli_int(dep_v.get('SC', 0))
        }
    
    return sonuc

# ============================================================================
# ⭐⭐⭐ ANA ANALİZ MOTORU (Literatür Bazlı)
# ============================================================================

async def mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot, session, event_id=None, league_name=""):
    """
    🎯 DISPATCHER MİMARİSİ (V44 - REFACTORED + GLOBAL FİLTRELER)
    
    Yeni Yapı:
    1. Veriyi TeamStats objelerine dönüştür
    2. Temel veri kalitesi kontrolü (MatchDataProtection)
    3. Global filtreler (Lig + Rölanti)
    4. Dakikaya göre modül seç ve çağır (DISPATCHER)
    5. Sinyal kontrolü ve puan barajı
    6. Mesaj oluşturma (AI analizi ile)
    
    Modüller:
    - IYGolModule: 15-40 dk, İlk yarı gol sinyali
    - IY2Module: 46-65 ve 76-90 dk, İkinci yarı sinyali
    - EvDepGolModule: 20-80 dk, Ev/Dep gol sinyali (AH gerekli)
    
    Global Filtreler:
    - Lig Filtresi: Whitelist/Blacklist kontrolü
    - Rölanti Filtresi: Skor farkı >= 3 ise elenir
    """
    try:
        logger.info(f"🔍 Analiz: {ev_adi} vs {dep_adi} - {dk}'")
        
        # ----------------------------------------------------------------
        # 1. VERİ ÇIKARMA VE TEAMSTATS OLUŞTURMA
        # ----------------------------------------------------------------
        v = veri_cikart(ev_v, dep_v)
        
        # TeamStats objelerine dönüştür
        home_stats = TeamStats(
            ta=v['ev_ta'],
            da=v['ev_da'],
            sot=v['ev_sot'],
            gol=v['ev_gol'],
            korner=v.get('ev_korner', 0)
        )
        
        away_stats = TeamStats(
            ta=v['dep_ta'],
            da=v['dep_da'],
            sot=v['dep_sot'],
            gol=v['dep_gol'],
            korner=v.get('dep_korner', 0)
        )
        
        # Toplam değerler
        sot = home_stats.sot + away_stats.sot
        ta = home_stats.ta + away_stats.ta
        da = home_stats.da + away_stats.da
        ev_gol = home_stats.gol
        dep_gol = away_stats.gol
        toplam_gol = ev_gol + dep_gol
        
        logger.info(f"📊 TA:{ta}, DA:{da}, SOT:{sot}, Gol:{toplam_gol}")
        
        # ----------------------------------------------------------------
        # 2. TEMEL VERİ KALİTESİ KONTROLÜ
        # ----------------------------------------------------------------
        logger.info(f"🛡️ Veri kalitesi kontrolü...")
        
        # Veri doğrulama
        data_valid, errors = MatchDataProtection.validate_match_data(home_stats, away_stats)
        if not data_valid:
            logger.warning(f"❌ Veri kalitesi kontrolü başarısız:")
            for error in errors:
                logger.warning(f"   - {error}")
            return None
        
        logger.info(f"✅ Veri kalitesi kontrolü başarılı")
        
        # ----------------------------------------------------------------
        # 3. GLOBAL FİLTRELER (Dispatcher'dan önce)
        # ----------------------------------------------------------------
        logger.info(f"🔍 Global filtreler kontrol ediliyor...")
        
        # 3.1. LİG FİLTRESİ
        lig_uygun, lig_sebep = LeagueFilter.check_league(league_name, ev_adi, dep_adi)
        if not lig_uygun:
            logger.warning(f"❌ ELENDİ: Lig Filtresi - {lig_sebep}")
            return None
        logger.info(f"✅ Lig filtresi geçti")
        
        # 3.2. RÖLANTİ FİLTRESİ (Skor farkı >= 3)
        if abs(ev_gol - dep_gol) >= 3:
            logger.warning(f"❌ ELENDİ: Rölanti Evresi (Skor farkı: {abs(ev_gol - dep_gol)} >= 3)")
            return None
        logger.info(f"✅ Rölanti filtresi geçti (Skor farkı: {abs(ev_gol - dep_gol)})")
        
        # ----------------------------------------------------------------
        # 4. DISPATCHER: DAKİKAYA GÖRE MODÜL SEÇ VE ÇAĞIR
        # ----------------------------------------------------------------
        logger.info(f"🎯 DISPATCHER: Dakika {dk}' için modül seçiliyor...")
        
        sinyal = None
        
        # İlk Yarı Gol Modülü (15-40 dk)
        if 15 <= dk <= 40:
            logger.info(f"   → IYGolModule çağrılıyor...")
            sinyal = IYGolModule.check(dk, ev_gol, dep_gol, home_stats, away_stats)
        
        # İkinci Yarı Modülü (46-65 ve 76-90 dk)
        elif (46 <= dk <= 65) or (76 <= dk <= 90):
            logger.info(f"   → IY2Module çağrılıyor...")
            sinyal = IY2Module.check(dk, home_stats, away_stats, ev_gol, dep_gol)
        
        # Ev/Dep Gol Modülü (20-80 dk, AH gerekli)
        elif 20 <= dk <= 80:
            logger.info(f"   → EvDepGolModule çağrılıyor (AH gerekli)...")
            
            # Asian Handicap çek
            if event_id:
                ah_data = await asian_handicap_cek(event_id, session)
                
                if ah_data:
                    ah_home = ah_data['ev_handicap']
                    ah_away = ah_data['dep_handicap']
                    logger.info(f"   ✅ AH alındı: Ev={ah_home}, Dep={ah_away}")
                    
                    sinyal = EvDepGolModule.check(dk, home_stats, away_stats, ah_home, ah_away)
                else:
                    logger.warning(f"   ⚠️ AH alınamadı, EvDepGolModule atlanıyor")
            else:
                logger.warning(f"   ⚠️ event_id yok, EvDepGolModule atlanıyor")
        
        else:
            logger.info(f"   ⚠️ Dakika {dk}' hiçbir modül aralığında değil")
            return None
        
        # ----------------------------------------------------------------
        # 5. SİNYAL KONTROLÜ VE PUAN BARAJI
        # ----------------------------------------------------------------
        if not sinyal or not sinyal.valid:
            if sinyal:
                logger.warning(f"❌ Sinyal geçersiz: {sinyal.reason}")
            else:
                logger.warning(f"❌ Sinyal oluşturulamadı")
            return None
        
        logger.info(f"✅ Sinyal geçerli: {sinyal.signal_type.value}")
        logger.info(f"   Puan: {sinyal.score:.1f}")
        logger.info(f"   Sebep: {sinyal.reason}")
        logger.info(f"   Detaylar: {sinyal.details}")
        
        # Puan barajı kontrolü (6.5 veya 7.0)
        PUAN_BARAJI = 6.5  # 🔧 FIX: 9.0 → 6.5 (Çok yüksekti, hiç maç geçmiyordu)
        
        if sinyal.score < PUAN_BARAJI:
            logger.warning(f"❌ Puan yetersiz: {sinyal.score:.1f} < {PUAN_BARAJI}")
            return None
        
        logger.info(f"🎉 SİNYAL BARAJDAN GEÇTİ! Puan: {sinyal.score:.1f} >= {PUAN_BARAJI}")
        
        # ----------------------------------------------------------------
        # 6. MESAJ OLUŞTURMA (AI Analizi ile)
        # ----------------------------------------------------------------
        logger.info(f"📝 Mesaj oluşturuluyor...")
        
        # AI analizi için maç verisi hazırla
        mac_verisi = {
            'ev_adi': ev_adi,
            'dep_adi': dep_adi,
            'skor': skor,
            'dakika': dk,
            'ta': ta,
            'da': da,
            'sot': sot,
            'gol': toplam_gol,
            'ev_ta': v['ev_ta'],
            'dep_ta': v['dep_ta'],
            'ev_da': v['ev_da'],
            'dep_da': v['dep_da'],
            'ev_sot': v['ev_sot'],
            'dep_sot': v['dep_sot'],
            'ev_gol': ev_gol,
            'dep_gol': dep_gol
        }
        
        # AI analizi çağır
        logger.info("🤖 AI analizi isteniyor...")
        ai_analiz, ai_source = await ai_analiz_yap(mac_verisi, session)
        
        if ai_analiz:
            logger.info(f"✅ AI analizi alındı ({ai_source})")
        else:
            logger.warning(f"⚠️ AI analizi alınamadı")
        
        # Mesaj oluştur
        mesaj = (
            f"💎 **SİNYAL (Puan: {sinyal.score:.1f})**\n"
            f"⚽ {ev_adi} {skor} {dep_adi}\n"
            f"⏱ Dakika: {dk}' | Sinyal: {sinyal.signal_type.value}\n"
            f"{'='*30}\n"
            f"📊 **İstatistikler:**\n"
            f"• Toplam Atak: {ta} (Ev:{v['ev_ta']}, Dep:{v['dep_ta']})\n"
            f"• Tehlikeli Atak: {da} (Ev:{v['ev_da']}, Dep:{v['dep_da']})\n"
            f"• İsabetli Şut: {sot} (Ev:{v['ev_sot']}, Dep:{v['dep_sot']})\n"
            f"• Gol: {toplam_gol} (Ev:{ev_gol}, Dep:{dep_gol})\n"
            f"{'='*30}\n"
            f"🎯 **Sinyal Detayları:**\n"
            f"• Sebep: {sinyal.reason}\n"
        )
        
        # Detayları ekle
        if sinyal.details:
            for key, value in sinyal.details.items():
                mesaj += f"• {key}: {value}\n"
        
        # AI analizi ekle
        if ai_analiz:
            mesaj += f"{'='*30}\n"
            mesaj += f"🤖 **{ai_source} AI Analizi:**\n{ai_analiz}\n"
        
        # Nesine lig kontrolü
        mesaj += f"{'='*30}\n"
        if nesine_lig_kontrolu(league_name, ev_adi, dep_adi):
            mesaj += f"✅ **Bu maç Nesine'de oynanıyor**"
        else:
            mesaj += f"ℹ️ **Bu maç Nesine'de oynanmıyor**"
        
        logger.info(f"✅ SİNYAL MESAJI OLUŞTURULDU")
        return mesaj
            
    except Exception as e:
        logger.error(f"❌ Analiz hatası: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None

async def mac_isle(bot, mac_data, session):
    """
    Tek bir maçı işler - EVENT DETAY + INPLAY FALLBACK
    
    ✅ Event detay endpoint'i aktif (öncelikli)
    🔄 Fallback: Inplay verisi (event detay başarısız olursa)
    """
    try:
        mac_id = str(mac_data.get('id', ''))
        
        # Takım isimleri
        ev_adi = mac_data.get('home', {}).get('name', '') if isinstance(mac_data.get('home'), dict) else ''
        dep_adi = mac_data.get('away', {}).get('name', '') if isinstance(mac_data.get('away'), dict) else ''
        
        if not ev_adi or not dep_adi:
            logger.debug(f"⚠️ Takım isimleri eksik (event_id: {mac_id})")
            return None
        
        # ⭐ V44: Lig adı çıkarma (Nesine kontrolü için)
        league_name = mac_data.get('league', {}).get('name', '') if isinstance(mac_data.get('league'), dict) else ''
        if not league_name:
            league_name = "Unknown League"
        logger.info(f"📋 Lig: {league_name}")
        
        # Timer bilgisi (dakika)
        timer = mac_data.get('timer', {})
        dk = guvenli_int(timer.get('tm', 0)) if isinstance(timer, dict) else 0
        
        # Skor bilgisi
        skor = mac_data.get('ss', '0-0')
        if not skor:
            scores = mac_data.get('scores', {})
            if isinstance(scores, dict):
                ev_skor = scores.get('1', {}).get('home', '0')
                dep_skor = scores.get('1', {}).get('away', '0')
                skor = f"{ev_skor}-{dep_skor}"
        
        # ============================================================================
        # 🆕 EVENT DETAY API ÇAĞRISI (Öncelikli)
        # ============================================================================
        stats_data = None
        veri_kaynagi = "inplay"  # Varsayılan
        
        try:
            logger.debug(f"📡 Event detay API çağrısı yapılıyor (event_id: {mac_id})...")
            async with session.get(
                f"https://api.betsapi.com/v1/event/view?token={BETSAPI_TOKEN}&event_id={mac_id}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as event_response:
                
                if event_response.status == 200:
                    event_data = await event_response.json()
                    
                    # Event detay response'undan stats çek
                    if event_data.get('success') == 1:
                        event_results = event_data.get('results', [])
                        if event_results and len(event_results) > 0:
                            event_info = event_results[0]
                            stats_data = event_info.get('stats', {})
                            
                            if stats_data and isinstance(stats_data, dict):
                                logger.info(f"✅ Event detay API başarılı (event_id: {mac_id})")
                                veri_kaynagi = "event_detail"
                            else:
                                logger.debug(f"⚠️ Event detay'da stats yok, inplay fallback kullanılacak")
                        else:
                            logger.debug(f"⚠️ Event detay results boş, inplay fallback kullanılacak")
                    else:
                        logger.debug(f"⚠️ Event detay success=0, inplay fallback kullanılacak")
                
                elif event_response.status in [403, 401]:
                    logger.warning(f"⚠️ Event detay API yetki hatası: HTTP {event_response.status}")
                    logger.warning(f"   Inplay fallback kullanılacak")
                else:
                    logger.debug(f"⚠️ Event detay API hatası: HTTP {event_response.status}")
        
        except asyncio.TimeoutError:
            logger.warning(f"⏱️ Event detay API timeout (event_id: {mac_id}), inplay fallback kullanılacak")
        except Exception as e:
            logger.debug(f"⚠️ Event detay API hatası: {str(e)}, inplay fallback kullanılacak")
        
        # ============================================================================
        # 🔄 FALLBACK: INPLAY VERİSİ
        # ============================================================================
        if not stats_data or not isinstance(stats_data, dict):
            logger.debug(f"🔄 Inplay verisi kullanılıyor (event_id: {mac_id})")
            stats_data = mac_data.get('stats', {})
            veri_kaynagi = "inplay"
        
        # ============================================================================
        # 🐛 DEBUG: Stats Data Kontrolü
        # ============================================================================
        logger.info(f"🐛 DEBUG - Stats Data Kontrolü (event_id: {mac_id})")
        logger.info(f"   📊 Stats data tipi: {type(stats_data)}")
        logger.info(f"   📊 Stats data boş mu: {not stats_data}")
        
        if stats_data and isinstance(stats_data, dict):
            logger.info(f"   📊 Stats keys: {list(stats_data.keys())}")
            logger.info(f"   📊 Stats içeriği (ilk 200 karakter): {str(stats_data)[:200]}")
            
            # Yeni format kontrolü
            if 'corners' in stats_data:
                logger.info(f"   ✅ YENİ FORMAT tespit edildi (array)")
                logger.info(f"      corners: {stats_data.get('corners')}")
                logger.info(f"      on_target: {stats_data.get('on_target')}")
                logger.info(f"      attacks: {stats_data.get('attacks')}")
            elif '1' in stats_data:
                logger.info(f"   ✅ ESKİ FORMAT tespit edildi (dict)")
                logger.info(f"      stats['1'] keys: {list(stats_data.get('1', {}).keys())}")
                logger.info(f"      stats['2'] keys: {list(stats_data.get('2', {}).keys())}")
            else:
                logger.warning(f"   ❌ BİLİNMEYEN FORMAT!")
        else:
            logger.warning(f"   ❌ Stats data yok veya dict değil!")
            logger.info(f"   📊 mac_data keys: {list(mac_data.keys())}")
            return None
        
        # ============================================================================
        # 🆕 YENİ FORMAT PARSE DENEMESİ
        # ============================================================================
        ev_v = None
        dep_v = None
        
        # Önce yeni formatı dene
        if 'corners' in stats_data and isinstance(stats_data.get('corners'), list):
            logger.info(f"   🔄 Yeni format parse ediliyor...")
            koruma = VeriKorumaKatmani()
            parse_result = koruma.yeni_format_parse(stats_data)
            
            if parse_result:
                ev_v, dep_v = parse_result
                logger.info(f"   ✅ Yeni format başarıyla parse edildi!")
                logger.info(f"      Ev stats: {ev_v}")
                logger.info(f"      Dep stats: {dep_v}")
            else:
                logger.warning(f"   ❌ Yeni format parse başarısız!")
        
        # Eski format fallback
        if not ev_v or not dep_v:
            logger.info(f"   🔄 Eski format deneniyor...")
            ev_v = stats_data.get('1', {})  # Ev sahibi stats
            dep_v = stats_data.get('2', {})  # Deplasman stats
            
            if ev_v and dep_v:
                logger.info(f"   ✅ Eski format başarılı!")
                logger.info(f"      Ev stats keys: {list(ev_v.keys())}")
                logger.info(f"      Dep stats keys: {list(dep_v.keys())}")
        
        if not ev_v or not dep_v:
            logger.warning(f"❌ Takım istatistikleri parse edilemedi (event_id: {mac_id})")
            return None
        
        # ============================================================================
        # 🐛 DEBUG: S-kod Kontrolü
        # ============================================================================
        ev_stats_count = sum(1 for k in ev_v.keys() if k.startswith('S'))
        dep_stats_count = sum(1 for k in dep_v.keys() if k.startswith('S'))
        
        logger.info(f"🐛 DEBUG - S-kod Kontrolü")
        logger.info(f"   📊 Ev S-kod sayısı: {ev_stats_count}")
        logger.info(f"   📊 Dep S-kod sayısı: {dep_stats_count}")
        
        if ev_stats_count == 0 or dep_stats_count == 0:
            logger.warning(f"❌ S-kod istatistikleri bulunamadı (event_id: {mac_id})")
            logger.info(f"   Ev stats keys: {list(ev_v.keys())}")
            logger.info(f"   Dep stats keys: {list(dep_v.keys())}")
            logger.info(f"   ⚠️ SORUN: Stats formatı S-kod içermiyor, parse gerekebilir!")
            return None
        
        logger.info(f"✅ Maç verisi parse edildi: {ev_adi} vs {dep_adi} ({dk}', {skor})")
        logger.info(f"   Veri kaynağı: {veri_kaynagi.upper()}")
        logger.info(f"   Ev stats: {ev_stats_count} S-kod, Dep stats: {dep_stats_count} S-kod")
        
        # ⭐ V44: event_id ve league_name parametreleri eklendi
        return await mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot, session, event_id=mac_id, league_name=league_name)
        
    except Exception as e:
        logger.error(f"❌ Maç işleme hatası: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None

# ============================================================================
# ANA DÖNGÜ
# ============================================================================

async def ana_dongu():
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        logger.info("🤖 Bot başlatılıyor...")
        
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "🚀 **BOT V44 - KANTİTATİF TRADING MODEL**\n\n"
                "📚 **UPDATED:** 3 Kritik Güncelleme\n\n"
                "🎯 **V44 YENİLİKLERİ:**\n"
                "• ✅ Kantitatif Trading Stratejisi (Akademik Model)\n"
                "• ✅ DA İvmesi Filtresi (≥ 1.5 DA/dakika)\n"
                "• ✅ DA/SOT Oran Kontrolü (> 8 → sahte baskı)\n"
                "• ✅ Korner Tuzağı (Korner > 2×SOT → elen)\n"
                "• ✅ AI Analizi Düzeltmesi (Her zaman görünür)\n"
                "• ✅ Nesine Lig Kontrolü (Sadece Nesine liglerinde mesaj)\n\n"
                "📐 **xG FORMÜLÜ:**\n"
                "xG = (SOT × 0.15) + (DA × 0.05) + (TA × 0.01) + (Korner × 0.03)\n\n"
                "🔍 **KANTİTATİF FİLTRELER:**\n"
                "• DA İvmesi: ≥ 1.5 DA/dakika (altında rölanti)\n"
                "• Fiziksel Hiyerarşi: TA ≥ DA ≥ SOT ≥ Gol\n"
                "• Sahte Baskı: DA/SOT > 8 → Elen\n"
                "• Korner Tuzağı: Korner > 2×SOT → Elen\n"
                "• Kopmuş Maç: Toplam gol ≥ 5 → Durdur\n\n"
                "⏰ **ALTIN PENCERELER:**\n"
                "• 24-36 dk: Olgunlaşma Evresi (+3.5 puan)\n"
                "• 48-58 dk: Kırılma Evresi (+5.0 puan)\n\n"
                "🎖️ **LİG KATSAYILARI:**\n"
                "• Premium (Bundesliga, Eredivisie): 1.5x\n"
                "• Gençlik (U23, U21): 1.3x\n"
                "• Denge (Serie A, La Liga): 1.2x\n\n"
                "📊 **SİNYAL MODÜLLERI:**\n"
                "• İY_GOL: 15-40 dk, 0-0, DA ivmesi ≥ 1.5\n"
                "• EV_GOL/DEP_GOL: AH < 0, Dominantlık ≥ 60%\n"
                "• İKİNCİ_YARI: 46-65 ve 76-90 dk\n\n"
                "🛡️ **DOĞRULAMA:**\n"
                "• VU (Veri Uygunluğu): Kritik filtreler\n"
                "• VA (Veri Anomalisi): Normalizasyon\n"
                "• USA (Uzun Süreli Anomali): 75+ dk\n"
                "• MA (Master Algoritma): Ekstrem koşul\n\n"
                "🤖 **AI ANALİZ:**\n"
                "• Grok AI (xAI) → Gemini AI fallback\n"
                "• Her sinyalde AI analizi görünür\n\n"
                "🎯 **NESİNE KONTROLÜ:**\n"
                "• Sadece Nesine liglerinde mesaj gösterilir\n"
                "• Major ligler: Premier League, La Liga, Bundesliga, vb.\n\n"
                "📊 **HEDEF:** %85-90 başarı\n"
                "🎯 **EŞIK:** 9.0 puan\n"
                "📡 **VERİ:** Event detail + Inplay fallback"
            )
        )
        logger.info("✅ Başlangıç mesajı gönderildi")
        
    except Exception as e:
        logger.error(f"❌ Bot başlatma hatası: {str(e)}")
        return
    
    async with aiohttp.ClientSession() as session:
        dongu_sayaci = 0
        
        while True:
            dongu_sayaci += 1
            logger.info(f"{'='*60}")
            logger.info(f"🔄 DÖNGÜ #{dongu_sayaci}")
            logger.info(f"{'='*60}")
            
            try:
                # 🔧 FIX: Soccer API endpoint kullan (Bet365 yerine)
                # sport_id=1 -> Futbol
                async with session.get(
                    f"https://api.betsapi.com/v1/events/inplay?sport_id=1&token={BETSAPI_TOKEN}",
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    
                    if response.status != 200:
                        logger.error(f"❌ API hatası: HTTP {response.status}")
                        await asyncio.sleep(60)
                        continue
                    
                    data = await response.json()
                    matches = data.get('results', [])
                
                logger.info(f"📥 {len(matches)} canlı maç bulundu")
                
                for idx, mac in enumerate(esnek_liste_duzelt(matches)):
                    mac_id = str(mac.get('id') or mac.get('FI', ''))
                    
                    if not mac_id or mac_id in bildirim_gonderilen:
                        continue
                    
                    # Takım isimlerini göster
                    ev_adi = mac.get('home', {}).get('name', 'N/A') if isinstance(mac.get('home'), dict) else 'N/A'
                    dep_adi = mac.get('away', {}).get('name', 'N/A') if isinstance(mac.get('away'), dict) else 'N/A'
                    
                    logger.info(f"🔍 Maç #{idx+1}/{len(matches)}: {ev_adi} vs {dep_adi} (ID: {mac_id})")
                    
                    # ⚠️ DEĞİŞİKLİK: mac_id yerine mac_data gönder (inplay verisi)
                    mesaj = await mac_isle(bot, mac, session)
                    
                    if mesaj:
                        await bot.send_message(
                            chat_id=CHAT_ID,
                            text=mesaj,
                            parse_mode="Markdown"
                        )
                        bildirim_gonderilen.append(mac_id)
                        logger.info(f"✅ Bildirim gönderildi: {mac_id}")
                
                logger.info(f"✅ Döngü #{dongu_sayaci} tamamlandı")
                logger.info(f"🤖 Gemini AI çağrı sayısı: {gemini_ai.api_call_count}")
                
                # Veri koruma istatistikleri (her 10 döngüde bir)
                if dongu_sayaci % 10 == 0:
                    veri_koruma.istatistikleri_goster()
                
            except Exception as e:
                logger.error(f"❌ Ana döngü hatası: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
            
            logger.info("⏳ 60 saniye bekleniyor...\n")
            await asyncio.sleep(60)

if __name__ == "__main__":
    logger.info("🚀 V41.0 Final Bot Başlatılıyor...")
    logger.info(f"📍 Telegram Token: {'✅' if TELEGRAM_TOKEN else '❌'}")
    logger.info(f"📍 Chat ID: {'✅' if CHAT_ID else '❌'}")
    logger.info(f"📍 BetsAPI Token: {'✅' if BETSAPI_TOKEN else '❌'}")
    logger.info(f"📍 Gemini API Keys: {len([k for k in [GEMINI_API_KEY_1, GEMINI_API_KEY_2, GEMINI_API_KEY_3] if k])}/3")
    
    try:
        asyncio.run(ana_dongu())
    except KeyboardInterrupt:
        logger.info("⚠️ Bot kullanıcı tarafından durduruldu")
    except Exception as e:
        logger.error(f"❌ Kritik hata: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
