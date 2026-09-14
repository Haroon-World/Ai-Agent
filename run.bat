@echo off
title AI Clinic Agent & WhatsApp Service
echo ========================================================
echo       AI Clinic Receptionist - Startup Manager
echo ========================================================
echo.

set PORT=5001

echo [1/2] Starting Flask Backend Server on port %PORT%...
start "AI Clinic - Flask Server" cmd /k "title AI Clinic Backend Server && set PORT=5001 && python app.py"

echo [2/2] Starting Persistent WhatsApp Tunnel via Ngrok...
echo.
echo Leave this window open to keep the tunnel alive.
echo Press Ctrl+C to stop.
echo.

python -c "from pyngrok import ngrok; t = ngrok.connect(5001, bind_tls=True); print(''); print('========================================================'); print('   LIVE WEBHOOK URL: ' + t.public_url + '/api/whatsapp/webhook'); print('========================================================'); import time; [time.sleep(1) for _ in iter(int, 1)]"

