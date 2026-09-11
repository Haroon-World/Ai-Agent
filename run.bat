@echo off
title AI Clinic Agent & WhatsApp Service
echo ========================================================
echo       AI Clinic Receptionist - Startup Manager
echo ========================================================
echo.

set PORT=5001

echo [1/2] Starting Flask Backend Server on port %PORT%...
start "AI Clinic - Flask Server" cmd /k "title AI Clinic Backend Server && set PORT=5001 && python app.py"

echo [2/2] Starting Persistent WhatsApp Tunnel (Subdomain: dental-ai-care-2026)...
echo Permanent Webhook URL: https://dental-ai-care-2026.loca.lt/api/whatsapp/webhook
echo.
echo Leave this window open to keep the tunnel alive.
echo Press Ctrl+C to stop.
echo.

:tunnel_loop
echo [%date% %time%] Connecting tunnel...
call npx --yes localtunnel --port 5001 --subdomain dental-ai-care-2026
echo Tunnel disconnected. Reconnecting in 3 seconds...
timeout /t 3 /nobreak >nul
goto tunnel_loop
