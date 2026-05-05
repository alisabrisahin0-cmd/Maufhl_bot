import asyncio, aiohttp, os, urllib.parse, logging
from telegram import Bot
from collections import deque

# ============================================================================
# KONFIGÜRASYON
# ============================================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

bildirim_gonderilen = deque(maxlen=1000)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================================================================
# ADAPTİF S-KOD TESPİT SİSTEMİ
# ============================================================================

class SKodHaritasi:
    """
    S-kodlarını otomatik tespit eden ve değişikliklere uyum sağlayan sistem
    """
    def __init__(self):
        # Varsayılan eşleştirme (gerçek veriden öğrenildi)
        self.sot_kod = 'S1'
        self.ta_kod = 'S3'
        self.da_kod = 'S4'
        self.gol_kod = 'SC'
        
        # Öğrenme sayacı
        self.ogrenme_sayaci = 0
        self.basarili_eslesme = 0
        
        logger.info("🧠 Adaptif S-Kod sistemi başlatıldı")
    
    def s_kodlarini_tespit_et(self, ev_v, dep_v, skor):
        """
        Fiziksel kuralları kullanarak S-kodlarını otomatik tespit eder
        
        Kurallar:
        1. Gol sayısı = SC (skordan biliniyor)
        2. En büyük değer = TA (Toplam Atak)
        3. 2. büyük değer = DA (Tehlikeli Atak)
        4. SOT >= Gol olmalı ve en küçük değerlerden biri
        5. TA >= DA >= SOT (Fiziksel hiyerarşi)
        """
        try:
            # Gol sayısını skordan çıkar
            try:
                ev_gol, dep_gol = map(int, skor.split('-'))
                toplam_gol = ev_gol + dep_gol
            except:
                toplam_gol = 0
            
            # Tüm S-kodlarını topla (boş olmayanlar)
            s_kodlari = {}
            for kod in ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8']:
                ev_val = self._guvenli_int(ev_v.get(kod, 0))
                dep_val = self._guvenli_int(dep_v.get(kod, 0))
                toplam = ev_val + dep_val
                
                if toplam > 0:  # Sadece dolu olanları al
                    s_kodlari[kod] = toplam
            
            if len(s_kodlari) < 3:
                logger.warning("⚠️ Yetersiz S-kodu verisi")
                return None
            
            # Büyükten küçüğe sırala
            sirali = sorted(s_kodlari.items(), key=lambda x: x[1], reverse=True)
            
            # TA = En büyük değer
            ta_aday = sirali[0]
            
            # DA = 2. büyük değer
            da_aday = sirali[1] if len(sirali) > 1 else None
            
            # SOT = Gol sayısından büyük olan en küçük değer
            sot_aday = None
            for kod, deger in reversed(sirali):  # Küçükten büyüğe
                if deger >= toplam_gol:
                    sot_aday = (kod, deger)
                    break
            
            # Fiziksel hiyerarşi kontrolü
            if ta_aday and da_aday and sot_aday:
                ta_val = ta_aday[1]
                da_val = da_aday[1]
                sot_val = sot_aday[1]
                
                if ta_val >= da_val >= sot_val:
                    # Başarılı tespit!
                    yeni_ta = ta_aday[0]
                    yeni_da = da_aday[0]
                    yeni_sot = sot_aday[0]
                    
                    # Eşleştirme değişti mi?
                    if (yeni_ta != self.ta_kod or 
                        yeni_da != self.da_kod or 
                        yeni_sot != self.sot_kod):
                        
                        logger.warning(f"🔄 S-KOD DEĞİŞİKLİĞİ TESPİT EDİLDİ!")
                        logger.warning(f"Eski: TA={self.ta_kod}, DA={self.da_kod}, SOT={self.sot_kod}")
                        logger.warning(f"Yeni: TA={yeni_ta}, DA={yeni_da}, SOT={yeni_sot}")
                        
                        self.ta_kod = yeni_ta
                        self.da_kod = yeni_da
                        self.sot_kod = yeni_sot
                        
                        return {
                            'degisti': True,
                            'ta_kod': yeni_ta,
                            'da_kod': yeni_da,
                            'sot_kod': yeni_sot,
                            'ta': ta_val,
                            'da': da_val,
                            'sot': sot_val
                        }
                    
                    self.basarili_eslesme += 1
                    return {
                        'degisti': False,
                        'ta_kod': self.ta_kod,
                        'da_kod': self.da_kod,
                        'sot_kod': self.sot_kod,
                        'ta': ta_val,
                        'da': da_val,
                        'sot': sot_val
                    }
            
            logger.warning("⚠️ Fiziksel hiyerarşi uyumsuz")
            return None
            
        except Exception as e:
            logger.error(f"❌ S-kod tespit hatası: {str(e)}")
            return None
    
    def _guvenli_int(self, deger):
        """String'i int'e çevirir"""
        try:
            if deger == '' or deger is None:
                return 0
            return int(deger)
        except:
            return 0
    
    def veri_cikart(self, ev_v, dep_v):
        """Mevcut eşleştirmeyi kullanarak veri çıkarır"""
        ev_sot = self._guvenli_int(ev_v.get(self.sot_kod, 0))
        ev_ta = self._guvenli_int(ev_v.get(self.ta_kod, 0))
        ev_da = self._guvenli_int(ev_v.get(self.da_kod, 0))
        ev_gol = self._guvenli_int(ev_v.get(self.gol_kod, 0))
        
        dep_sot = self._guvenli_int(dep_v.get(self.sot_kod, 0))
        dep_ta = self._guvenli_int(dep_v.get(self.ta_kod, 0))
        dep_da = self._guvenli_int(dep_v.get(self.da_kod, 0))
        dep_gol = self._guvenli_int(dep_v.get(self.gol_kod, 0))
        
        return {
            'sot': ev_sot + dep_sot,
            'ta': ev_ta + dep_ta,
            'da': ev_da + dep_da,
            'gol': ev_gol + dep_gol
        }

