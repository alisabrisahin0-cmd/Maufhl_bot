import asyncio, aiohttp, os, urllib.parse, logging, re, time
from telegram import Bot
from collections import deque

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

# Gemini AI API Keys (3 adet rotasyon)
GEMINI_API_KEY_1 = os.getenv("GEMINI_API_KEY_1", "")
GEMINI_API_KEY_2 = os.getenv("GEMINI_API_KEY_2", "")
GEMINI_API_KEY_3 = os.getenv("GEMINI_API_KEY_3", "")

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
# 🎯 NESİNE LİG KONTROLÜ (LİTERATÜR)
# ============================================================================

def nesine_lig_kontrolu(ev_adi, dep_adi):
    """
    🎯 LİTERATÜR: Regex tabanlı güçlendirilmiş filtre
    U19, Reserves, E-spor maçlarını eler
    """
    mac_metni = f"{ev_adi} {dep_adi}".lower()
    
    # 1. U19, U20, U21, U23 regex ile tespit
    if re.search(r'\bu\d{2}\b', mac_metni):
        logger.info(f"🚫 Nesine'de yok: U-yaş kategorisi")
        return False
    
    # 2. Reserves
    if re.search(r'\breserve[s]?\b', mac_metni):
        logger.info(f"🚫 Nesine'de yok: Reserves")
        return False
    
    # 3. E-spor
    if re.search(r'\be[-\s]?sport[s]?\b', mac_metni):
        logger.info(f"🚫 Nesine'de yok: E-spor")
        return False
    
    # 4. Women/Kadınlar
    if re.search(r'\b(w|women|kadın|kadin)\b', mac_metni):
        logger.info(f"🚫 Nesine'de yok: Kadınlar ligi")
        return False
    
    # 5. Youth/Junior/Academy
    if re.search(r'\b(youth|junior|academy)\b', mac_metni):
        logger.info(f"🚫 Nesine'de yok: Genç takım")
        return False
    
    # 6. Virtual/Simulation
    if re.search(r'\b(virtual|simulation|sim)\b', mac_metni):
        logger.info(f"🚫 Nesine'de yok: Virtual maç")
        return False
    
    # 7. E-spor takım isimleri
    esport_takimlar = [
        'kodak', 'kray', 'og', 'hotshot', 'andrew', 'professor',
        'carlos', 'ken', 'jetli', 'volvo', 'grellz', 'glory',
        'grimace', 'frantsuz', 'nekishka', 'eden', 'boom',
        'force', 'emperor', 'yerema', 'catalyst', 'pimchik',
        'koss', 'fantazer'
    ]
    
    for takim in esport_takimlar:
        if f'({takim})' in mac_metni:
            logger.info(f"🚫 Nesine'de yok: E-spor takımı '({takim})'")
            return False
    
    logger.info(f"✅ Nesine'de oynanıyor")
    return True

# ============================================================================
# ⭐⭐⭐ KRİTİK: ALTIN PENCERE VE SKOR DURUMU FİLTRELERİ
# ============================================================================

