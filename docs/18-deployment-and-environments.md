# 18 — Deployment and Environments

This document details the configuration, deployment patterns, runtime environments, and networking topologies for ClinicConnect AI across local development, testing, staging, and production.

---

## Environment Matrix Overview

| Dimension | Local Development | Automated Testing | Staging (Planned) | Production (Target) |
|---|---|---|---|---|
| **OS** | Windows / Linux / macOS | CI Container / Local | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS / Managed PaaS |
| **WSGI Server** | Werkzeug Dev Server | In-Memory WSGI Client | Gunicorn (2-4 workers) | Gunicorn + Gevent / Uvicorn |
| **Reverse Proxy** | Ngrok / Cloudflared | None | Nginx | Cloudflare + Nginx / AWS ALB |
| **Database** | SQLite (WAL mode) | SQLite (`:memory:`) | PostgreSQL 15 | Managed PostgreSQL (RDS / Supabase) |
| **LLM Provider** | Mock / Gemini / Groq | Mock Provider | Gemini / Groq | Gemini / Groq + Fallback |
| **STT / TTS** | Mock / Groq / Gemini | Mock Provider | Groq / Gemini | Groq / Gemini |
| **HTTPS** | Via Tunneling Service | Not Required | Let's Encrypt / Certbot | SSL/TLS Termination (Strict) |
| **WhatsApp Access** | Meta Sandbox / Test Number | Mocked Payloads | Dedicated Staging WABA | Verified Production Meta WABA |

---

## 1. Local Development Environment

### Setup Steps
```bash
# 1. Clone repository
git clone <repo-url>
cd "AI Agent"

# 2. Set up Python virtual environment (recommended)
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Initialize configuration
copy .env.example .env

# 5. Start application
python app.py
```

### Windows 1-Click Startup (`run.bat`)
The project includes a unified Windows automation script (`run.bat`) designed for dual-process development:
1. Spawns the Flask backend in a separate terminal process running on port `5001`.
2. Starts a persistent `pyngrok` HTTPS tunnel targeting port `5001`.
3. Outputs the public webhook URL (`https://<ngrok-id>.ngrok-free.app/api/whatsapp/webhook`) ready to paste into the Meta App Dashboard.

### Alternative Tunneling: Cloudflared
A standalone `cloudflared.exe` binary is committed to the repository root:
```bash
.\cloudflared.exe tunnel --url http://127.0.0.1:5001
```
This produces a persistent `trycloudflare.com` tunnel without requiring ngrok accounts or authorization tokens.

---

## 2. Meta WhatsApp Cloud API Setup for Local Testing

To receive real WhatsApp messages during development:
1. Navigate to the [Meta for Developers Console](https://developers.facebook.com/).
2. Create or open an app with the **WhatsApp** product enabled.
3. In **WhatsApp > API Setup**, retrieve:
   - Temporary Access Token -> Set as `WHATSAPP_ACCESS_TOKEN` in `.env`.
   - Phone Number ID -> Set as `WHATSAPP_PHONE_NUMBER_ID` in `.env`.
   - WhatsApp Business Account ID -> Set as `WHATSAPP_WABA_ID` in `.env`.
4. In **WhatsApp > Configuration**:
   - Set **Callback URL** to: `https://<your-tunnel-domain>/api/whatsapp/webhook`.
   - Set **Verify Token** to the exact string configured in `WHATSAPP_WEBHOOK_VERIFY_TOKEN` in `.env`.
   - Click **Verify and Save** (Meta will send a GET challenge request).
   - Subscribe to the `messages` webhook field under Webhook Fields.
5. In **App Settings > Basic**, copy the **App Secret** -> Set as `WHATSAPP_APP_SECRET` in `.env` to enable cryptographic HMAC signature verification.

---

## 3. Production Deployment Architecture (Recommended Target)

```
                            [ Internet ]
                                 │
                                 ▼
                     [ Cloudflare / Route53 ]
                                 │
                     (Strict HTTPS Port 443)
                                 │
                                 ▼
                   [ Nginx Reverse Proxy Server ]
                     - SSL/TLS Termination
                     - Static File Serving (/static/)
                     - Request Buffering & Gzip
                     - Rate Limiting
                                 │
                                 ▼ (Unix Domain Socket / Localhost:5001)
                [ Gunicorn WSGI Application Server ]
                  - 4 Sync/Async Worker Processes
                  - Worker Class: gthread / gevent
                  - Max Requests & Jitter for Leak Prevention
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
      [ PostgreSQL Database ]     [ Redis Cache / Queue ]
        - High-Availability         - Celery Task Queue
        - Connection Pooler (PgBouncer) - Rate Limiting Store
```

### Production Gunicorn Command
```bash
gunicorn \
  --workers=4 \
  --threads=2 \
  --worker-class=gthread \
  --bind=127.0.0.1:5001 \
  --timeout=120 \
  --max-requests=1000 \
  --max-requests-jitter=100 \
  --access-logfile=/var/log/clinicconnect/access.log \
  --error-logfile=/var/log/clinicconnect/error.log \
  "app:create_app()"
```

### Production Nginx Virtual Host Configuration
```nginx
server {
    listen 80;
    server_name api.clinicconnectai.com app.clinicconnectai.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.clinicconnectai.com app.clinicconnectai.com;

    ssl_certificate /etc/letsencrypt/live/clinicconnectai.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/clinicconnectai.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Static Assets bypass WSGI
    location /static/ {
        alias /var/www/clinicconnect/static/;
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }

    # Proxy to Gunicorn
    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 60s;
    }
}
```

---

## 4. Production Readiness Checklist

Before transitioning from local evaluation to production deployment, verify each of the following controls:

- [ ] **Secret Key Replacement:** `SECRET_KEY` generated using `python -c "import secrets; print(secrets.token_hex(32))"` and exported via environment.
- [ ] **Admin Credential Rotation:** Default credentials (`admin` / `admin123` and `clinicconnectaipro` / `@Clinic2026`) replaced with unique, cryptographically strong passwords.
- [ ] **PostgreSQL Migration:** Production database pointed to a managed PostgreSQL cluster via `DATABASE_URL` with connection pooling enabled.
- [ ] **Webhook Signature Enforcement:** `WHATSAPP_APP_SECRET` populated, ensuring unauthenticated or spoofed webhook payloads are rejected with HTTP 403.
- [ ] **Email Configuration:** Production SMTP credentials (`SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`) verified to prevent silent password reset or invitation failures.
- [ ] **Reverse Proxy SSL:** Strict HTTPS enforced with TLS 1.2+ and HSTS headers.
- [ ] **Process Monitoring:** Systemd service unit or Docker restart policies configured for automatic recovery on failure.
- [ ] **Log Centralization:** Application and error logs directed to persistent storage or centralized logging services (Datadog, CloudWatch, Papertrail).

---

*Next: [19-configuration-and-environment-variables.md](./19-configuration-and-environment-variables.md)*
