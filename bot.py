import asyncio, aiohttp, os, urllib.parse, logging, re, time
from telegram import Bot
from collections import deque

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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================================================================
# ZAMAN KONTROLÜ (Gece 12'den sonra bot kapansın)
# ============================================================================

def gece_kontrol():
    """
    Türkiye saati (UTC+3) ile gece 00:00 - 06:00 arası bot çalışmasın
    """
    # UTC saatini al ve Türkiye saatine çevir (UTC+3)
    utc_saat = time.gmtime()
    turkiye_saat = (utc_saat.tm_hour + 3) % 24
    
    if 0 <= turkiye_saat < 6:
        logger.warning(f"🌙 Gece modu: Saat {turkiye_saat:02d}:00 - Bot uyuyor")
        return True
    
    return False

# ============================================================================
# NESİNE LİG KONTROLÜ
# ============================================================================

def nesine_lig_kontrolu(ev_adi, dep_adi):
    """
    🎯 LİTERATÜR: Regex tabanlı güçlendirilmiş filtre
    Maçın Nesine'de oynanıp oynanmadığını kontrol eder
    E-spor, Reserves, U19/U20 gibi maçları eler
    """
    mac_metni = f"{ev_adi} {dep_adi}".lower()
    
    # 1. U19, U20, U21, U23 regex ile tespit (GÜÇLÜ)
    if re.search(r'\bu\d{2}\b', mac_metni):
        logger.info(f"🚫 Nesine'de yok: U-yaş kategorisi tespit edildi")
        return False
    
    # 2. Reserves regex ile tespit
    if re.search(r'\breserve[s]?\b', mac_metni):
        logger.info(f"🚫 Nesine'de yok: Reserves tespit edildi")
        return False
    
    # 3. E-spor regex ile tespit
    if re.search(r'\be[-\s]?sport[s]?\b', mac_metni):
        logger.info(f"🚫 Nesine'de yok: E-spor tespit edildi")
        return False
    
    # 4. Women/Kadınlar
    if re.search(r'\b(w|women|kadın|kadin)\b', mac_metni):
        logger.info(f"🚫 Nesine'de yok: Kadınlar ligi tespit edildi")
        return False
    
    # 5. Youth/Junior/Academy
    if re.search(r'\b(youth|junior|academy)\b', mac_metni):
        logger.info(f"🚫 Nesine'de yok: Genç takım tespit edildi")
        return False
    
    # 6. Virtual/Simulation
    if re.search(r'\b(virtual|simulation|sim)\b', mac_metni):
        logger.info(f"🚫 Nesine'de yok: Virtual maç tespit edildi")
        return False
    
    # 7. E-spor takım isimleri (parantez içinde)
    esport_takimlar = [
        'kodak', 'kray', 'og', 'hotshot', 'andrew',
        'professor', 'carlos', 'ken', 'jetli',
        'volvo', 'grellz', 'glory', 'grimace',
        'frantsuz', 'nekishka', 'eden', 'boom',
        'force', 'emperor', 'yerema', 'catalyst',
        'pimchik', 'koss', 'fantazer'
    ]
    
    for takim in esport_takimlar:
        if f'({takim})' in mac_metni:
            logger.info(f"🚫 Nesine'de yok: E-spor takımı '({takim})' tespit edildi")
            return False
    
    logger.info(f"✅ Nesine'de oynanıyor")
    return True

# ============================================================================
# ⭐⭐⭐ KRİTİK: ALTIN PENCERE VE SKOR DURUMU FİLTRELERİ
# ============================================================================

def altin_pencere_kontrol(dakika):
    """⭐⭐⭐ Veri seti analizi: 55-60 dakika %100 başarı"""
    if 55 <= dakika <= 60:
        return 4.0, "ALTIN_PENCERE"
    elif 60 < dakika <= 75:
        return 2.0, "GECIS_OYUNU"
    else:
        return 0.0, "NORMAL"

def skor_durumu_kontrol(ev_gol, dep_gol):
    """⭐⭐⭐ Toplam gol >= 5: Kaos bölgesi, Fark >= 3: Rölanti evresi"""
    toplam_gol = ev_gol + dep_gol
    fark = abs(ev_gol - dep_gol)
    
    if toplam_gol >= 5:
        logger.warning(f"❌ Kaos bölgesi: Toplam gol {toplam_gol} >= 5")
        return False, "KAOS_BOLGESI"
    
    if fark >= 3:
        logger.warning(f"❌ Rölanti evresi: Fark {fark} >= 3")
        return False, "ROLANTI_EVRESI"
    
    if (ev_gol == 2 and dep_gol == 1) or (ev_gol == 1 and dep_gol == 2) or \
       (ev_gol == 3 and dep_gol == 1) or (ev_gol == 1 and dep_gol == 3):
        logger.info(f"✅ Optimum skor durumu: {ev_gol}-{dep_gol}")
        return True, "OPTIMUM"
    
    return True, "NORMAL"

