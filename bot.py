import asyncio, aiohttp, os, urllib.parse, logging, json
from telegram import Bot
from collections import deque
from datetime import datetime

# ============================================================================
# KONFIGÜRASYON
# ============================================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

# Gemini AI API Keys (3 adet)
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
# GEMİNİ AI ENTEGRASYONu (3 API Rotasyonu)
# ============================================================================

class GeminiAIAnalyzer:
    """
    Gemini AI ile maç analizi ve tahmin sistemi
    3 API key rotasyonu ile rate limit aşımını önler
    """
    def __init__(self):
        self.api_keys = [
            GEMINI_API_KEY_1,
            GEMINI_API_KEY_2,
            GEMINI_API_KEY_3
        ]
        self.current_key_index = 0
        self.api_call_count = 0
        
        # Aktif API keylerini filtrele
        self.api_keys = [key for key in self.api_keys if key]
        
        if not self.api_keys:
            logger.warning("⚠️ Gemini API key bulunamadı, AI analizi devre dışı")
        else:
            logger.info(f"✅ {len(self.api_keys)} Gemini API key yüklendi")
    
    def _get_next_api_key(self):
        """API keylerini rotasyonla döndürür"""
        if not self.api_keys:
            return None
        
        key = self.api_keys[self.current_key_index]
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        return key
    
    async def analiz_yap(self, mac_verisi, session):
        """
        Gemini AI'dan maç analizi ve tahmin alır
        
        Gri Alan Analizi:
        - Maç temposu
        - Takım motivasyonu
        - Momentum değişimi
        - Olası sonuç tahmini
        """
        if not self.api_keys:
            return None
        
        try:
            api_key = self._get_next_api_key()
            self.api_call_count += 1
            
            # Gemini AI için prompt hazırla
            prompt = self._hazirla_prompt(mac_verisi)
            
            # Gemini API çağrısı
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={api_key}"
            
            payload = {
                "contents": [{
                    "parts": [{
                        "text": prompt
                    }]
                }],
                "generationConfig": {
                    "temperature": 0.7,
                    "maxOutputTokens": 500
                }
            }
            
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                
                if response.status != 200:
                    logger.error(f"❌ Gemini API hatası: HTTP {response.status}")
                    return None
                
                data = await response.json()
                
                # Yanıtı parse et
                if 'candidates' in data and len(data['candidates']) > 0:
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    logger.info(f"🤖 Gemini AI yanıtı alındı ({len(text)} karakter)")
                    return text
                
                return None
                
        except asyncio.TimeoutError:
            logger.error("⏱️ Gemini AI timeout")
            return None
        except Exception as e:
            logger.error(f"❌ Gemini AI hatası: {str(e)}")
            return None
    
    def _hazirla_prompt(self, mac_verisi):
        """Gemini AI için detaylı prompt hazırlar"""
        return f"""
Futbol maç analizi yap ve tahmin ver.

MAÇ BİLGİLERİ:
• Ev Sahibi: {mac_verisi['ev_adi']}
• Deplasman: {mac_verisi['dep_adi']}
• Skor: {mac_verisi['skor']}
• Dakika: {mac_verisi['dakika']}'

İSTATİSTİKLER:
• Toplam Atak: {mac_verisi['ta']} (Ev: {mac_verisi['ev_ta']}, Dep: {mac_verisi['dep_ta']})
• Tehlikeli Atak: {mac_verisi['da']} (Ev: {mac_verisi['ev_da']}, Dep: {mac_verisi['dep_da']})
• İsabetli Şut: {mac_verisi['sot']} (Ev: {mac_verisi['ev_sot']}, Dep: {mac_verisi['dep_sot']})
• Gol: {mac_verisi['gol']} (Ev: {mac_verisi['ev_gol']}, Dep: {mac_verisi['dep_gol']})

GRİ ALAN ANALİZİ YAP:
1. Maç Temposu: Hızlı mı, yavaş mı?
2. Hangi takım baskın?
3. Momentum: Hangi takımda?
4. Gol Olasılığı: Sonraki 15 dakikada gol gelir mi?
5. Tavsiye: Bu maça bahis oynanır mı?

KISA VE NET CEVAP VER (MAX 300 KARAKTER):
"""

# Global Gemini AI instance
gemini_ai = GeminiAIAnalyzer()

# ============================================================================
# EKSİKSİZ VERİ KONTROL SİSTEMİ
# ============================================================================

