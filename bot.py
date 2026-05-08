#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tam API Response Kontrolü
BetsAPI'den gelen tüm verileri gösterir (Asian Handicap, Odds, Stats vb.)
"""

import os
import requests
import json
from datetime import datetime

# Token'ı environment variable'dan al
BETSAPI_TOKEN = os.getenv('BETSAPI_TOKEN', '')

if not BETSAPI_TOKEN:
    print("❌ HATA: BETSAPI_TOKEN environment variable tanımlı değil!")
    exit(1)

print("=" * 80)
print("🔍 TAM API RESPONSE KONTROLÜ")
print("=" * 80)
print(f"Token: {BETSAPI_TOKEN[:10]}...{BETSAPI_TOKEN[-5:]}")
print(f"⏰ Zaman: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 80)
print()

# 1. Inplay maçlarını çek
print("📡 Step 1: Inplay maçları çekiliyor...")
inplay_url = f"https://api.betsapi.com/v1/events/inplay?sport_id=1&token={BETSAPI_TOKEN}"

try:
    response = requests.get(inplay_url, timeout=10)
    
    if response.status_code != 200:
        print(f"❌ HTTP {response.status_code}")
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

# 2. İlk maçın FULL detayını göster
if matches:
    first_match = matches[0]
    match_id = first_match.get('id')
    home_team = first_match.get('home', {}).get('name', 'N/A')
    away_team = first_match.get('away', {}).get('name', 'N/A')
    
    print("=" * 80)
    print(f"🏆 İLK MAÇ DETAYI")
    print("=" * 80)
    print(f"Maç: {home_team} vs {away_team}")
    print(f"ID: {match_id}")
    print()
    
    # Event detay çek
    print("📡 Event detay API çağrısı yapılıyor...")
    event_url = f"https://api.betsapi.com/v1/event/view?token={BETSAPI_TOKEN}&event_id={match_id}"
    
    try:
        event_response = requests.get(event_url, timeout=10)
        
        if event_response.status_code != 200:
            print(f"❌ HTTP {event_response.status_code}")
            exit(1)
        
        event_data = event_response.json()
        
        if not event_data.get('success'):
            print(f"❌ API Hatası: {event_data.get('error')}")
            exit(1)
        
        results = event_data.get('results', [])
        if not results:
            print(f"❌ Results boş")
            exit(1)
        
        event_info = results[0]
        
        # Tam response'u JSON dosyasına kaydet
        with open('full_api_response.json', 'w', encoding='utf-8') as f:
            json.dump(event_info, f, indent=2, ensure_ascii=False)
        
        print("✅ Tam response 'full_api_response.json' dosyasına kaydedildi")
        print()
        
        # Ana alanları göster
        print("=" * 80)
        print("📋 ANA ALANLAR")
        print("=" * 80)
        
        for key in event_info.keys():
            print(f"  • {key}")
        
        print()
        
        # Stats detayı
        print("=" * 80)
        print("📊 STATS DETAYI")
        print("=" * 80)
        
        stats = event_info.get('stats', {})
        if stats:
            print(f"Stats tipi: {type(stats)}")
            print(f"Stats keys: {list(stats.keys())}")
            print()
            
            for key, value in stats.items():
                print(f"  • {key}: {value} (tip: {type(value).__name__})")
        else:
            print("❌ Stats yok")
        
        print()
        
        # Odds/Bahis bilgileri
        print("=" * 80)
        print("💰 ODDS/BAHİS BİLGİLERİ")
        print("=" * 80)
        
        # Asian Handicap kontrolü
        asian_handicap_keys = [k for k in event_info.keys() if 'asian' in k.lower() or 'handicap' in k.lower()]
        if asian_handicap_keys:
            print("✅ Asian Handicap alanları bulundu:")
            for key in asian_handicap_keys:
                print(f"  • {key}: {event_info.get(key)}")
        else:
            print("❌ Asian Handicap alanı bulunamadı")
        
        print()
        
        # Odds kontrolü
        odds_keys = [k for k in event_info.keys() if 'odd' in k.lower() or 'bet' in k.lower()]
        if odds_keys:
            print("✅ Odds alanları bulundu:")
            for key in odds_keys:
                value = event_info.get(key)
                if isinstance(value, dict):
                    print(f"  • {key}: {len(value)} item")
                else:
                    print(f"  • {key}: {value}")
        else:
            print("❌ Odds alanı bulunamadı")
        
        print()
        
        # Skor ve timer
        print("=" * 80)
        print("⚽ SKOR VE SÜRE BİLGİLERİ")
        print("=" * 80)
        
        print(f"  • ss (skor): {event_info.get('ss')}")
        print(f"  • scores: {event_info.get('scores')}")
        print(f"  • timer: {event_info.get('timer')}")
        print(f"  • time_status: {event_info.get('time_status')}")
        
        print()
        
        # Takım bilgileri
        print("=" * 80)
        print("👥 TAKIM BİLGİLERİ")
        print("=" * 80)
        
        home = event_info.get('home', {})
        away = event_info.get('away', {})
        
        print(f"Ev Sahibi:")
        for key, value in home.items():
            print(f"  • {key}: {value}")
        
        print()
        print(f"Deplasman:")
        for key, value in away.items():
            print(f"  • {key}: {value}")
        
        print()
        print("=" * 80)
        print("✅ Kontrol tamamlandı")
        print("=" * 80)
        print()
        print("💡 Detaylı inceleme için 'full_api_response.json' dosyasını açın")
        
    except Exception as e:
        print(f"❌ Hata: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

else:
    print("❌ Canlı maç bulunamadı")
