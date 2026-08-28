import os
from dotenv import load_dotenv

load_dotenv()

basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "default-dev-secret-key-12345")
    
    # Database
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url or db_url == "sqlite:///ai_business_agent.db":
        instance_dir = os.path.join(basedir, "instance")
        os.makedirs(instance_dir, exist_ok=True)
        db_path = os.path.join(instance_dir, "ai_business_agent.db")
        db_url = f"sqlite:///{db_path}"
    elif db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
        
    SQLALCHEMY_DATABASE_URI = db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    @staticmethod
    def _clean_key(val: str, prefix: str = "") -> str:
        if not val:
            return ""
        s = val.strip().strip("'").strip('"')
        if prefix and s.startswith(prefix + "="):
            s = s[len(prefix) + 1:].strip().strip("'").strip('"')
        return s

    # LLM Settings
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock").lower()
    GEMINI_API_KEY = _clean_key(os.getenv("GEMINI_API_KEY", ""), "GEMINI_API_KEY")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

    GROQ_API_KEY = _clean_key(os.getenv("GROQ_API_KEY", ""), "GROQ_API_KEY")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    
    # STT & TTS Settings
    STT_PROVIDER = os.getenv("STT_PROVIDER", "mock").lower()
    GROQ_STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3")
    TTS_PROVIDER = os.getenv("TTS_PROVIDER", "gemini").lower()
    GROQ_TTS_MODEL = os.getenv("GROQ_TTS_MODEL", "playai-tts")
    GROQ_TTS_VOICE = os.getenv("GROQ_TTS_VOICE", "Fritz-PlayAI")
    
    # Multi-tenant Defaults
    DEFAULT_BUSINESS_ID = int(os.getenv("DEFAULT_BUSINESS_ID", "1"))
    BUSINESS_TIMEZONE = os.getenv("BUSINESS_TIMEZONE", "Asia/Karachi")
    
    # Admin Credentials
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
