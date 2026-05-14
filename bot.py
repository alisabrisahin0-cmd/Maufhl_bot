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
        return 3.5, "OLGUNLAŞMA EVRESİ"  # İlk yarı olgunlaşma
    elif 48 <= dakika <= 58:
        return 5.0, "KIRILMA EVRESİ"  # İkinci yarı kırılma (en güçlü)
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
            logger.warning("🐛 Grok AI: API key yok!")
            return None
        
        try:
            self.api_call_count += 1
            
            logger.info(f"🐛 Grok AI: API çağrısı #{self.api_call_count}")
            
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
            
            logger.info(f"🐛 Grok AI: POST isteği gönderiliyor...")
            
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
                logger.info(f"🐛 Grok AI: Response status: {response.status}")
                
                if response.status != 200:
                    response_text = await response.text()
                    logger.error(f"❌ Grok API hatası: HTTP {response.status}")
                    logger.error(f"   Response: {response_text[:200]}")
                    return None
                
                data = await response.json()
                logger.info(f"🐛 Grok AI: JSON parse başarılı")
                logger.info(f"🐛 Grok AI: Response keys: {list(data.keys())}")
                
                if 'choices' in data and len(data['choices']) > 0:
                    text = data['choices'][0]['message']['content']
                    logger.info(f"✅ Grok AI yanıtı alındı ({len(text)} karakter)")
                    logger.info(f"   İlk 100 karakter: {text[:100]}")
                    return text
                else:
                    logger.warning(f"⚠️ Grok AI: 'choices' yok veya boş")
                    logger.warning(f"   Data: {str(data)[:200]}")
                
                return None
                
        except Exception as e:
            logger.error(f"❌ Grok AI hatası: {str(e)}")
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