def sot_epilasyon_kontrol(sot):
    """⭐⭐⭐ İsabetli şut > 8: Hücum epilasyonu"""
    if sot <= 8:
        return sot * 0.25
    else:
        logger.warning(f"⚠️ Hücum epilasyonu: SOT {sot} > 8")
        return -1.0

def detayli_tavsiye_olustur(v, ev_gol, dep_gol, dk, skor_durum, zaman_durumu):
    """Detaylı gri alan analizi ve tahmin oluşturur"""
    toplam_gol = ev_gol + dep_gol
    
    # Momentum analizi
    ev_momentum = v['ev_da'] + v['ev_sot'] * 2
    dep_momentum = v['dep_da'] + v['dep_sot'] * 2
    toplam_momentum = ev_momentum + dep_momentum
    
    # Baskınlık oranı
    if ev_momentum + dep_momentum > 0:
        ev_baskinlik = ev_momentum / (ev_momentum + dep_momentum)
Date/time: 2026-05-05T21:21:59.963Z
Extension version: 3.53.0
Provider: anthropic
Model: claude-sonnet-4-6

Provider ended the request: API request failed    else:
        ev_baskinlik = 0.5
    
    # Tempo analizi
    if dk > 0:
        tempo = (v['ev_ta'] + v['dep_ta']) / dk
    else:
        tempo = 0
    
    # TAHMİN OLUŞTURMA
    tahminler = []
    
    # 1. Hangi takım gol atacak?
    if ev_baskinlik > 0.65:
        tahminler.append("🏠 **Ev sahibi gol atacak**")
    elif ev_baskinlik < 0.35:
        tahminler.append("✈️ **Deplasman gol atacak**")
    else:
        tahminler.append("⚽ **Gol olacak**")
    
    # 2. Alt/Üst tahminleri
    if toplam_gol == 0:
        if tempo > 2.5 and toplam_momentum > 50:
            tahminler.append("📈 **Üst 0.5 Gol** (Yüksek tempo)")
        tahminler.append("📊 **Üst 1.5 Gol** oynanabilir")
    elif toplam_gol == 1:
        if tempo > 2.0 and toplam_momentum > 40:
            tahminler.append("📈 **Üst 2.5 Gol** oynanabilir")
        else:
            tahminler.append("📊 **Alt 3.5 Gol** güvenli")
    elif toplam_gol == 2:
        if tempo > 2.5:
            tahminler.append("📈 **Üst 3.5 Gol** oynanabilir")
        else:
            tahminler.append("📊 **Alt 4.5 Gol** güvenli")
    elif toplam_gol == 3:
        tahminler.append("📈 **Üst 4.5 Gol** riskli ama oynanabilir")
    elif toplam_gol == 4:
        tahminler.append("⚠️ **Kaos bölgesine yakın, dikkatli olun**")
    
    # 3. Özel durumlar
    if zaman_durumu == "ALTIN_PENCERE":
        tahminler.insert(0, "⭐ **ALTIN PENCERE: Gol olasılığı çok yüksek!**")
    
    if skor_durum == "OPTIMUM":
        tahminler.insert(0, "🎯 **Optimum skor durumu: Gol beklentisi yüksek**")
    
    # GRİ ALAN ANALİZİ
    gri_alan = []
    
    # Tempo
    if tempo > 3.0:
        gri_alan.append("⚡ **Tempo:** Çok hızlı (Gol olasılığı yüksek)")
    elif tempo > 2.0:
        gri_alan.append("🏃 **Tempo:** Hızlı (Dengeli oyun)")
    else:
        gri_alan.append("🐌 **Tempo:** Yavaş (Gol olasılığı düşük)")
    
    # Baskınlık
    if ev_baskinlik > 0.70:
        gri_alan.append(f"💪 **Baskınlık:** Ev sahibi çok baskın (%{int(ev_baskinlik*100)})")
    elif ev_baskinlik < 0.30:
        gri_alan.append(f"💪 **Baskınlık:** Deplasman çok baskın (%{int((1-ev_baskinlik)*100)})")
    else:
        gri_alan.append("⚖️ **Baskınlık:** Dengeli maç")
    
    # Momentum
    if ev_momentum > dep_momentum * 1.5:
        gri_alan.append("📈 **Momentum:** Ev sahibinde")
    elif dep_momentum > ev_momentum * 1.5:
        gri_alan.append("📈 **Momentum:** Deplasmanda")
    else:
        gri_alan.append("↔️ **Momentum:** Dengeli")
    
    # Şut verimliliği
    if v['ev_sot'] + v['dep_sot'] > 0:
        sut_verimlilik = toplam_gol / (v['ev_sot'] + v['dep_sot'])
        if sut_verimlilik > 0.3:
            gri_alan.append(f"🎯 **Şut Verimliliği:** Yüksek (%{int(sut_verimlilik*100)})")
        else:
            gri_alan.append(f"🎯 **Şut Verimliliği:** Düşük (%{int(sut_verimlilik*100)})")
    
    return tahminler, gri_alan

