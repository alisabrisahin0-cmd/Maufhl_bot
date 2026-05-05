import asyncio, aiohttp, os, json
from datetime import datetime

"""
🔍 BetsAPI Veri Keşif Aracı

Bu script:
1. Canlı bir maç bulur
2. Tüm S-kodlarını ve değerlerini gösterir
3. Gerçek karşılıklarını manuel eşleştirmenize yardımcı olur
"""

BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

async def kesif():
    print("="*70)
    print("🔍 BetsAPI Veri Yapısı Keşif Aracı")
    print("="*70)
    
    async with aiohttp.ClientSession() as session:
        # 1. Canlı maçları al
        print("\n📡 Canlı maçlar alınıyor...")
        async with session.get(
            f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1"
        ) as r:
            data = await r.json()
            matches = data.get('results', [])
        
        print(f"✅ {len(matches)} canlı maç bulundu\n")
        
        if not matches:
            print("❌ Canlı maç yok, daha sonra tekrar deneyin")
            return
        
        # 2. İlk maçın detaylarını al
        mac = matches[0] if isinstance(matches[0], dict) else matches[0][0]
        mac_id = str(mac.get('id') or mac.get('FI', ''))
        
        print(f"🎯 Analiz edilen maç ID: {mac_id}\n")
        
        async with session.get(
            f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={mac_id}&stats=1"
        ) as r:
            event_data = await r.json()
        
        # 3. Veri yapısını analiz et
        results = event_data.get('results', [])
        
        ev_adi = ""
        dep_adi = ""
        dk = 0
        skor = "0-0"
        ev_stats = {}
        dep_stats = {}
        
        for item in results:
            if isinstance(item, list):
                for sub in item:
                    if sub.get('type') == 'EV':
                        na = sub.get('NA', '')
                        if ' v ' in na:
                            ev_adi, dep_adi = na.split(' v ')
                        dk = int(str(sub.get('TM', 0)) or 0)
                        skor = sub.get('SS', '0-0')
                    elif sub.get('type') == 'TE':
                        if str(sub.get('ID')) == '1':
                            ev_stats = sub
                        else:
                            dep_stats = sub
            else:
                if item.get('type') == 'EV':
                    na = item.get('NA', '')
                    if ' v ' in na:
                        ev_adi, dep_adi = na.split(' v ')
                    dk = int(str(item.get('TM', 0)) or 0)
                    skor = item.get('SS', '0-0')
                elif item.get('type') == 'TE':
                    if str(item.get('ID')) == '1':
                        ev_stats = item
                    else:
                        dep_stats = item
        
        # 4. Sonuçları göster
        print("="*70)
        print(f"⚽ MAÇ BİLGİLERİ")
        print("="*70)
        print(f"Ev Sahibi: {ev_adi}")
        print(f"Deplasman: {dep_adi}")
        print(f"Skor: {skor}")
        print(f"Dakika: {dk}")
        
        print("\n" + "="*70)
        print(f"📊 EV SAHİBİ İSTATİSTİKLERİ")
        print("="*70)
        
        ev_s_kodlari = {k: v for k, v in ev_stats.items() if k.startswith('S')}
        for kod, deger in sorted(ev_s_kodlari.items()):
            print(f"{kod}: {deger}")
        
        print("\n" + "="*70)
        print(f"📊 DEPLASMAN İSTATİSTİKLERİ")
        print("="*70)
        
        dep_s_kodlari = {k: v for k, v in dep_stats.items() if k.startswith('S')}
        for kod, deger in sorted(dep_s_kodlari.items()):
            print(f"{kod}: {deger}")
        
        print("\n" + "="*70)
        print(f"🔍 ANALİZ")
        print("="*70)
        
        # 100 kuralı kontrolü
        print("\n🎯 100 Kuralı Kontrolü (Possession tespiti):")
        for kod in ev_s_kodlari.keys():
            if kod in dep_s_kodlari:
                toplam = int(ev_s_kodlari[kod]) + int(dep_s_kodlari[kod])
                if 98 <= toplam <= 102:
                    print(f"  ✅ {kod}: {ev_s_kodlari[kod]} + {dep_s_kodlari[kod]} = {toplam} → POSSESSION")
        
        # Büyüklük sıralaması
        print("\n📈 Büyüklük Sıralaması (Ev + Deplasman):")
        toplam_degerler = {}
        for kod in ev_s_kodlari.keys():
            if kod in dep_s_kodlari:
                toplam = int(ev_s_kodlari[kod]) + int(dep_s_kodlari[kod])
                toplam_degerler[kod] = toplam
        
        sirali = sorted(toplam_degerler.items(), key=lambda x: x[1], reverse=True)
        for idx, (kod, deger) in enumerate(sirali[:5], 1):
            print(f"  {idx}. {kod}: {deger}")
        
        # Gol kontrolü
        print("\n⚽ Gol Analizi:")
        ev_gol, dep_gol = map(int, skor.split('-'))
        print(f"  Ev Gol: {ev_gol}")
        print(f"  Deplasman Gol: {ev_gol}")
        print(f"  Toplam Gol: {ev_gol + dep_gol}")
        
        # Ham veriyi kaydet
        print("\n💾 Ham veri 'api_debug.json' dosyasına kaydedildi")
        with open('api_debug.json', 'w', encoding='utf-8') as f:
            json.dump({
                'mac_bilgileri': {
                    'ev': ev_adi,
                    'deplasman': dep_adi,
                    'skor': skor,
                    'dakika': dk
                },
                'ev_istatistikler': ev_stats,
                'deplasman_istatistikler': dep_stats,
                'tam_api_yaniti': event_data
            }, f, indent=2, ensure_ascii=False)
        
        print("\n" + "="*70)
        print("✅ Keşif tamamlandı!")
        print("="*70)
        print("\n📝 SONRAKİ ADIMLAR:")
        print("1. Yukarıdaki S-kodlarını flashscore.com'dan kontrol edin")
        print("2. Hangi kodun ne anlama geldiğini belirleyin:")
        print("   - Toplam Atak (Total Attacks)")
        print("   - Tehlikeli Atak (Dangerous Attacks)")
        print("   - İsabetli Şut (Shots on Target)")
        print("   - Kaleci Kurtarışı (Saves)")
        print("   - Korner (Corners)")
        print("   - vb.")
        print("3. Eşleştirmeyi kod içine hardcode edin")

if __name__ == "__main__":
    asyncio.run(kesif())