# Global S-Kod haritası
s_kod_haritasi = SKodHaritasi()

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

# ============================================================================
# ANA ANALİZ MOTORU
# ============================================================================

async def mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot):
    """Maç verilerini analiz eder ve sinyal oluşturur"""
    try:
        logger.info(f"🔍 Analiz: {ev_adi} vs {dep_adi} - {dk}'")
        
        # ----------------------------------------------------------------
        # ADAPTİF S-KOD TESPİTİ
        # ----------------------------------------------------------------
        tespit = s_kod_haritasi.s_kodlarini_tespit_et(ev_v, dep_v, skor)
        
        if tespit and tespit['degisti']:
            # S-kod değişikliği tespit edildi, Telegram'a bildir
            await bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"🔄 **S-KOD DEĞİŞİKLİĞİ TESPİT EDİLDİ!**\n\n"
                    f"Yeni Eşleştirme:\n"
                    f"• TA = {tespit['ta_kod']}\n"
                    f"• DA = {tespit['da_kod']}\n"
                    f"• SOT = {tespit['sot_kod']}\n\n"
                    f"Sistem otomatik olarak yeni eşleştirmeye geçti."
                )
            )
        
        # Veriyi çıkar
        veri = s_kod_haritasi.veri_cikart(ev_v, dep_v)
        sot = veri['sot']
        ta = veri['ta']
        da = veri['da']
        toplam_gol = veri['gol']
        
        logger.info(f"📊 TA:{ta}, DA:{da}, SOT:{sot}, Gol:{toplam_gol}")
        
        # ----------------------------------------------------------------
        # FİZİKSEL KONTROLLER
        # ----------------------------------------------------------------
        if ta == 0 and da == 0 and sot == 0:
            logger.warning("❌ Tüm istatistikler sıfır")
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
        
        if ta > 0 and (da / ta) > 0.80:
            logger.warning(f"⚠️ DA/TA oranı yüksek: {da}/{ta}")
        
        # ----------------------------------------------------------------
        # PUANLAMA
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
        # SİNYAL OLUŞTURMA
        # ----------------------------------------------------------------
        if puan >= 7.0:
            link = f"https://www.nesine.com/iddaa/arama?text={urllib.parse.quote(ev_adi)}"
            mesaj = (
                f"💎 **SİNYAL (Puan: {round(puan,1)})**\n"
                f"⚽ {ev_adi} {skor} {dep_adi}\n"
                f"⏱ Dakika: {dk}\n"
                f"{'='*30}\n"
                f"📊 **İstatistikler:**\n"
                f"• Toplam Atak: {ta}\n"
                f"• Tehlikeli Atak: {da}\n"
                f"• İsabetli Şut: {sot}\n"
                f"• Gol: {toplam_gol}\n"
                f"{'='*30}\n"
                f"🧠 S-Kod: TA={s_kod_haritasi.ta_kod}, DA={s_kod_haritasi.da_kod}, SOT={s_kod_haritasi.sot_kod}\n"
                f"🔗 [Nesine'de Aç]({link})"
            )
            logger.info(f"✅ SİNYAL OLUŞTURULDU!")
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
                
                dk = int(str(item.get('TM', 0)) or 0)
                skor = item.get('SS', '0-0')
                
            elif item_type == 'TE':
                team_id = str(item.get('ID', ''))
                if team_id == '1':
                    ev_v = item
                else:
                    dep_v = item
        
        if not ev_adi or not dep_adi or not ev_v or not dep_v:
            return None
        
        if not (15 <= dk <= 85):
            return None
        
        return await mac_analiz_et(ev_v, dep_v, ev_adi, dep_adi, skor, dk, bot)
        
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
                "🛡️ **V38.0 ADAPTİF S-KOD SİSTEMİ**\n\n"
                "🧠 **Yeni Özellik: Otomatik S-Kod Tespiti**\n"
                "• API S-kodlarını değiştirirse sistem otomatik tespit eder\n"
                "• Fiziksel kuralları kullanarak doğru eşleştirme yapar\n"
                "• Değişiklik olduğunda sizi bilgilendirir\n\n"
                "✅ Tüm güvenlik kontrolleri aktif\n"
                "✅ Railway stable\n"
                "✅ Bellek optimizasyonu yapıldı"
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
                logger.info(f"📊 Başarılı eşleşme sayısı: {s_kod_haritasi.basarili_eslesme}")
                
            except Exception as e:
                logger.error(f"❌ Ana döngü hatası: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
            
            logger.info("⏳ 60 saniye bekleniyor...\n")
            await asyncio.sleep(60)

if __name__ == "__main__":
    logger.info("🚀 V38.0 Adaptif Bot Başlatılıyor...")
    logger.info(f"📍 Telegram Token: {'✅' if TELEGRAM_TOKEN else '❌'}")
    logger.info(f"📍 Chat ID: {'✅' if CHAT_ID else '❌'}")
    logger.info(f"📍 BetsAPI Token: {'✅' if BETSAPI_TOKEN else '❌'}")
    
    try:
        asyncio.run(ana_dongu())
    except KeyboardInterrupt:
        logger.info("⚠️ Bot kullanıcı tarafından durduruldu")
    except Exception as e:
        logger.error(f"❌ Kritik hata: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
