import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "default-dev-secret-key-12345")
    
    # Database
    db_url = os.getenv("DATABASE_URL", "sqlite:///ai_business_agent.db")
    # Handle postgres:// prefix from legacy hosts if necessary
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # LLM Settings
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock").lower()
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "llama3-70b-8192")
    
    # Multi-tenant Defaults
    DEFAULT_BUSINESS_ID = int(os.getenv("DEFAULT_BUSINESS_ID", "1"))
    BUSINESS_TIMEZONE = os.getenv("BUSINESS_TIMEZONE", "Asia/Karachi")
    
    # Admin Credentials
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