DOĞAL dille yaz. İnsan gibi düşün, makine gibi değil."""
            
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={api_key}"
            
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.9,
                    "maxOutputTokens": 500
                }
            }
            
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status != 200:
                    return None
                
                data = await response.json()
                
                if 'candidates' in data and len(data['candidates']) > 0:
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    logger.info(f"✅ Gemini AI yanıtı alındı (fallback)")
                    return text
                
                return None
                
        except Exception as e:
            logger.error(f"❌ Gemini AI hatası: {str(e)}")
            return None

# 🔧 FIX V4: Önce Grok, sonra Gemini fallback
grok_ai = GrokAIAnalyzer()
gemini_ai = GeminiAIAnalyzer()

async def ai_analiz_yap(mac_verisi, session):
    """
    AI analizi - Önce Grok, başarısız olursa Gemini
    """
    # Önce Grok dene
    if grok_ai.api_key:
        result = await grok_ai.analiz_yap(mac_verisi, session)
        if result:
            return result, "Grok"
    
    # Grok başarısız, Gemini dene
    if gemini_ai.api_keys:
        result = await gemini_ai.analiz_yap(mac_verisi, session)
        if result:
            return result, "Gemini"
    
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
        # 🐛 DEBUG: Filtre Kontrolü
        # ----------------------------------------------------------------
        logger.info(f"🐛 DEBUG - Filtre Kontrolü Başlıyor")
        
        # ----------------------------------------------------------------
        # ⭐⭐⭐ KRİTİK FİLTRELER
        # ----------------------------------------------------------------
        
        # 1. Skor durumu kontrolü (Kaos/Rölanti)
        skor_ok, skor_durum = skor_durumu_kontrol(ev_gol, dep_gol)
        logger.info(f"   ✓ Skor kontrolü: {skor_ok} ({skor_durum})")
        if not skor_ok:
            logger.warning(f"   ❌ ELENDİ: Skor durumu uygun değil ({skor_durum})")
            return None
        
        # 2. Dakika aralığı kontrolü (15-85)
        logger.info(f"   ✓ Dakika kontrolü: {dk} (15-85 arası olmalı)")
        if not (15 <= dk <= 85):
            logger.warning(f"   ❌ ELENDİ: Dakika aralık dışı: {dk}")
            return None
        
        # 3. ⭐ YENİ: Oyun Durumu Normalizasyonu
        ev_da_norm, ev_sot_norm, dep_da_norm, dep_sot_norm = oyun_durumu_normalizasyonu(
            ev_gol, dep_gol, v['ev_da'], v['dep_da'], v['ev_sot'], v['dep_sot'], dk
        )
        logger.info(f"   ✓ Normalizasyon: Ev DA:{v['ev_da']}→{ev_da_norm}, Dep DA:{v['dep_da']}→{dep_da_norm}")
        
        # Normalize edilmiş değerleri kullan
        da_norm = ev_da_norm + dep_da_norm
        sot_norm = ev_sot_norm + dep_sot_norm
        
        # 4. ⭐ YENİ: xG (Beklenen Gol) Hesaplama
        ev_korner = v.get('ev_korner', 0)
        dep_korner = v.get('dep_korner', 0)
        
        ev_xg = xg_hesapla(v['ev_sot'], v['ev_da'], v['ev_ta'], ev_korner)
        dep_xg = xg_hesapla(v['dep_sot'], v['dep_da'], v['dep_ta'], dep_korner)
        logger.info(f"   ✓ xG: Ev={ev_xg}, Dep={dep_xg}")
        
        # 5. ⭐ YENİ: Sahte Baskı Eliminasyonu - FİX EDİLDİ
        sahte_baski_ok, sahte_baski_durum = sahte_baski_eliminasyonu(ev_xg, dep_xg, ev_gol, dep_gol)
        logger.info(f"   ✓ Sahte baskı kontrolü: {sahte_baski_ok} (Durum: {sahte_baski_durum})")
        if not sahte_baski_ok:
            logger.warning(f"   ❌ ELENDİ: Sahte baskı tespit edildi: {sahte_baski_durum}")
            return None  # SİNYAL GÖNDERİLMEMELİ!
        
        # 6. ⭐ YENİ: Korner Tuzağı Kontrolü (S2 verisi varsa)
        if ev_korner > 0 or dep_korner > 0:
            korner_ok = korner_tuzagi_kontrolu(ev_korner, dep_korner, v['ev_sot'], v['dep_sot'])
            logger.info(f"   ✓ Korner tuzağı kontrolü: {korner_ok}")
            if not korner_ok:
                logger.warning(f"   ❌ ELENDİ: Korner tuzağı tespit edildi")
                return None
        
        # 7. Fiziksel hiyerarşi kontrolü
        hiyerarsi_ok = ta >= da and da >= sot and ta >= sot
        logger.info(f"   ✓ Fiziksel hiyerarşi: {hiyerarsi_ok} (TA:{ta} >= DA:{da} >= SOT:{sot})")
        if not hiyerarsi_ok:
            logger.warning(f"   ❌ ELENDİ: Fiziksel hiyerarşi ihlali")
            return None
        
        # 8. Gol vs SOT kontrolü
        gol_sot_ok = sot >= toplam_gol
        logger.info(f"   ✓ Gol/SOT kontrolü: {gol_sot_ok} (SOT:{sot} >= Gol:{toplam_gol})")
        if not gol_sot_ok:
            logger.warning(f"   ❌ ELENDİ: SOT < Gol")
            return None
        
        # 9. Dakika başı şut limiti
        sot_limit = dk * 0.7 if dk > 0 else 999
        sot_limit_ok = sot <= sot_limit
        logger.info(f"   ✓ SOT limit kontrolü: {sot_limit_ok} (SOT:{sot} <= {sot_limit:.1f})")
        if not sot_limit_ok:
            logger.warning(f"   ❌ ELENDİ: SOT limiti aşıldı")
            return None
        
        # 10. ⭐ YENİ: Master Algoritma (Ekstrem Koşul Kontrolü)
        ma_aktif = False
        if toplam_gol >= 4 and dk >= 75:
            logger.warning(f"   ⚠️ Master Algoritma: Toplam gol {toplam_gol} >= 4 ve dakika {dk} >= 75")
            ma_aktif = True
        if abs(ev_gol - dep_gol) >= 3 and dk >= 70:
            logger.warning(f"   ⚠️ Master Algoritma: Gol farkı {abs(ev_gol - dep_gol)} >= 3 ve dakika {dk} >= 70")
            ma_aktif = True
        
        logger.info(f"🎉 TÜM FİLTRELERDEN GEÇTİ!")
        
        # ----------------------------------------------------------------
        # ⭐⭐⭐ PUANLAMA SİSTEMİ (Literatür Bazlı + Normalizasyon + Asian Handicap)
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
        
        # ⭐⭐⭐ YENİ: ASIAN HANDICAP PUANLAMASI (Erken çek - event_id gerekli)
        asian_handicap = None
        ah_bonus = 0.0
        if event_id:
            logger.info("📊 Asian Handicap çekiliyor (puanlama için)...")
            asian_handicap = await asian_handicap_cek(event_id, session)
            
            if asian_handicap:
                ev_hc = asian_handicap['ev_handicap']
                dep_hc = asian_handicap['dep_handicap']
                ev_oran = asian_handicap['ev_oran']
                dep_oran = asian_handicap['dep_oran']
                
                logger.info(f"   ✅ AH alındı: Ev {ev_hc} ({ev_oran}), Dep {dep_hc} ({dep_oran})")
                
                # Değerli çizgiler (Value lines): +1.0 veya +2.0 bonus
                # Mantık: Handikap değeri büyükse (örn. +1.5, +2.5) değerlidir
                if abs(ev_hc) >= 1.5 or abs(dep_hc) >= 1.5:
                    ah_bonus = 2.0
                    logger.info(f"   💎 Değerli AH çizgisi: +{ah_bonus} puan (|HC| >= 1.5)")
                elif abs(ev_hc) >= 1.0 or abs(dep_hc) >= 1.0:
                    ah_bonus = 1.0
                    logger.info(f"   💎 Değerli AH çizgisi: +{ah_bonus} puan (|HC| >= 1.0)")
                
                # Tuzak çizgiler (Trap lines): -2.0 ceza
                # Mantık: Çok düşük handikap + düşük oran = tuzak
                # Örnek: -0.25 handikap + 1.50 oran = şüpheli
                if (abs(ev_hc) <= 0.25 and ev_oran < 1.60) or (abs(dep_hc) <= 0.25 and dep_oran < 1.60):
                    ah_bonus = -2.0
                    logger.warning(f"   ⚠️ Tuzak AH çizgisi: {ah_bonus} puan (düşük HC + düşük oran)")
                
                puan += ah_bonus
                logger.info(f"📊 Asian Handicap bonusu: {ah_bonus:+.1f}")
            else:
                logger.warning(f"   ⚠️ Asian Handicap alınamadı, bonus yok")
        
        logger.info(f"💯 Toplam puan: {round(puan, 1)}")
        
        # ----------------------------------------------------------------
        # ⭐ YENİ: ÇOK KATMANLI DOĞRULAMA (LİTERATÜR) - FİX EDİLDİ
        # ----------------------------------------------------------------
        logger.info(f"🐛 DEBUG - Çok Katmanlı Doğrulama Başlıyor")
        dogrulama = CokKatmanliDogrulama()
        
        # VU (Veri Uygunluğu): Tüm kritik filtrelerden geçtiyse 1
        dogrulama.VU = 1
        logger.info(f"   ✓ VU (Veri Uygunluğu) = 1 (tüm filtrelerden geçti)")
        
        # ----------------------------------------------------------------
        # 🐛 DEBUG: VA (Veri Anomalisi) Hesaplama - FİX EDİLDİ
        # ----------------------------------------------------------------
        logger.info(f"🐛 DEBUG - VA (Veri Anomalisi) Hesaplama:")
        logger.info(f"   📊 DA orijinal: {da}")
        logger.info(f"   📊 DA normalize: {da_norm}")
        logger.info(f"   📊 DA farkı: {abs(da - da_norm)}")
        logger.info(f"   📊 DA eşik (%20): {da * 0.2}")  # 🔧 FIX: %30 -> %20 (daha hassas)
        logger.info(f"   📊 SOT orijinal: {sot}")
        logger.info(f"   📊 SOT normalize: {sot_norm}")
        logger.info(f"   📊 SOT farkı: {abs(sot - sot_norm)}")
        logger.info(f"   📊 SOT eşik (%20): {sot * 0.2}")  # 🔧 FIX: %30 -> %20 (daha hassas)
        
        # 🔧 FIX: VA eşiğini düşür (%30 -> %20) ve minimum fark ekle
        da_fark = abs(da - da_norm)
        sot_fark = abs(sot - sot_norm)
        
        if (da_fark > max(da * 0.2, 5)) or (sot_fark > max(sot * 0.2, 2)):
            dogrulama.VA = 1
            logger.info(f"   ✓ VA = 1 (Veri anomalisi tespit edildi - normalizasyon farkı yüksek)")
        else:
            logger.info(f"   ✓ VA = 0 (Veri anomalisi yok - normalizasyon farkı düşük)")
        
        # ----------------------------------------------------------------
        # 🐛 DEBUG: USA (Uzun Süreli Anomali) Hesaplama - FİX EDİLDİ
        # ----------------------------------------------------------------
        logger.info(f"🐛 DEBUG - USA (Uzun Süreli Anomali) Hesaplama:")
        logger.info(f"   📊 Dakika: {dk}")
        logger.info(f"   📊 VA değeri: {dogrulama.VA}")
        logger.info(f"   📊 Koşul: dk >= 75 AND VA == 1")  # 🔧 FIX: 80 -> 75 (daha erken tespit)
        
        # 🔧 FIX: USA eşiğini düşür (80 -> 75 dakika)
        if dk >= 75 and dogrulama.VA == 1:
            dogrulama.USA = 1
            logger.info(f"   ✓ USA = 1 (Uzun süreli anomali tespit edildi)")
        else:
            logger.info(f"   ✓ USA = 0 (Uzun süreli anomali yok)")
            if dk < 75:
                logger.info(f"      Sebep: Dakika {dk} < 75")
            if dogrulama.VA == 0:
                logger.info(f"      Sebep: VA = 0 (anomali yok)")
        
        # ----------------------------------------------------------------
        # 🐛 DEBUG: MA (Master Algoritma) Hesaplama
        # ----------------------------------------------------------------
        logger.info(f"🐛 DEBUG - MA (Master Algoritma) Hesaplama:")
        logger.info(f"   📊 Toplam gol: {toplam_gol}")
        logger.info(f"   📊 Gol farkı: {abs(ev_gol - dep_gol)}")
        logger.info(f"   📊 Dakika: {dk}")
        logger.info(f"   📊 Koşul 1: toplam_gol >= 4 AND dk >= 75")
        logger.info(f"   📊 Koşul 2: gol_farki >= 3 AND dk >= 70")
        logger.info(f"   📊 ma_aktif değeri: {ma_aktif}")
        
        # MA (Master Algoritma): Ekstrem koşul varsa 1
        if ma_aktif:
            dogrulama.MA = 1
            logger.info(f"   ✓ MA = 1 (Master Algoritma aktif - ekstrem koşul)")
        else:
            logger.info(f"   ✓ MA = 0 (Master Algoritma pasif - normal koşul)")
        
        # ----------------------------------------------------------------
        # 🐛 DEBUG: Doğrulama Özeti - STRICT SYNC RESTORED
        # ----------------------------------------------------------------
        logger.info(f"🐛 DEBUG - Doğrulama Özeti:")
        logger.info(f"   VU={dogrulama.VU}, VA={dogrulama.VA}, USA={dogrulama.USA}, MA={dogrulama.MA}")
        
        # ⭐⭐⭐ STRICT VA/USA SYNCHRONIZATION RESTORED (85-90% başarı için kritik)
        # Kural: VA ve USA her ikisi 0 veya her ikisi 1 olmalı
        dogrulama_ok = False  # Varsayılan: geçersiz
        
        # Master Algoritma kontrolü (öncelikli)
        if dogrulama.MA == 1:
            logger.warning(f"   ❌ ELENDİ: Master Algoritma aktif (ekstrem koşul)")
            dogrulama_ok = False
        # VU kontrolü (temel gereksinim)
        elif dogrulama.VU == 0:
            logger.warning(f"   ❌ ELENDİ: Veri Uygunluğu başarısız")
            dogrulama_ok = False
        # ⭐⭐⭐ STRICT VA/USA SYNC (LİTERATÜR)
        elif (dogrulama.VA == 0 and dogrulama.USA == 0) or (dogrulama.VA == 1 and dogrulama.USA == 1):
            logger.info(f"   ✅ Doğrulama başarılı: VU={dogrulama.VU}, VA={dogrulama.VA}, USA={dogrulama.USA} (SYNC OK)")
            dogrulama_ok = True
        else:
            logger.warning(f"   ❌ ELENDİ: VA/USA senkronizasyonu bozuk (VA={dogrulama.VA}, USA={dogrulama.USA})")
            dogrulama_ok = False
        
        if not dogrulama_ok:
            logger.warning(f"   ❌ ELENDİ: Çok katmanlı doğrulama başarısız")
            logger.info(f"      VU:{dogrulama.VU}, VA:{dogrulama.VA}, USA:{dogrulama.USA}, MA:{dogrulama.MA}")
            return None
        
        # ----------------------------------------------------------------
        # SİNYAL OLUŞTURMA (Puan >= 9.0)
        # ----------------------------------------------------------------
        logger.info(f"🐛 DEBUG - Puan Kontrolü: {round(puan, 1)} >= 9.0")
        if puan >= 9.0:  # Puan eşiği 9.0'a geri alındı
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
            
            # ----------------------------------------------------------------
            # 🐛 DEBUG: AI Analiz Çağrısı (Grok/Gemini)
            # ----------------------------------------------------------------
            logger.info("🤖 AI analizi isteniyor...")
            logger.info(f"   🔑 Grok API: {'✅' if grok_ai.api_key else '❌'}")
            logger.info(f"   🔑 Gemini API: {len(gemini_ai.api_keys)} key")
            
            ai_analiz, ai_source = await ai_analiz_yap(mac_verisi, session)
            
            if ai_analiz:
                logger.info(f"   ✅ {ai_source} AI yanıtı alındı ({len(ai_analiz)} karakter)")
            else:
                logger.warning(f"   ❌ AI yanıtı alınamadı (None döndü)")
                logger.warning(f"   🔍 Olası nedenler: API key yok, API hatası, timeout, response boş")
            
            # Tavsiye oluştur - Basit ve anlaşılır
            if skor_durum == "OPTIMUM" and 55 <= dk <= 60:
                tavsiye = "⭐ ALTIN FIRSAT: SIRADAKİ GOL"
            else:
                tavsiye = "🎯 SIRADAKİ GOL"
            
            # xG analizi ekle
            if ev_xg > dep_xg + 0.5:
                tavsiye += f"\n💡 Ev sahibi daha baskın (xG: {ev_xg:.1f} vs {dep_xg:.1f})"
            elif dep_xg > ev_xg + 0.5:
                tavsiye += f"\n💡 Deplasman daha baskın (xG: {dep_xg:.1f} vs {ev_xg:.1f})"
            else:
                tavsiye += f"\n💡 Dengeli oyun (xG: {ev_xg:.1f} vs {ev_xg:.1f})"
            
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
                f"✅ Sahte Baskı: {'YOK' if sahte_baski_durum == 'YOK' else f'TESPİT EDİLDİ ({sahte_baski_durum})'}\n"
                f"✅ Doğrulama: VU:{dogrulama.VU} VA:{dogrulama.VA} USA:{dogrulama.USA} MA:{dogrulama.MA}\n"
            )
            
            # AI analizi ekle (Grok veya Gemini)
            if ai_analiz:
                mesaj += f"{'='*30}\n"
                mesaj += f"🤖 **{ai_source} AI:**\n{ai_analiz}\n"
            
            # Asian Handicap bilgisi ekle (zaten puanlamada kullanıldı)
            if asian_handicap:
                mesaj += f"{'='*30}\n"
                mesaj += f"📊 **Asian Handicap (Bonus: {ah_bonus:+.1f}):**\n"
                mesaj += f"• Ev: {asian_handicap['ev_handicap']} (Oran: {asian_handicap['ev_oran']})\n"
                mesaj += f"• Dep: {asian_handicap['dep_handicap']} (Oran: {asian_handicap['dep_oran']})\n"
                
                # Yorum ekle
                if ah_bonus > 0:
                    mesaj += f"• 💎 Değerli çizgi tespit edildi\n"
                elif ah_bonus < 0:
                    mesaj += f"• ⚠️ Tuzak çizgi tespit edildi\n"
            
            mesaj += f"{'='*30}\n"
            mesaj += f"ℹ️ Bu maç Nesine'de oynanıyor"
            
            logger.info(f"✅ SİNYAL OLUŞTURULDU (AI: {'✅' if ai_analiz else '❌'})")
            return mesaj
        else:
            logger.warning(f"   ❌ ELENDİ: Puan yetersiz: {round(puan, 1)} < 9.0")
            logger.info(f"      Puan detayı: Baz=4.0, Zaman={zaman_bonusu}, Skor={'3.0' if skor_durum=='OPTIMUM' else '0'}, SOT={sot_puan}, DA={da_bonus}")
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
                logger.warning(f"  
