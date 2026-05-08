# -*- coding: utf-8 -*-
"""
Event ID Diagnostic Test (Simple Version)
Test event/view endpoint with correct IDs from /events/inplay

Based on BetsAPI support response:
"get the 'id' from /events/inplay and try again"
"""

import os
import sys
import json
import requests
from datetime import datetime

# Fix encoding for Windows console
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

def test_event_ids():
    """Test event/view endpoint with IDs from inplay"""
    
    if not BETSAPI_TOKEN:
        print("❌ BETSAPI_TOKEN environment variable not set")
        return
    
    print("="*70)
    print("🔍 EVENT ID DIAGNOSTIC TEST")
    print("="*70)
    print(f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Step 1: Get inplay matches
    print("📥 Step 1: Fetching inplay matches...")
    print(f"   Endpoint: /v1/events/inplay?sport_id=1")
    print()
    
    try:
        response = requests.get(
            f"https://api.betsapi.com/v1/events/inplay?sport_id=1&token={BETSAPI_TOKEN}",
            timeout=30
        )
        
        if response.status_code != 200:
            print(f"❌ Inplay API error: HTTP {response.status_code}")
            print(f"   Response: {response.text[:500]}")
            return
        
        data = response.json()
        matches = data.get('results', [])
        
        print(f"✅ Found {len(matches)} live matches")
        print()
        
        if not matches:
            print("⚠️ No live matches found. Try again later.")
            return
        
        # Step 2: Extract and display IDs
        print("📋 Step 2: Extracting event IDs from inplay response...")
        print()
        
        test_matches = []
        for idx, match in enumerate(matches[:5]):  # Test first 5 matches
            event_id = match.get('id')
            home_name = match.get('home', {}).get('name', 'N/A') if isinstance(match.get('home'), dict) else 'N/A'
            away_name = match.get('away', {}).get('name', 'N/A') if isinstance(match.get('away'), dict) else 'N/A'
            
            print(f"   Match #{idx+1}:")
            print(f"   • ID: {event_id}")
            print(f"   • Teams: {home_name} vs {away_name}")
            print(f"   • ID Type: {type(event_id).__name__}")
            
            # Check if stats exist in inplay
            has_stats = 'stats' in match and match['stats']
            print(f"   • Has stats in inplay: {'✅' if has_stats else '❌'}")
            
            if has_stats:
                stats = match.get('stats', {})
                home_stats = stats.get('1', {})
                away_stats = stats.get('2', {})
                s_codes_home = [k for k in home_stats.keys() if k.startswith('S')]
                s_codes_away = [k for k in away_stats.keys() if k.startswith('S')]
                print(f"   • S-codes in inplay: Home={len(s_codes_home)}, Away={len(s_codes_away)}")
                if s_codes_home:
                    print(f"   • Sample S-codes: {s_codes_home[:5]}")
            
            print()
            
            if event_id:
                test_matches.append({
                    'id': event_id,
                    'home': home_name,
                    'away': away_name,
                    'has_inplay_stats': has_stats
                })
        
        if not test_matches:
            print("❌ No valid event IDs found")
            return
        
        # Step 3: Test event/view endpoint with extracted IDs
        print("="*70)
        print("🧪 Step 3: Testing /v1/event/view with extracted IDs...")
        print()
        
        success_count = 0
        fail_count = 0
        
        for idx, match_info in enumerate(test_matches[:3]):  # Test first 3
            event_id = match_info['id']
            
            print(f"🔍 Test #{idx+1}: {match_info['home']} vs {match_info['away']}")
            print(f"   Event ID: {event_id}")
            print(f"   Endpoint: /v1/event/view?event_id={event_id}")
            
            try:
                detail_response = requests.get(
                    f"https://api.betsapi.com/v1/event/view?event_id={event_id}&token={BETSAPI_TOKEN}",
                    timeout=15
                )
                
                status = detail_response.status_code
                detail_data = detail_response.json()
                
                print(f"   HTTP Status: {status}")
                
                if status == 200:
                    success_count += 1
                    print(f"   ✅ SUCCESS!")
                    
                    # Check if stats exist in detail response
                    results = detail_data.get('results', [])
                    if results and len(results) > 0:
                        event_detail = results[0]
                        has_stats = 'stats' in event_detail and event_detail['stats']
                        print(f"   • Has stats in detail: {'✅' if has_stats else '❌'}")
                        
                        if has_stats:
                            stats = event_detail.get('stats', {})
                            home_stats = stats.get('1', {})
                            away_stats = stats.get('2', {})
                            s_codes_home = [k for k in home_stats.keys() if k.startswith('S')]
                            s_codes_away = [k for k in away_stats.keys() if k.startswith('S')]
                            print(f"   • S-codes in detail: Home={len(s_codes_home)}, Away={len(s_codes_away)}")
                            
                            # Show sample S-codes
                            if s_codes_home:
                                print(f"   • Sample home S-codes: {s_codes_home[:5]}")
                                # Show values
                                sample_values = {k: home_stats[k] for k in s_codes_home[:3]}
                                print(f"   • Sample values: {sample_values}")
                        
                        # Compare with inplay stats
                        if match_info['has_inplay_stats']:
                            print(f"   • ℹ️ Stats available in BOTH inplay and detail endpoints")
                        else:
                            print(f"   • ℹ️ Stats only in detail endpoint (not in inplay)")
                    else:
                        print(f"   ⚠️ Empty results array")
                    
                else:
                    fail_count += 1
                    print(f"   ❌ FAILED!")
                    print(f"   Response: {json.dumps(detail_data, indent=2)[:500]}")
                
            except Exception as e:
                fail_count += 1
                print(f"   ❌ Exception: {str(e)}")
            
            print()
        
        # Step 4: Summary
        print("="*70)
        print("📊 SUMMARY")
        print("="*70)
        print(f"✅ Successful requests: {success_count}")
        print(f"❌ Failed requests: {fail_count}")
        print()
        
        if success_count > 0:
            print("✅ DIAGNOSIS: Event IDs from /events/inplay work correctly!")
            print("   The event/view endpoint is accessible with proper IDs.")
            print()
            print("💡 RECOMMENDATION:")
            print("   1. Re-enable event detail API calls in mac_isle()")
            print("   2. Use the 'id' field from inplay response")
            print("   3. Stats may be available in both endpoints")
            print()
            print("🔧 NEXT STEPS:")
            print("   • Update bot_v44_literatur_pro.py to use event/view endpoint")
            print("   • Add fallback to inplay stats if event/view fails")
        else:
            print("❌ DIAGNOSIS: Event/view endpoint still has issues")
            print("   Even with correct IDs from inplay")
            print()
            print("💡 RECOMMENDATION:")
            print("   Continue using inplay endpoint only (current approach)")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print()
    test_event_ids()
    print()
