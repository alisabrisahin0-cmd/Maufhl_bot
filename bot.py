#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Detaylı Stats Kontrol Scripti
BetsAPI'den gelen verilerde hangi istatistiklerin olduğunu kontrol eder
"""

import os
import requests
import json
from datetime import datetime

# Token'ı environment variable'dan al
BETSAPI_TOKEN = os.getenv('BETSAPI_TOKEN', '')

if not BETSAPI_TOKEN:
    print("❌ HATA: BETSAPI_TOKEN environment variable tanımlı değil!")
    print("   Kullanım: set BETSAPI_TOKEN=your_token")
    exit(1)

print("=" * 70)
print("🔍 DETAYLI STATS KONTROL SCRIPTI")
print("=" * 70)
print(f"Token: {BETSAPI_TOKEN[:10]}...{BETSAPI_TOKEN[-5:]}")
print(f"⏰ Zaman: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 70)
print()

# 1. Inplay maçlarını çek
print("📡 Step 1: Inplay maçları çekiliyor...")
inplay_url = f"https://api.betsapi.com/v1/events/inplay?sport_id=1&token={BETSAPI_TOKEN}"

try:
    response = requests.get(inplay_url, timeout=10)
    
    if response.status_code != 200:
        print(f"❌ HTTP {response.status_code}")
        print(f"Response: {response.text[:200]}")
        exit(1)
    
    data = response.json()
    
    if not data.get('success'):
        print(f"❌ API Hatası: {data.get('error')}")
        exit(1)
    
    matches = data.get('results', [])
    print(f"✅ {len(matches)} canlı maç bulundu")
    print()
    
except Exception as e:
    print(f"❌ Hata: {e}")
    exit(1)

# 2. İlk 3 maçın detayını çek ve stats'ları kontrol et
print("=" * 70)
print("📊 Step 2: Maç detayları ve stats kontrolü")
print("=" * 70)
print()

stats_found = 0
stats_types = {}

for i, match in enumerate(matches[:5], 1):
    match_id = match.get('id')
    home_team = match.get('home', {}).get('name', 'N/A')
    away_team = match.get('away', {}).get('name', 'N/A')
    
    print(f"🏆 Maç #{i}: {home_team} vs {away_team}")
    print(f"   ID: {match_id}")
    
    # Event detay çek
    event_url = f"https://api.betsapi.com/v1/event/view?token={BETSAPI_TOKEN}&event_id={match_id}"
    
    try:
        event_response = requests.get(event_url, timeout=10)
        
        if event_response.status_code != 200:
            print(f"   ❌ HTTP {event_response.status_code}")
            print()
            continue
        
        event_data = event_response.json()
        
        if not event_data.get('success'):
            print(f"   ❌ API Hatası: {event_data.get('error')}")
            print()
            continue
        
        results = event_data.get('results', [])
        if not results:
            print(f"   ⚠️ Results boş")
            print()
            continue
        
        event_info = results[0]
        stats = event_info.get('stats', {})
        
        if stats:
            stats_found += 1
            print(f"   ✅ Stats bulundu!")
            print(f"   📊 Stats içeriği:")
            
            # Stats yapısını analiz et
            for key, value in stats.items():
                print(f"      • {key}: {value}")
                
                # Stats tiplerini say
                if key not in stats_types:
                    stats_types[key] = 0
                stats_types[key] += 1
            
            # Özel alanları kontrol et
            print()
            print(f"   🔍 Özel Alan Kontrolü:")
            
            # Korner
            corners_home = stats.get('corners', {}).get('home', 0) if isinstance(stats.get('corners'), dict) else 0
            corners_away = stats.get('corners', {}).get('away', 0) if isinstance(stats.get('corners'), dict) else 0
            print(f"      🚩 Korner: Ev {corners_home} - Dep {corners_away}")
            
            # Kartlar
            yellow_home = stats.get('yellowcards', {}).get('home', 0) if isinstance(stats.get('yellowcards'), dict) else 0
            yellow_away = stats.get('yellowcards', {}).get('away', 0) if isinstance(stats.get('yellowcards'), dict) else 0
            print(f"      🟨 Sarı Kart: Ev {yellow_home} - Dep {yellow_away}")
            
            red_home = stats.get('redcards', {}).get('home', 0) if isinstance(stats.get('redcards'), dict) else 0
            red_away = stats.get('redcards', {}).get('away', 0) if isinstance(stats.get('redcards'), dict) else 0
            print(f"      🟥 Kırmızı Kart: Ev {red_home} - Dep {red_away}")
            
            # Ataklar
            attacks_home = stats.get('attacks', {}).get('home', 0) if isinstance(stats.get('attacks'), dict) else 0
            attacks_away = stats.get('attacks', {}).get('away', 0) if isinstance(stats.get('attacks'), dict) else 0
            print(f"      ⚔️ Atak: Ev {attacks_home} - Dep {attacks_away}")
            
            # Tehlikeli Ataklar
            dangerous_home = stats.get('dangerous_attacks', {}).get('home', 0) if isinstance(stats.get('dangerous_attacks'), dict) else 0
            dangerous_away = stats.get('dangerous_attacks', {}).get('away', 0) if isinstance(stats.get('dangerous_attacks'), dict) else 0
            print(f"      🔥 Tehlikeli Atak: Ev {dangerous_home} - Dep {dangerous_away}")
            
            # Şutlar
            shots_home = stats.get('shots_on_target', {}).get('home', 0) if isinstance(stats.get('shots_on_target'), dict) else 0
            shots_away = stats.get('shots_on_target', {}).get('away', 0) if isinstance(stats.get('shots_on_target'), dict) else 0
            print(f"      🎯 İsabetli Şut: Ev {shots_home} - Dep {shots_away}")
            
        else:
            print(f"   ❌ Stats yok")
        
        print()
        
    except Exception as e:
        print(f"   ❌ Hata: {e}")
        print()
        continue

# 3. Özet
print("=" * 70)
print("📊 ÖZET")
print("=" * 70)
print(f"✅ Stats bulunan maç sayısı: {stats_found}/{min(5, len(matches))}")
print()

if stats_types:
    print("📋 Bulunan Stats Tipleri:")
    for stat_type, count in sorted(stats_types.items()):
        print(f"   • {stat_type}: {count} maçta bulundu")
else:
    print("❌ Hiçbir maçta stats verisi bulunamadı!")
    print()
    print("💡 Olası Sebepler:")
    print("   1. Maçlar henüz başlamadı (stats sadece canlı maçlarda)")
    print("   2. Düşük seviye ligler (stats sadece büyük liglerde)")
    print("   3. API subscription stats içermiyor")
    print()
    print("📧 BetsAPI müşteri hizmetlerine sorun:")
    print("   'Which leagues have detailed statistics (corners, cards, attacks)?'")

print()
print("=" * 70)
print("✅ Kontrol tamamlandı")
print("=" * 70)