# ============================================================================
# GEMİNİ AI
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
            
            prompt = f"""Futbol maç analizi (Türkçe, MAX 300 karakter):

MAÇ: {mac_verisi['ev_adi']} {mac_verisi['skor']} {mac_verisi['dep_adi']} ({mac_verisi['dakika']}')

İSTATİSTİK:
• TA: {mac_verisi['ta']} (Ev:{mac_verisi['ev_ta']}, Dep:{mac_verisi['dep_ta']})
• DA: {mac_verisi['da']} (Ev:{mac_verisi['ev_da']}, Dep:{mac_verisi['dep_da']})
• SOT: {mac_verisi['sot']} (Ev:{mac_verisi['ev_sot']}, Dep:{mac_verisi['dep_sot']})

DETAYLI ANALİZ:
1. Fiziksel durum (yorgunluk?)
2. Taktik (açık/kapalı?)
3. Hangi takım golü hak ediyor?
4. Sonraki 15 dakika?
5. Özel durum?

KISA CEVAP (MAX 300 KARAKTER):"""
            
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={api_key}"
            
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.7,
                    "maxOutputTokens": 400
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

def guvenli_int(deger, varsayilan=0):
    try:
        if deger == '' or deger is None:
            return varsayilan
        return int(deger)
    except:
        return varsayilan

def veri_cikart(ev_v, dep_v):
    """S-kodlarından veri çıkarır"""
    return {
        'ev_sot': guvenli_int(ev_v.get('S1', 0)),
        'ev_ta': guvenli_int(ev_v.get('S3', 0)),
        'ev_da': guvenli_int(ev_v.get('S4', 0)),
        'ev_gol': guvenli_int(ev_v.get('SC', 0)),
        'dep_sot': guvenli_int(dep_v.get('S1', 0)),
        'dep_ta': guvenli_int(dep_v.get('S3', 0)),
        'dep_da': guvenli_int(dep_v.get('S4', 0)),
        'dep_gol': guvenli_int(dep_v.get('SC', 0))
    }

# ============================================================================
# ANA ANALİZ MOTORU
# ============================================================================