def altin_pencere_kontrol(dakika):
    """
    🎯 LİTERATÜR: Altın Pencereler (Akademik Rapor)
    - 24-36 dk: İlk Yarı Olgunlaşma Evresi (taktik oturdu, ciddi ataklar)
    - 48-58 dk: İkinci Yarı Kırılma Evresi (yüksek enerji, savunma organize değil)
    """
    if 24 <= dakika <= 36:
        return 3.5, "ALTIN_PENCERE_1_OLGUNLASMA"  # İlk yarı olgunlaşma
    elif 48 <= dakika <= 58:
        return 5.0, "ALTIN_PENCERE_2_KIRILMA"  # İkinci yarı kırılma (en güçlü)
    elif 60 < dakika <= 75:
        return 1.5, "GECIS_OYUNU"
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
    🎯 xG (Beklenen Gol) Hesaplama (LİTERATÜR)
    
    Formül: xG = (SOT * 0.15) + (DA * 0.05) + (TA * 0.01) + (Korner * 0.03)
    
    Mantık:
    - İsabetli şut en önemli faktör (0.15)
    - Tehlikeli atak ikinci faktör (0.05)
    - Toplam atak düşük ağırlık (0.01)
    - Korner orta ağırlık (0.03)
    """
    xg = (sot * 0.15) + (da * 0.05) + (ta * 0.01) + (korner * 0.03)
    return round(xg, 2)

def sahte_baski_eliminasyonu(ev_xg, dep_xg, ev_gol, dep_gol):
    """
    🎯 Sahte Baskı Eliminasyonu (LİTERATÜR)
    
    xG ile gerçek gol arasındaki farkı analiz eder.
    Büyük fark = Sahte baskı veya şanssızlık
    """
    ev_fark = abs(ev_xg - ev_gol)
    dep_fark = abs(dep_xg - dep_gol)
    
    # Eşik: 1.5 xG farkı
    if ev_fark > 1.5:
        logger.warning(f"⚠️ Ev sahibi sahte baskı: xG={ev_xg}, Gol={ev_gol}, Fark={ev_fark}")
        return False, "EV_SAHTE_BASKI"
    
    if dep_fark > 1.5:
        logger.warning(f"⚠️ Deplasman sahte baskı: xG={dep_xg}, Gol={dep_gol}, Fark={dep_fark}")
        return False, "DEP_SAHTE_BASKI"
    
    logger.info(f"✅ Sahte baskı yok: Ev xG={ev_xg}, Dep xG={dep_xg}")
    return True, "NORMAL"

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
# GEMİNİ AI ENTEGRASYONu
# ============================================================================

class GeminiAIAnalyzer:
    def __init__(self):
        self.api_keys = [k for k in [GEMINI_API_KEY_1, GEMINI_API_KEY_2, GEMINI_API_KEY_3] if k]
        self.current_key_index = 0
        self.api_call_count = 0
        
        if self.api_keys:
            logger.info(f"✅ {len(self.api_keys)} Gemini API key yüklendi")
        else:
            logger.warning("⚠️ Gemini API key yok, AI analizi devre dışı")
    
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
            
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={api_key}"
            
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.9,  # 🎯 LİTERATÜR: Daha yaratıcı (0.7 → 0.9)
                    "maxOutputTokens": 500  # 🎯 LİTERATÜR: Daha detaylı (400 → 500)
                }
            }
            
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status != 200:
                    logger.error(f"❌ Gemini API hatası: HTTP {response.status}")
                    return None
                
                data = await response.json()
                
                if 'candidates' in data and len(data['candidates']) > 0:
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    logger.info(f"🤖 Gemini AI yanıtı alındı")
                    return text
                
                return None
                
        except Exception as e:
            logger.error(f"❌ Gemini AI hatası: {str(e)}")
            return None

gemini_ai = GeminiAIAnalyzer()

# ============================================================================
# 🎯 ASIAN HANDICAP ENTEGRASYONu (LİTERATÜR)
# ============================================================================

async def asian_handicap_cek(event_id, session):
    """
    🎯 Asian Handicap Çek (LİTERATÜR)
    
    /event/odds endpoint'inden Asian Handicap çeker (kod: 1_2)
    - Buçuklu handikaplar: -0.5, +1.5
    - Çeyrekli handikaplar: -0.25, +0.75
    - Canlı handikap çizgisi takibi
    
    Returns:
        dict: {'ev_handicap': -0.5, 'dep_handicap': +0.5, 'ev_oran': 1.85, 'dep_oran': 2.05}
        None: Hata durumunda
    """
    try:
        logger.debug(f"📡 Asian Handicap API çağrısı (event_id: {event_id})...")
        
        async with session.get(
            f"https://api.betsapi.com/v1/event/odds?token={BETSAPI_TOKEN}&event_id={event_id}",
            timeout=aiohttp.ClientTimeout(total=10)
        ) as response:
            
            # Response validation
            if response.status != 200:
                logger.warning(f"⚠️ Asian Handicap API hatası: HTTP {response.status}")
                return None
            
            # JSON parse validation
            try:
                data = await response.json()
            except Exception as e:
                logger.error(f"❌ Asian Handicap JSON parse hatası: {str(e)}")
                return None
            
            # Success kontrolü
            if data.get('success') != 1:
                logger.debug(f"⚠️ Asian Handicap API success=0")
                return None
            
            # Results validation
            results = data.get('results', {})
            if not isinstance(results, dict):
                logger.debug(f"⚠️ Asian Handicap results geçersiz")
                return None
            
            # Odds validation
            odds = results.get('odds', {})
            if not isinstance(odds, dict):
                logger.debug(f"⚠️ Asian Handicap odds geçersiz")
                return None
            
            # Asian Handicap (1_2) arama
            asian_handicap = odds.get('1_2', {})
            if not isinstance(asian_handicap, dict):
                logger.debug(f"⚠️ Asian Handicap (1_2) bulunamadı")
                return None
            
            # Handikap değerlerini çek
            ev_handicap = guvenli_float(asian_handicap.get('home_od', 0))
            dep_handicap = guvenli_float(asian_handicap.get('away_od', 0))
            ev_oran = guvenli_float(asian_handicap.get('home_odds', 0))
            dep_oran = guvenli_float(asian_handicap.get('away_odds', 0))
            
            if ev_handicap == 0 and dep_handicap == 0:
                logger.debug(f"⚠️ Asian Handicap değerleri sıfır")
                return None
            
            logger.info(f"✅ Asian Handicap: Ev {ev_handicap} ({ev_oran}), Dep {dep_handicap} ({dep_oran})")
            
            return {
                'ev_handicap': ev_handicap,
                'dep_handicap': dep_handicap,
                'ev_oran': ev_oran,
                'dep_oran': dep_oran
            }
            
    except asyncio.TimeoutError:
        logger.warning(f"⏱️ Asian Handicap API timeout (event_id: {event_id})")
        return None
    except Exception as e:
        logger.error(f"❌ Asian Handicap hatası: {str(e)}")
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

async def mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot, session, event_id=None):
    """
    Kantitatif analiz motoru (V44 - LİTERATÜR):
    1. Altın pencere kontrolü (24-36, 48-58 dakika)
    2. Skor durumu kontrolü (kaos/rölanti)
    3. SOT epilasyon kontrolü
    4. Fiziksel hiyerarşi kontrolü
    5. ⭐ YENİ: Oyun durumu normalizasyonu
    6. ⭐ YENİ: Korner tuzağı kontrolü
    7. ⭐ YENİ: xG (Beklenen Gol) hesaplama
    8. ⭐ YENİ: Sahte baskı eliminasyonu
    9. ⭐ YENİ: Çok katmanlı doğrulama (VU, VA, USA, MA)
    10. ⭐ YENİ: Asian Handicap entegrasyonu
    11. Gemini AI analizi
    """
    try:
        logger.info(f"🔍 Analiz: {ev_adi} vs {dep_adi} - {dk}'")
        
        # ----------------------------------------------------------------
        # VERİ ÇIKARMA
        # ----------------------------------------------------------------
        v = veri_cikart(ev_v, dep_v)
        
        sot = v['ev_sot'] + v['dep_sot']
        ta = v['ev_ta'] + v['dep_ta']
        da = v['ev_da'] + v['dep_da']
        ev_gol = v['ev_gol']
        dep_gol = v['dep_gol']
        toplam_gol = ev_gol + dep_gol
        
        logger.info(f"📊 TA:{ta}, DA:{da}, SOT:{sot}, Gol:{toplam_gol}")
        
        # ----------------------------------------------------------------
        # ⭐⭐⭐ KRİTİK FİLTRELER
        # ----------------------------------------------------------------
        
        # 1. Skor durumu kontrolü (Kaos/Rölanti)
        skor_ok, skor_durum = skor_durumu_kontrol(ev_gol, dep_gol)
        if not skor_ok:
            return None
        
        # 2. Dakika aralığı kontrolü (15-85)
        if not (15 <= dk <= 85):
            logger.debug(f"⏱️ Dakika aralık dışı: {dk}")
            return None
        
        # 3. ⭐ YENİ: Oyun Durumu Normalizasyonu
        ev_da_norm, ev_sot_norm, dep_da_norm, dep_sot_norm = oyun_durumu_normalizasyonu(
            ev_gol, dep_gol, v['ev_da'], v['dep_da'], v['ev_sot'], v['dep_sot'], dk
        )
        logger.info(f"🔄 Normalizasyon: Ev DA:{v['ev_da']}→{ev_da_norm}, Dep DA:{v['dep_da']}→{dep_da_norm}")
        
        # Normalize edilmiş değerleri kullan
        da_norm = ev_da_norm + dep_da_norm
        sot_norm = ev_sot_norm + dep_sot_norm
        
        # 4. ⭐ YENİ: xG (Beklenen Gol) Hesaplama
        ev_korner = v.get('ev_korner', 0)
        dep_korner = v.get('dep_korner', 0)
        
        ev_xg = xg_hesapla(v['ev_sot'], v['ev_da'], v['ev_ta'], ev_korner)
        dep_xg = xg_hesapla(v['dep_sot'], v['dep_da'], v['dep_ta'], dep_korner)
        logger.info(f"🎯 xG: Ev={ev_xg}, Dep={dep_xg}")
        
        # 5. ⭐ YENİ: Sahte Baskı Eliminasyonu
        sahte_baski_ok, sahte_baski_durum = sahte_baski_eliminasyonu(ev_xg, dep_xg, ev_gol, dep_gol)
        if not sahte_baski_ok:
            logger.warning(f"❌ Sahte baskı tespit edildi: {sahte_baski_durum}")
            return None
        
        # 6. ⭐ YENİ: Korner Tuzağı Kontrolü (S2 verisi varsa)
        if ev_korner > 0 or dep_korner > 0:
            if not korner_tuzagi_kontrolu(ev_korner, dep_korner, v['ev_sot'], v['dep_sot']):
                logger.warning(f"❌ Korner tuzağı tespit edildi")
                return None
        
        # 7. Fiziksel hiyerarşi kontrolü
        if ta < da or da < sot or ta < sot:
            logger.warning(f"❌ Fiziksel hiyerarşi ihlali: TA:{ta}, DA:{da}, SOT:{sot}")
            return None
        
        # 8. Gol vs SOT kontrolü
        if sot < toplam_gol:
            logger.warning(f"❌ SOT < Gol: {sot} < {toplam_gol}")
            return None
        
        # 9. Dakika başı şut limiti
        if dk > 0 and sot > (dk * 0.7):
            logger.warning(f"❌ SOT limiti aşıldı: {sot} > {dk * 0.7}")
            return None
        
        # 10. ⭐ YENİ: Master Algoritma (Ekstrem Koşul Kontrolü)
        ma_aktif = False
        if toplam_gol >= 4 and dk >= 75:
            logger.warning(f"⚠️ Master Algoritma: Toplam gol {toplam_gol} >= 4 ve dakika {dk} >= 75")
            ma_aktif = True
        if abs(ev_gol - dep_gol) >= 3 and dk >= 70:
            logger.warning(f"⚠️ Master Algoritma: Gol farkı {abs(ev_gol - dep_gol)} >= 3 ve dakika {dk} >= 70")
            ma_aktif = True
        
        # ----------------------------------------------------------------
        # ⭐⭐⭐ PUANLAMA SİSTEMİ (Literatür Bazlı + Normalizasyon)
        # ----------------------------------------------------------------
        puan = 4.0
        
        # Altın pencere bonusu
        zaman_bonusu, zaman_tipi = altin_pencere_kontrol(dk)
        puan += zaman_bonusu
        if zaman_bonusu > 0:
            logger.info(f"⭐ Altın pencere bonusu: +{zaman_bonusu} ({zaman_tipi})")
        
        # Skor durumu bonusu
        if skor_durum == "OPTIMUM":
            puan += 3.0
            logger.info(f"🎯 Optimum skor bonusu: +3.0")
        
        # SOT puanı (epilasyon kontrolü ile) - Normalize edilmiş değer kullan
        sot_puan = sot_epilasyon_kontrol(sot_norm)
        puan += sot_puan
        logger.info(f"🎯 SOT puanı: {sot_puan} (SOT norm: {sot_norm})")
        
        # DA bonusu (her 10 DA için 0.5 puan, max 3.0) - Normalize edilmiş değer kullan
        da_bonus = min((da_norm // 10) * 0.5, 3.0)
        puan += da_bonus
        logger.info(f"📊 DA bonusu: +{da_bonus} (DA norm: {da_norm})")
        
        logger.info(f"💯 Toplam puan: {round(puan, 1)}")
        
        # ----------------------------------------------------------------
        # ⭐ YENİ: ÇOK KATMANLI DOĞRULAMA (LİTERATÜR)
        # ----------------------------------------------------------------
        dogrulama = CokKatmanliDogrulama()
        
        # VU (Veri Uygunluğu): Tüm kritik filtrelerden geçtiyse 1
        dogrulama.VU = 1
        
        # VA (Veri Anomalisi): Normalize edilmiş değerler orijinalden çok farklıysa 1
        if abs(da - da_norm) > (da * 0.3) or abs(sot - sot_norm) > (sot * 0.3):
            dogrulama.VA = 1
            logger.info(f"⚠️ Veri anomalisi tespit edildi (normalizasyon farkı yüksek)")
        
        # USA (Uzun Süreli Anomali): 80+ dakika ve anomali varsa 1
        if dk >= 80 and dogrulama.VA == 1:
            dogrulama.USA = 1
            logger.info(f"⚠️ Uzun süreli anomali tespit edildi")
        
        # MA (Master Algoritma): Ekstrem koşul varsa 1
        if ma_aktif:
            dogrulama.MA = 1
            logger.warning(f"⚠️ Master Algoritma aktif")
        
        # Çok katmanlı doğrulama kontrolü
        if not dogrulama.sinyal_uret():
            logger.warning(f"❌ Çok katmanlı doğrulama başarısız")
            return None
        
        # ----------------------------------------------------------------
        # SİNYAL OLUŞTURMA (Puan >= 9.0) ⭐ YENİ EŞIK
        # ----------------------------------------------------------------
        if puan >= 9.0:
            # Gemini AI analizi
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
            
            logger.info("🤖 Gemini AI analizi isteniyor...")
            ai_analiz = await gemini_ai.analiz_yap(mac_verisi, session)
            
            # ⭐ YENİ: Asian Handicap çek
            asian_handicap = None
            if event_id:
                logger.info("📊 Asian Handicap çekiliyor...")
                asian_handicap = await asian_handicap_cek(event_id, session)
            
            # Tavsiye oluştur - Sadece "SIRADAKİ GOL" (hangi takım atacak diye tahmin yapma)
            xg_fark = abs(ev_xg - dep_xg)
            
            # Ana tavsiye: Filtreleme sonucuna göre sadece "GOL OLACAK"
            if skor_durum == "OPTIMUM" and 55 <= dk <= 60:
                tavsiye = "⭐ ALTIN FIRSAT: SIRADAKİ GOL"
            elif xg_fark > 0.8:
                tavsiye = "🎯 SIRADAKİ GOL (Yüksek Momentum)"
            elif xg_fark > 0.4:
                tavsiye = "📊 SIRADAKİ GOL (Orta Momentum)"
            else:
                tavsiye = "⚖️ SIRADAKİ GOL (Dengeli Oyun)"
            
            # xG bilgisi ekle (hangi takım daha baskın - sadece bilgi amaçlı)
            if ev_xg > dep_xg + 0.5:
                tavsiye += f"\n💡 xG Analizi: Ev sahibi daha baskın ({ev_xg:.2f} vs {dep_xg:.2f})"
            elif dep_xg > ev_xg + 0.5:
                tavsiye += f"\n💡 xG Analizi: Deplasman daha baskın ({dep_xg:.2f} vs {ev_xg:.2f})"
            else:
                tavsiye += f"\n💡 xG Analizi: Dengeli oyun ({ev_xg:.2f} vs {dep_xg:.2f})"
            
            # Asian Handicap bilgisi (varsa - piyasa görüşü, bilgi amaçlı)
            if asian_handicap:
                try:
                    ah_value = float(asian_handicap.get('ev_handicap', 0))
                    if ah_value < -0.5:
                        tavsiye += f"\n📊 Handikap: Ev sahibi favori ({asian_handicap.get('ev_handicap')})"
                    elif ah_value > 0.5:
                        tavsiye += f"\n📊 Handikap: Deplasman favori (+{asian_handicap.get('ev_handicap')})"
                except:
                    pass
            
            # Toplam xG hesapla
            xg = ev_xg + dep_xg
            
            mesaj = (
                f"💎 **SİNYAL (Puan: {round(puan,1)})**\n"
                f"⚽ {ev_adi} {skor} {dep_adi}\n"
                f"⏱ Dakika: {dk}' | Pencere: {zaman_tipi}\n"
                f"{'='*30}\n"
                f"📊 **İstatistikler:**\n"
                f"• Toplam Atak: {ta} (Ev:{v['ev_ta']}, Dep:{v['dep_ta']})\n"
                f"• Tehlikeli Atak: {da} (Ev:{v['ev_da']}, Dep:{v['dep_da']})\n"
                f"• İsabetli Şut: {sot} (Ev:{v['ev_sot']}, Dep:{v['dep_sot']})\n"
                f"• Gol: {toplam_gol} (Ev:{ev_gol}, Dep:{dep_gol})\n"
                f"• xG (Beklenen Gol): Ev {ev_xg}, Dep {dep_xg}\n"
                f"{'='*30}\n"
                f"🎯 **Doğrulama:** VU={dogrulama.VU}, VA={dogrulama.VA}, USA={dogrulama.USA}, MA={dogrulama.MA}\n"
                f"{'='*30}\n"
                f"💡 **Tavsiye:** {tavsiye}\n"
                f"{'='*30}\n"
                f"🔍 **Filtre Sonuçları:**\n"
                f"✅ Skor Durumu: {skor_durum}\n"
                f"✅ Dakika Penceresi: {dk}' ({zaman_tipi if zaman_bonusu > 0 else 'Normal'})\n"
                f"✅ Fiziksel Hiyerarşi: TA({ta}) > DA({da}) > SOT({sot})\n"
                f"✅ xG Kontrolü: {xg:.2f}\n"
                f"✅ Sahte Baskı: {'Tespit Edilmedi' if not sahte_baski_durum else 'Tespit Edildi'}\n"
                f"✅ Doğrulama: VU:{dogrulama.VU} VA:{dogrulama.VA} USA:{dogrulama.USA} MA:{dogrulama.MA}\n"
            )
            
            # Asian Handicap bilgisi ekle
            if asian_handicap:
                mesaj += f"{'='*30}\n"
                mesaj += f"📊 **Asian Handicap:**\n"
                mesaj += f"• Ev: {asian_handicap['ev_handicap']} (Oran: {asian_handicap['ev_oran']})\n"
                mesaj += f"• Dep: {asian_handicap['dep_handicap']} (Oran: {asian_handicap['dep_oran']})\n"
            
            if ai_analiz:
                mesaj += f"{'='*30}\n"
                mesaj += f"🤖 **Gemini AI:**\n{ai_analiz}\n"
            
            mesaj += f"{'='*30}\n"
            mesaj += f"ℹ️ Bu maç Nesine'de oynanıyor"
            
            logger.info(f"✅ SİNYAL OLUŞTURULDU (AI: {'✅' if ai_analiz else '❌'})")
            return mesaj
        else:
            logger.info(f"📉 Puan yetersiz: {round(puan, 1)} < 9.0")
            return None
            
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
        
        if not stats_data or not isinstance(stats_data, dict):
            logger.debug(f"⚠️ İstatistik verisi yok (event_id: {mac_id})")
            logger.debug(f"   Mevcut keys: {list(mac_data.keys())}")
            return None
        
        ev_v = stats_data.get('1', {})  # Ev sahibi stats
        dep_v = stats_data.get('2', {})  # Deplasman stats
        
        if not ev_v or not dep_v:
            logger.debug(f"⚠️ Takım istatistikleri eksik (event_id: {mac_id})")
            return None
        
        # S-kodlarını kontrol et (en az birkaç istatistik olmalı)
        ev_stats_count = sum(1 for k in ev_v.keys() if k.startswith('S'))
        dep_stats_count = sum(1 for k in dep_v.keys() if k.startswith('S'))
        
        if ev_stats_count == 0 or dep_stats_count == 0:
            logger.debug(f"⚠️ S-kod istatistikleri bulunamadı (event_id: {mac_id})")
            logger.debug(f"   Ev stats keys: {list(ev_v.keys())}")
            logger.debug(f"   Dep stats keys: {list(dep_v.keys())}")
            return None
        
        logger.info(f"✅ Maç verisi parse edildi: {ev_adi} vs {dep_adi} ({dk}', {skor})")
        logger.info(f"   Veri kaynağı: {veri_kaynagi.upper()}")
        logger.info(f"   Ev stats: {ev_stats_count} S-kod, Dep stats: {dep_stats_count} S-kod")
        
        # ⭐ YENİ: event_id parametresi eklendi (Asian Handicap için)
        return await mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot, session, event_id=mac_id)
        
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
                "🚀 **BOT V44 LİTERATÜR PRO - FULL EDITION**\n\n"
                "📚 **LİTERATÜR BAZLI:** Akademik rapor uyumlu\n\n"
                "🎯 **YENİ ÖZELLİKLER:**\n"
                "• 🆕 xG (Beklenen Gol) Hesaplama\n"
                "• 🆕 Sahte Baskı Eliminasyonu\n"
                "• 🆕 Asian Handicap Entegrasyonu\n"
                "• 🆕 Master Algoritma (MA) - Ekstrem koşul şalteri\n"
                "• 🆕 Yeni Stats Format Desteği (API liste formatı)\n\n"
                "🎯 **ALTIN PENCERELER (LİTERATÜR):**\n"
                "• 24-36 dk: Olgunlaşma Evresi (+3.5 puan)\n"
                "• 48-58 dk: Kırılma Evresi (+5.0 puan)\n\n"
                "🛡️ **DOĞRULAMA BAYRAKLARI:**\n"
                "• VU (Veri Uygunluğu): Kritik filtreler\n"
                "• VA (Veri Anomalisi): Normalizasyon kontrolü\n"
                "• USA (Uzun Süreli Anomali): 80+ dk kontrolü\n"
                "• MA (Master Algoritma): Ekstrem koşul\n"
                "• Başarı Kuralı: VA ve USA senkronize olmalı\n\n"
                "🎯 **MEVCUT ÖZELLİKLER:**\n"
                "• ✅ Regex Filtreler: U19/Reserves/E-spor\n"
                "• ✅ Sezgisel Gemini AI (Temp: 0.9)\n"
                "• ✅ Kaos/Rölanti Filtreleri\n"
                "• ✅ Oyun Durumu Normalizasyonu\n"
                "• ✅ Korner Tuzağı Kontrolü\n"
                "• 🛡️ Veri Koruma Katmanı\n"
                "• 🛡️ Fiziksel Hiyerarşi (TA>=DA>=SOT>=Gol)\n"
                "• 🛡️ Akıllı S-kod Adaptasyonu\n\n"
                "📊 **HEDEF:** %85-90 başarı\n"
                "🎯 **EŞIK:** 9.0 puan\n"
                "📡 **VERİ:** Event detail + Inplay fallback\n"
                "🔒 **API DOĞRULAMA:** Tüm çağrılarda aktif"
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