class VeriKontrolSistemi:
    """
    API'den gelen verilerin eksiksiz olduğunu kontrol eder
    Eksik veri varsa işlemi durdurur
    """
    
    @staticmethod
    def kontrol_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk):
        """
        Tüm kritik verilerin varlığını kontrol eder
        """
        hatalar = []
        
        # Takım isimleri kontrolü
        if not ev_adi or ev_adi.strip() == "":
            hatalar.append("Ev sahibi adı eksik")
        
        if not dep_adi or dep_adi.strip() == "":
            hatalar.append("Deplasman adı eksik")
        
        # Skor kontrolü
        if not skor or '-' not in skor:
            hatalar.append("Skor formatı hatalı")
        
        # Dakika kontrolü
        if dk <= 0 or dk > 120:
            hatalar.append(f"Dakika değeri anormal: {dk}")
        
        # S-kodları kontrolü
        gerekli_kodlar = ['S1', 'S3', 'S4', 'SC']
        
        for kod in gerekli_kodlar:
            if kod not in ev_v or ev_v[kod] == '' or ev_v[kod] is None:
                hatalar.append(f"Ev sahibi {kod} eksik")
            
            if kod not in dep_v or dep_v[kod] == '' or dep_v[kod] is None:
                hatalar.append(f"Deplasman {kod} eksik")
        
        # Hata varsa logla ve False döndür
        if hatalar:
            logger.warning(f"⚠️ Veri eksiklikleri tespit edildi:")
            for hata in hatalar:
                logger.warning(f"  • {hata}")
            return False, hatalar
        
        logger.info("✅ Tüm veriler eksiksiz")
        return True, []

veri_kontrol = VeriKontrolSistemi()

# ============================================================================
# S-KOD EŞLEŞTİRME (Sabit - Güvenilir)
# ============================================================================

def guvenli_int(deger, varsayilan=0):
    """String'i int'e çevirir"""
    try:
        if deger == '' or deger is None:
            return varsayilan
        return int(deger)
    except:
        return varsayilan

def veri_cikart(ev_v, dep_v):
    """S-kodlarından veri çıkarır (Sabit eşleştirme)"""
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

def esnek_liste_duzelt(veri):
    duz = []
    if isinstance(veri, list):
        for e in veri: 
            duz.extend(esnek_liste_duzelt(e))
    elif isinstance(veri, dict): 
        duz.append(veri)
    return duz

