#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Event Odds Endpoint Test
BetsAPI /event/odds endpoint'ini test eder - Asian Handicap kontrolü
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
print("🎲 EVENT ODDS ENDPOINT TEST - ASIAN HANDICAP KONTROLÜ")
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

# 2. İlk maçın odds bilgisini çek
if matches:
    first_match = matches[0]
    match_id = first_match.get('id')
    home_team = first_match.get('home', {}).get('name', 'N/A')
    away_team = first_match.get('away', {}).get('name', 'N/A')
    
    print("=" * 80)
    print(f"🏆 TEST MAÇI")
    print("=" * 80)
    print(f"Maç: {home_team} vs {away_team}")
    print(f"ID: {match_id}")
    print()
    
    # Event odds çek
    print("📡 /event/odds endpoint'i test ediliyor...")
    odds_url = f"https://api.betsapi.com/v1/event/odds?token={BETSAPI_TOKEN}&event_id={match_id}"
    
    try:
        odds_response = requests.get(odds_url, timeout=10)
        
        print(f"HTTP Status: {odds_response.status_code}")
        
        if odds_response.status_code != 200:
            print(f"❌ HTTP {odds_response.status_code}")
            print(f"Response: {odds_response.text[:500]}")
            exit(1)
        
        odds_data = odds_response.json()
        
        # Tam response'u kaydet
        with open('event_odds_response.json', 'w', encoding='utf-8') as f:
            json.dump(odds_data, f, indent=2, ensure_ascii=False)
        
        print("✅ Tam response 'event_odds_response.json' dosyasına kaydedildi")
        print()
        
        if not odds_data.get('success'):
            print(f"❌ API Hatası: {odds_data.get('error')}")
            print(f"Error Detail: {odds_data.get('error_detail')}")
            exit(1)
        
        # Ana yapıyı göster
        print("=" * 80)
        print("📋 ODDS RESPONSE YAPISI")
        print("=" * 80)
        
        print(f"Ana keys: {list(odds_data.keys())}")
        print()
        
        results = odds_data.get('results', {})
        print(f"Results tipi: {type(results)}")
        print(f"Results keys: {list(results.keys()) if isinstance(results, dict) else 'N/A'}")
        print()
        
        # Asian Handicap kontrolü
        print("=" * 80)
        print("🎯 ASIAN HANDICAP KONTROLÜ")
        print("=" * 80)
        
        asian_handicap_found = False
        
        # Tüm keys'lerde asian handicap ara
        for key in results.keys() if isinstance(results, dict) else []:
            if 'asian' in key.lower() or 'handicap' in key.lower() or 'ah' in key.lower():
                asian_handicap_found = True
                print(f"✅ Asian Handicap bulundu: {key}")
                
                ah_data = results.get(key)
                print(f"   Tip: {type(ah_data)}")
                
                if isinstance(ah_data, dict):
                    print(f"   Keys: {list(ah_data.keys())[:10]}")  # İlk 10 key
                elif isinstance(ah_data, list):
                    print(f"   Liste uzunluğu: {len(ah_data)}")
                    if ah_data:
                        print(f"   İlk item: {ah_data[0]}")
                else:
                    print(f"   Değer: {ah_data}")
                print()
        
        if not asian_handicap_found:
            print("❌ Asian Handicap alanı bulunamadı")
            print()
            print("📋 Mevcut odds tipleri:")
            for key in list(results.keys())[:20]:  # İlk 20 key
                print(f"   • {key}")
        
        print()
        
        # Diğer bahis tipleri
        print("=" * 80)
        print("💰 DİĞER BAHİS TİPLERİ")
        print("=" * 80)
        
        # 1X2
        if '1_1' in results or 'home_away' in results or '1x2' in results:
            print("✅ 1X2 (Maç Sonucu) bulundu")
        
        # Over/Under
        ou_keys = [k for k in results.keys() if 'over' in k.lower() or 'under' in k.lower() or 'ou' in k.lower()]
        if ou_keys:
            print(f"✅ Over/Under bulundu: {ou_keys[:3]}")
        
        # Corners
        corner_keys = [k for k in results.keys() if 'corner' in k.lower()]
        if corner_keys:
            print(f"✅ Korner bahisleri bulundu: {corner_keys[:3]}")
        
        # Goals
        goal_keys = [k for k in results.keys() if 'goal' in k.lower()]
        if goal_keys:
            print(f"✅ Gol bahisleri bulundu: {goal_keys[:3]}")
        
        print()
        print("=" * 80)
        print("✅ Test tamamlandı")
        print("=" * 80)
        print()
        print("💡 Detaylı inceleme için 'event_odds_response.json' dosyasını açın")
        
    except Exception as e:
        print(f"❌ Hata: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

else:
    print("❌ Canlı maç bulunamadı")
