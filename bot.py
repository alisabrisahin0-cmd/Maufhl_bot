# ==========================================
# BOT UYKU MODU (KILL SWITCH)
# Bu kod botu bitkisel hayata sokar, 0 istek yapar.
# ==========================================

import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Uyku Modunda")

def run_health_check():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

async def ana_dongu():
    threading.Thread(target=run_health_check, daemon=True).start()
    print("💤 BOT TAMAMEN UYKU MODUNA ALINDI. API KULLANIMI DURDURULDU.")
    
    while True:
        await asyncio.sleep(86400) # 24 saat boyunca hiçbir şey yapmadan uyu

if __name__ == "__main__":
    asyncio.run(ana_dongu())