async def mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot, session):
    """
    Eksiksiz veri kontrolü + Gemini AI analizi + Sinyal oluşturma
    """
    try:
        logger.info(f"🔍 Analiz: {ev_adi} vs {dep_adi} - {dk}'")
        
        # ----------------------------------------------------------------
        # 1. EKSİKSİZ VERİ KONTROLÜ
        # ----------------------------------------------------------------
        veri_tamam, hatalar = veri_kontrol.kontrol_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk)
        
        if not veri_tamam:
            logger.error(f"❌ Veri eksik, işlem iptal edildi")
            return None
        
        # ----------------------------------------------------------------
        # 2. VERİ ÇIKARMA
        # ----------------------------------------------------------------
        v = veri_cikart(ev_v, dep_v)
        
        sot = v['ev_sot'] + v['dep_sot']
        ta = v['ev_ta'] + v['dep_ta']
        da = v['ev_da'] + v['dep_da']
        gol = v['ev_gol'] + v['dep_gol']
        
        logger.info(f"📊 TA:{ta}, DA:{da}, SOT:{sot}, Gol:{gol}")
        
        # ----------------------------------------------------------------
        # 3. FİZİKSEL KONTROLLER
        # ----------------------------------------------------------------
        if ta == 0 and da == 0 and sot == 0:
            logger.warning("❌ Tüm istatistikler sıfır")
            return None
        
        if ta < da or da < sot or ta < sot:
            logger.warning(f"❌ Fiziksel hiyerarşi ihlali: TA:{ta}, DA:{da}, SOT:{sot}")
            return None
        
        if sot < gol:
            logger.warning(f"❌ SOT < Gol: {sot} < {gol}")
            return None
        
        if dk > 0 and sot > (dk * 0.7):
            logger.warning(f"❌ SOT limiti aşıldı: {sot} > {dk * 0.7}")
            return None
        
        # ----------------------------------------------------------------
        # 4. PUANLAMA
        # ----------------------------------------------------------------
        puan = 4.0
        
        if skor in ["0-0", "1-1", "1-0", "0-1"]:
            puan += 3.0
        elif skor in ["2-0", "0-2", "2-1", "1-2", "2-2"]:
            puan += 1.5
        
        puan += min((da // 10) * 0.5, 3.0)
        puan += min((sot // 2) * 0.5, 2.0)
        
        if 15 <= dk <= 30 or 60 <= dk <= 75:
            puan += 0.5
        
        logger.info(f"💯 Toplam puan: {round(puan, 1)}")
        
        # ----------------------------------------------------------------
        # 5. SİNYAL OLUŞTURMA (Puan >= 7.0)
        # ----------------------------------------------------------------
        if puan >= 7.0:
            # Gemini AI analizi al
            mac_verisi = {
                'ev_adi': ev_adi,
                'dep_adi': dep_adi,
                'skor': skor,
                'dakika': dk,
                'ta': ta,
                'da': da,
                'sot': sot,
                'gol': gol,
                'ev_ta': v['ev_ta'],
                'dep_ta': v['dep_ta'],
                'ev_da': v['ev_da'],
                'dep_da': v['dep_da'],
                'ev_sot': v['ev_sot'],
                'dep_sot': v['dep_sot'],
                'ev_gol': v['ev_gol'],
                'dep_gol': v['dep_gol']
            }
            
            logger.info("🤖 Gemini AI analizi isteniyor...")
            ai_analiz = await gemini_ai.analiz_yap(mac_verisi, session)
            
            link = f"https://www.nesine.com/iddaa/arama?text={urllib.parse.quote(ev_adi)}"
            
            mesaj = (
                f"💎 **SİNYAL (Puan: {round(puan,1)})**\n"
                f"⚽ {ev_adi} {skor} {dep_adi}\n"
                f"⏱ Dakika: {dk}'\n"
                f"{'='*30}\n"
                f"📊 **İstatistikler:**\n"
                f"• Toplam Atak: {ta} (Ev:{v['ev_ta']}, Dep:{v['dep_ta']})\n"
                f"• Tehlikeli Atak: {da} (Ev:{v['ev_da']}, Dep:{v['dep_da']})\n"
                f"• İsabetli Şut: {sot} (Ev:{v['ev_sot']}, Dep:{v['dep_sot']})\n"
                f"• Gol: {gol} (Ev:{v['ev_gol']}, Dep:{v['dep_gol']})\n"
            )
            
            if ai_analiz:
                mesaj += f"{'='*30}\n"
                mesaj += f"🤖 **Gemini AI Analizi:**\n"
                mesaj += f"{ai_analiz}\n"
            
            mesaj += f"{'='*30}\n"
            mesaj += f"🔗 [Nesine'de Aç]({link})"
            
            logger.info(f"✅ SİNYAL OLUŞTURULDU (AI: {'✅' if ai_analiz else '❌'})")
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
        
        if not (15 <= dk <= 85):
            return None
        
        return await mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot, session)
        
    except Exception as e:
        logger.error(f"❌ Maç işleme hatası ({mac_id}): {str(e)}")
        return None

# ============================================================================
# ANA DÖNGÜ
# ============================================================================

async def ana_dongu():
    """Railway için optimize edilmiş ana döngü"""
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        logger.info("🤖 Bot başlatılıyor...")
        
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "🛡️ **V39.0 GEMİNİ AI ENTEGRE**\n\n"
                "🤖 **Yeni Özellikler:**\n"
                "• Gemini AI ile gri alan analizi\n"
                "• 3 API key rotasyonu (rate limit koruması)\n"
                "• Eksiksiz veri kontrolü\n"
                "• Maç tahmini ve momentum analizi\n\n"
                "✅ Tüm güvenlik kontrolleri aktif\n"
                "✅ Railway stable\n"
                f"✅ Gemini AI: {len(gemini_ai.api_keys)} key yüklü"
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
                async with session.get(
                    f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1",
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
                    
                    logger.info(f"🔍 Maç #{idx+1}/{len(matches)}: {mac_id}")
                    
                    mesaj = await mac_isle(bot, mac_id, session)
                    
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
                
            except Exception as e:
                logger.error(f"❌ Ana döngü hatası: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
            
            logger.info("⏳ 60 saniye bekleniyor...\n")
            await asyncio.sleep(60)

if __name__ == "__main__":
    logger.info("🚀 V39.0 Gemini AI Bot Başlatılıyor...")
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
