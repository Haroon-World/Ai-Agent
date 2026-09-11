import os
import sys
from flask import Flask, render_template, redirect, url_for, request, session
from config.config import Config
from models import db, Business, auto_migrate_db
from routes.chat import chat_bp
from routes.appointments import appointments_bp
from routes.admin import admin_bp
from routes.platform import platform_bp
from routes.whatsapp import whatsapp_bp
from seed import seed_database

# Ensure stdout/stderr use UTF-8 on Windows so emoji in LLM responses
# don't crash the dev server with a cp1252 UnicodeEncodeError.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def init_db(app):
    """Ensure database schema exists, missing columns are migrated, and seed data is populated."""
    with app.app_context():
        try:
            db.create_all()
            auto_migrate_db(app)
            seed_database(app)
        except Exception as e:
            print(f"[DB Init Warning]: {e}")

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize SQLAlchemy
    db.init_app(app)

    # Register Blueprints
    app.register_blueprint(chat_bp)
    app.register_blueprint(appointments_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(platform_bp, url_prefix="/platform")
    app.register_blueprint(whatsapp_bp, url_prefix="/api/whatsapp")

    @app.route("/")
    @app.route("/clinic/<int:clinic_id>")
    def index(clinic_id=None):
        target_id = clinic_id or request.args.get("clinic") or request.args.get("business_id")
        business = None
        if target_id and str(target_id).isdigit():
            business = db.session.get(Business, int(target_id))
        elif session.get("business_id"):
            business = db.session.get(Business, session.get("business_id"))
        elif session.get("active_clinic_id"):
            business = db.session.get(Business, session.get("active_clinic_id"))

        if not business:
            business = db.session.get(Business, Config.DEFAULT_BUSINESS_ID)
        return render_template("index.html", business=business)

    init_db(app)

    return app

if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("PORT", 5000))
    debug_mode = os.getenv("FLASK_ENV", "production").lower() == "development"
    print(f"[AI Agent] Server running at: http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