async def mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot, session):
    """Kantitatif analiz motoru"""
    try:
        logger.info(f"🔍 Analiz: {ev_adi} vs {dep_adi} - {dk}'")
        
        # Nesine lig kontrolü
        nesine_de_mi = nesine_lig_kontrolu(ev_adi, dep_adi)
        
        v = veri_cikart(ev_v, dep_v)
        
        sot = v['ev_sot'] + v['dep_sot']
        ta = v['ev_ta'] + v['dep_ta']
        da = v['ev_da'] + v['dep_da']
        ev_gol = v['ev_gol']
        dep_gol = v['dep_gol']
        toplam_gol = ev_gol + dep_gol
        
        logger.info(f"📊 TA:{ta}, DA:{da}, SOT:{sot}, Gol:{toplam_gol}")
        
        # Kritik filtreler
        skor_ok, skor_durum = skor_durumu_kontrol(ev_gol, dep_gol)
        if not skor_ok:
            return None
        
        if not (15 <= dk <= 85):
            logger.debug(f"⏱️ Dakika aralık dışı: {dk}")
            return None
        
        # Dakika anomalisi kontrolü
        if dk < 30 and toplam_gol >= 3:
            logger.warning(f"⚠️ Dakika anomalisi: {dk}' ama {toplam_gol} gol")
            return None
        
        if dk < 45 and (ta > 150 or da > 100):
            logger.warning(f"⚠️ Dakika anomalisi: {dk}' ama TA:{ta}, DA:{da}")
            return None
        
        if dk > 0:
            atak_per_dakika = ta / dk
            if atak_per_dakika > 5.0:
                logger.warning(f"⚠️ Dakika anomalisi: {atak_per_dakika:.1f} atak/dk")
                return None
        
        if ta < da or da < sot or ta < sot:
            logger.warning(f"❌ Fiziksel hiyerarşi ihlali")
            return None
        
        if sot < toplam_gol:
            logger.warning(f"❌ SOT < Gol: {sot} < {toplam_gol}")
            return None
        
        if dk > 0 and sot > (dk * 0.7):
            logger.warning(f"❌ SOT limiti aşıldı")
            return None
        
        # Puanlama
        puan = 4.0
        
        zaman_bonusu, zaman_durumu = altin_pencere_kontrol(dk)
        puan += zaman_bonusu
        if zaman_bonusu > 0:
            logger.info(f"⭐ Zaman bonusu: +{zaman_bonusu}")
        
        if skor_durum == "OPTIMUM":
            puan += 3.0
            logger.info(f"🎯 Optimum skor bonusu: +3.0")
        
        sot_puan = sot_epilasyon_kontrol(sot)
        puan += sot_puan
        logger.info(f"🎯 SOT puanı: {sot_puan}")
        
        da_bonus = min((da // 10) * 0.5, 3.0)
        puan += da_bonus
        logger.info(f"📊 DA bonusu: +{da_bonus}")
        
        logger.info(f"💯 Toplam puan: {round(puan, 1)}")
        
        # Sinyal oluşturma
        if puan >= 7.0:
            tahminler, gri_alan = detayli_tavsiye_olustur(v, ev_gol, dep_gol, dk, skor_durum, zaman_durumu)
            
            mac_verisi = {
                'ev_adi': ev_adi, 'dep_adi': dep_adi, 'skor': skor, 'dakika': dk,
                'ta': ta, 'da': da, 'sot': sot, 'gol': toplam_gol,
                'ev_ta': v['ev_ta'], 'dep_ta': v['dep_ta'],
                'ev_da': v['ev_da'], 'dep_da': v['dep_da'],
                'ev_sot': v['ev_sot'], 'dep_sot': v['dep_sot'],
                'ev_gol': ev_gol, 'dep_gol': dep_gol
            }
            
            logger.info("🤖 Gemini AI analizi isteniyor...")
            ai_analiz = await gemini_ai.analiz_yap(mac_verisi, session)
            
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
                f"🎯 **TAHMİNLER:**\n"
            )
            
            for tahmin in tahminler:
                mesaj += f"{tahmin}\n"
            
            mesaj += f"{'='*30}\n"
            mesaj += f"🔍 **GRİ ALAN ANALİZİ:**\n"
            
            for analiz in gri_alan:
                mesaj += f"{analiz}\n"
            
            if ai_analiz:
                mesaj += f"{'='*30}\n"
                mesaj += f"🤖 **Gemini AI:**\n{ai_analiz}\n"
            
            mesaj += f"{'='*30}\n"
            if nesine_de_mi:
                mesaj += f"ℹ️ Bu maç Nesine'de oynanıyor"
            else:
                mesaj += f"⚠️ Bu maç Nesine'de oynanmıyor"
            
            logger.info(f"✅ SİNYAL OLUŞTURULDU")
            return mesaj
        else:
            logger.info(f"📉 Puan yetersiz: {round(puan, 1)} < 7.0")
            return None
            
    except Exception as e:
        logger.error(f"❌ Analiz hatası: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None

async def mac_isle(bot, mac_id, session):
    """Tek bir maçı işler"""
    try:
        async with session.get(
            f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={mac_id}&stats=1",
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            
            if response.status != 200:
                return None
            
            event_data = await response.json()
            results = event_data.get('results', [])
        
        stats = esnek_liste_duzelt(results)
        
        ev_adi = ""
        dep_adi = ""
        dk = 0
        skor = "0-0"
        ev_v = {}
        dep_v = {}
        
        for item in stats:
            item_type = item.get('type', '')
            
            if item_type == 'EV':
                na = item.get('NA', '')
                if ' v ' in na:
                    parts = na.split(' v ')
                    ev_adi = parts[0].strip()
                    dep_adi = parts[1].strip()
                
                dk = guvenli_int(item.get('TM', 0))
                skor = item.get('SS', '0-0')
                
            elif item_type == 'TE':
                team_id = str(item.get('ID', ''))
                if team_id == '1':
                    ev_v = item
                else:
                    dep_v = item
        
        if not ev_adi or not dep_adi or not ev_v or not dep_v:
            return None
        
        return await mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot, session)
        
    except Exception as e:
        logger.error(f"❌ Maç işleme hatası ({mac_id}): {str(e)}")
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
                "🛡️ **V43.0 COMPLETE FINAL**\n\n"
                "🎯 **Özellikler:**\n"
                "• Altın Pencere: 55-60 dakika\n"
                "• Kaos/Rölanti filtreleri\n"
                "• SOT epilasyon kontrolü\n"
                "• Dakika anomalisi tespiti\n"
                "• Nesine lig filtresi\n"
                "• Gece 00:00-06:00 arası kapalı\n"
                "• Detaylı tahmin + Gri alan\n"
                "• Gemini AI analizi"
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
            logger.info(f"{'
