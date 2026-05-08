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
        return int(deger)
    except:
        return varsayilan

class VeriKorumaKatmani:
    """
    🛡️ Veri Koruma Katmanı
    
    S-kodlarını dinamik tespit eder ve fiziksel hiyerarşiyi doğrular
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
    🎯 LİTERATÜR: Altın Pencereler
    - 24-36 dk: İlk yarı olgunlaşma (taktik oturdu, ciddi ataklar)
    - 48-58 dk: İkinci yarı kırılma (yüksek enerji, savunma organize değil)
    """
    if 24 <= dakika <= 36:
        return 3.0, "ALTIN_PENCERE_1"  # İlk yarı olgunlaşma
    elif 48 <= dakika <= 58:
        return 4.0, "ALTIN_PENCERE_2"  # İkinci yarı kırılma (en güçlü)
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
    🎯 Çok Katmanlı Doğrulama Sistemi
    VU (Veri Uygunluğu), VA (Veri Anomalisi), USA (Uzun Süreli Anomali)
    """
    def __init__(self):
        self.VU = 0  # Veri Uygunluğu
        self.VA = 0  # Veri Anomalisi
        self.USA = 0  # Uzun Süreli Anomali
    
    def sinyal_uret(self):
        """
        Sinyal üretim mantığı:
        - VA=0 ve USA=1 ise: False (Uzun süreli anomali var)
        - VU=1 ve diğer kombinasyonlar: True
        """
        if self.VA == 0 and self.USA == 1:
            logger.warning("❌ Çok katmanlı doğrulama: VA=0 ve USA=1")
            return False
        if self.VU == 1 and ((self.VA == 0 and self.USA == 0) or (self.VA == 1 and self.USA == 1) or (self.VA == 1 and self.USA == 0)):
            logger.info("✅ Çok katmanlı doğrulama: Sinyal onaylandı")
            return True
        logger.warning("❌ Çok katmanlı doğrulama: Sinyal reddedildi")
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

async def mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot, session):
    """
    Kantitatif analiz motoru (V44 - Faz 1):
    1. Altın pencere kontrolü (55-60 dakika)
    2. Skor durumu kontrolü (kaos/rölanti)
    3. SOT epilasyon kontrolü
    4. Fiziksel hiyerarşi kontrolü
    5. ⭐ YENİ: Oyun durumu normalizasyonu
    6. ⭐ YENİ: Korner tuzağı kontrolü
    7. ⭐ YENİ: Çok katmanlı doğrulama
    8. Gemini AI analizi
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
        
        # 4. ⭐ YENİ: Korner Tuzağı Kontrolü (S2 verisi varsa)
        ev_korner = v.get('ev_korner', 0)
        dep_korner = v.get('dep_korner', 0)
        if ev_korner > 0 or dep_korner > 0:
            if not korner_tuzagi_kontrolu(ev_korner, dep_korner, v['ev_sot'], v['dep_sot']):
                logger.warning(f"❌ Korner tuzağı tespit edildi")
                return None
        
        # 5. Fiziksel hiyerarşi kontrolü
        if ta < da or da < sot or ta < sot:
            logger.warning(f"❌ Fiziksel hiyerarşi ihlali: TA:{ta}, DA:{da}, SOT:{sot}")
            return None
        
        # 6. Gol vs SOT kontrolü
        if sot < toplam_gol:
            logger.warning(f"❌ SOT < Gol: {sot} < {toplam_gol}")
            return None
        
        # 7. Dakika başı şut limiti
        if dk > 0 and sot > (dk * 0.7):
            logger.warning(f"❌ SOT limiti aşıldı: {sot} > {dk * 0.7}")
            return None
        
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
        # ⭐ YENİ: ÇOK KATMANLI DOĞRULAMA
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
            
            # Tavsiye oluştur
            if skor_durum == "OPTIMUM" and 55 <= dk <= 60:
                tavsiye = "⭐ ALTIN FIRSAT: SIRADAKİ GOL"
            elif ev_gol > dep_gol:
                tavsiye = "📈 Ev sahibi baskın, gol beklentisi yüksek"
            elif dep_gol > ev_gol:
                tavsiye = "📈 Deplasman baskın, gol beklentisi yüksek"
            else:
                tavsiye = "⚖️ Dengeli maç, her iki takım da gol atabilir"
            
            mesaj = (
                f"💎 **SİNYAL (Puan: {round(puan,1)})**\n"
                f"⚽ {ev_adi} {skor} {dep_adi}\n"
                f"⏱ Dakika: {dk}'\n"
                f"{'='*30}\n"
                f"📊 **İstatistikler:**\n"
                f"• Toplam Atak: {ta} (Ev:{v['ev_ta']}, Dep:{v['dep_ta']})\n"
                f"• Tehlikeli Atak: {da} (Ev:{v['ev_da']}, Dep:{v['dep_da']})\n"
                f"• İsabetli Şut: {sot} (Ev:{v['ev_sot']}, Dep:{v['dep_sot']})\n"
                f"• Gol: {toplam_gol} (Ev:{ev_gol}, Dep:{dep_gol})\n"
                f"{'='*30}\n"
                f"💡 **Tavsiye:** {tavsiye}\n"
            )
            
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
    Tek bir maçı işler - INPLAY VERİSİYLE ÇALIŞIR
    
    ⚠️ NOT: Event detay endpoint'i PERMISSION_DENIED döndüğü için
    sadece inplay endpoint'inden gelen verilerle çalışıyoruz.
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
        
        # İstatistikler - Inplay endpoint'inde stats varsa kullan
        stats_data = mac_data.get('stats', {})
        
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
        logger.info(f"   Ev stats: {ev_stats_count} S-kod, Dep stats: {dep_stats_count} S-kod")
        
        return await mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot, session)
        
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
                "🚀 **BOT V44 LİTERATÜR PRO - INPLAY MODE**\n\n"
                "⚠️ **ÖNEMLI:** Bot sadece inplay endpoint'iyle çalışıyor\n"
                "   (Event detay endpoint PERMISSION_DENIED)\n\n"
                "🎯 **Özellikler:**\n"
                "• ✅ Regex Filtreler: U19/Reserves/E-spor güçlü tespit\n"
                "• ✅ Altın Pencere: 24-36 dk (İlk yarı) + 48-58 dk (İkinci yarı)\n"
                "• ✅ Sezgisel Gemini AI: Kontra atak riski, 'ama' diyebilen\n"
                "• ✅ Kaos/Rölanti Filtreleri: Toplam gol < 5, Fark < 3\n"
                "• ✅ SOT Kontrolü: SOT <= 8\n"
                "• ✅ Oyun Durumu Normalizasyonu (skor farkı bazlı)\n"
                "• ✅ Korner Tuzağı Kontrolü (sahte baskı tespiti)\n"
                "• ✅ Çok Katmanlı Doğrulama (VU/VA/USA)\n"
                "• 🛡️ Veri Koruma Katmanı (S-kod dinamik tespit)\n"
                "• 🛡️ Fiziksel Hiyerarşi Doğrulama (TA>=DA>=SOT>=Gol)\n"
                "• 🛡️ Akıllı S-kod Adaptasyonu\n\n"
                "📊 **Hedef Başarı:** %85-90\n"
                "🎯 **Minimum Eşik:** 9.0\n"
                "🤖 **AI:** Temperature 0.9 (Yaratıcı)\n"
                "🛡️ **Veri Koruma:** Aktif\n"
                "📡 **Veri Kaynağı:** Inplay endpoint only\n\n"
                "⚠️ **NOT:** İstatistikler inplay verisinden alınıyor.\n"
                "Eğer maçlarda istatistik yoksa, bot o maçları atlayacak."
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
