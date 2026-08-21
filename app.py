import os
import sys
from flask import Flask, render_template, redirect, url_for
from config.config import Config
from models import db, Business
from routes.chat import chat_bp
from routes.appointments import appointments_bp
from routes.admin import admin_bp
from seed import seed_database

# Ensure stdout/stderr use UTF-8 on Windows so emoji in LLM responses
# don't crash the dev server with a cp1252 UnicodeEncodeError.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize SQLAlchemy
    db.init_app(app)

    # Register Blueprints
    app.register_blueprint(chat_bp)
    app.register_blueprint(appointments_bp)
    app.register_blueprint(admin_bp)

    @app.route("/")
    def index():
        business = db.session.get(Business, Config.DEFAULT_BUSINESS_ID)
        return render_template("index.html", business=business)

    with app.app_context():
        db.create_all()
        seed_database(app)

    return app

if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("PORT", 5000))
    debug_mode = os.getenv("FLASK_ENV", "production").lower() == "development"
    print(f"[AI Agent] Server running at: http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
