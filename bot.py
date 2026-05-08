"""
🧪 Inplay Stats Test Script
Bu script inplay endpoint'inden gelen maçlarda stats olup olmadığını kontrol eder.
"""

import requests
import os
import json

BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

print("=" * 70)
print("🧪 INPLAY STATS TEST")
print("=" * 70)
print(f"Token: {BETSAPI_TOKEN[:10]}...{BETSAPI_TOKEN[-5:]}" if BETSAPI_TOKEN else "❌ Token bulunamadı!")
print("=" * 70)

# Inplay endpoint'ini çağır
url = f"https://api.betsapi.com/v1/events/inplay?sport_id=1&token={BETSAPI_TOKEN}"

print(f"\n📡 API Çağrısı yapılıyor...")
print(f"URL: {url[:80]}...")

try:
    response = requests.get(url, timeout=10)
    
    if response.status != 200:
        print(f"❌ API Hatası: HTTP {response.status}")
        exit(1)
    
    data = response.json()
    matches = data.get('results', [])
    
    print(f"\n✅ API Başarılı: {len(matches)} maç bulundu\n")
    
    # İstatistikleri kontrol et
    stats_var = 0
    stats_yok = 0
    
    print("=" * 70)
    print("📊 MAÇLARDA STATS KONTROLÜ")
    print("=" * 70)
    
    for idx, mac in enumerate(matches[:10], 1):  # İlk 10 maçı kontrol et
        mac_id = mac.get('id', 'N/A')
        ev_adi = mac.get('home', {}).get('name', 'N/A') if isinstance(mac.get('home'), dict) else 'N/A'
        dep_adi = mac.get('away', {}).get('name', 'N/A') if isinstance(mac.get('away'), dict) else 'N/A'
        
        print(f"\n[{idx}] {ev_adi} vs {dep_adi}")
        print(f"    ID: {mac_id}")
        
        # Stats kontrolü
        stats_data = mac.get('stats', {})
        
        if stats_data and isinstance(stats_data, dict):
            ev_stats = stats_data.get('1', {})
            dep_stats = stats_data.get('2', {})
            
            # S-kodlarını say
            ev_s_codes = [k for k in ev_stats.keys() if k.startswith('S')]
            dep_s_codes = [k for k in dep_stats.keys() if k.startswith('S')]
            
            if ev_s_codes and dep_s_codes:
                print(f"    ✅ STATS VAR")
                print(f"       Ev S-kodları: {ev_s_codes}")
                print(f"       Dep S-kodları: {dep_s_codes}")
                print(f"       Örnek değerler:")
                for s_code in ev_s_codes[:3]:
                    print(f"         {s_code}: Ev={ev_stats.get(s_code)}, Dep={dep_stats.get(s_code)}")
                stats_var += 1
            else:
                print(f"    ⚠️ Stats dict var ama S-kodları yok")
                print(f"       Ev keys: {list(ev_stats.keys())}")
                print(f"       Dep keys: {list(dep_stats.keys())}")
                stats_yok += 1
        else:
            print(f"    ❌ STATS YOK")
            print(f"       Mevcut keys: {list(mac.keys())}")
            stats_yok += 1
    
    # Özet
    print("\n" + "=" * 70)
    print("📊 ÖZET")
    print("=" * 70)
    print(f"✅ Stats var: {stats_var}/{min(10, len(matches))}")
    print(f"❌ Stats yok: {stats_yok}/{min(10, len(matches))}")
    
    if stats_var == 0:
        print("\n⚠️ UYARI: Hiçbir maçta stats verisi yok!")
        print("\n💡 Olası Sebepler:")
        print("   1. Soccer API subscription'ı stats verisi içermiyor")
        print("   2. Inplay endpoint'i stats döndürmüyor")
        print("   3. Stats sadece belirli maçlarda mevcut")
        print("\n🔧 Çözüm Önerileri:")
        print("   1. BetsAPI dashboard'unda subscription detaylarını kontrol edin")
        print("   2. Event detay endpoint'i için yetki isteyin")
        print("   3. Farklı bir API plan'ına geçin")
    elif stats_var < stats_yok:
        print("\n⚠️ UYARI: Çoğu maçta stats verisi yok!")
        print(f"   Bot sadece {stats_var} maçı işleyebilir.")
    else:
        print("\n✅ Çoğu maçta stats verisi var, bot çalışabilir!")
    
    # Örnek maç verisini kaydet
    if matches:
        print("\n" + "=" * 70)
        print("📄 ÖRNEK MAÇ VERİSİ (ilk maç)")
        print("=" * 70)
        with open('sample_inplay_match.json', 'w', encoding='utf-8') as f:
            json.dump(matches[0], f, indent=2, ensure_ascii=False)
        print("✅ Kaydedildi: sample_inplay_match.json")

except Exception as e:
    print(f"\n❌ Hata: {str(e)}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
