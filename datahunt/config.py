import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

class Settings:
    APP_NAME: str = "DataHunt"
    
    # Gemini LLM Settings
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "auto")
    GEMINI_LITE_MODEL: str = os.getenv("GEMINI_LITE_MODEL", "gemini-flash-lite-latest")
    GEMINI_FLASH_MODEL: str = os.getenv("GEMINI_FLASH_MODEL", "gemini-flash-latest")
    GEMINI_MAX_OUTPUT_TOKENS: int = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "4096"))
    GEMINI_TEMPERATURE: float = float(os.getenv("GEMINI_TEMPERATURE", "0.1"))
    
    # Storage & Exports
    DATABASE_PATH: Path = Path(os.getenv("DATABASE_PATH", "./data/datahunt.sqlite3"))
    EXPORT_DIR: Path = Path(os.getenv("EXPORT_DIR", "./exports"))
    
    # Run Limits & Budgets
    MAX_RUN_SECONDS: int = int(os.getenv("MAX_RUN_SECONDS", "600"))
    MAX_SEARCH_QUERIES: int = int(os.getenv("MAX_SEARCH_QUERIES", "30"))
    MAX_PAGES_FETCHED: int = int(os.getenv("MAX_PAGES_FETCHED", "120"))
    MAX_BROWSER_PAGES: int = int(os.getenv("MAX_BROWSER_PAGES", "8"))
    MAX_RESPONSE_BYTES: int = int(os.getenv("MAX_RESPONSE_BYTES", "2000000"))
    MAX_RECORDS: int = int(os.getenv("MAX_RECORDS", "500"))
    MAX_TOOL_STEPS: int = int(os.getenv("MAX_TOOL_STEPS", "30"))
    
    # Fetch Settings
    CONNECT_TIMEOUT_SECONDS: float = float(os.getenv("CONNECT_TIMEOUT_SECONDS", "5.0"))
    READ_TIMEOUT_SECONDS: float = float(os.getenv("READ_TIMEOUT_SECONDS", "20.0"))
    TOTAL_FETCH_TIMEOUT_SECONDS: float = float(os.getenv("TOTAL_FETCH_TIMEOUT_SECONDS", "30.0"))
    MAX_REDIRECTS: int = int(os.getenv("MAX_REDIRECTS", "5"))
    MAX_EXTRACTED_CHARS: int = int(os.getenv("MAX_EXTRACTED_CHARS", "30000"))
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # LangSmith Observability
    LANGSMITH_API_KEY: str = os.getenv("LANGSMITH_API_KEY", os.getenv("LANGCHAIN_API_KEY", ""))
    LANGSMITH_PROJECT: str = os.getenv("LANGSMITH_PROJECT", os.getenv("LANGCHAIN_PROJECT", "DataHunt"))
    LANGSMITH_TRACING: bool = os.getenv("LANGSMITH_TRACING", os.getenv("LANGCHAIN_TRACING_V2", "true")).lower() in ("true", "1", "yes")

    # API Security
    API_KEY: str = os.getenv("DATAHUNT_API_KEY", os.getenv("API_KEY", ""))

    def __init__(self):
        # Ensure directories exist
        if self.DATABASE_PATH.parent:
            self.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.EXPORT_DIR.mkdir(parents=True, exist_ok=True)

        # Propagate LangSmith env vars if configured
        if self.LANGSMITH_API_KEY:
            os.environ["LANGSMITH_API_KEY"] = self.LANGSMITH_API_KEY
            os.environ["LANGCHAIN_API_KEY"] = self.LANGSMITH_API_KEY
            os.environ["LANGCHAIN_TRACING_V2"] = "true" if self.LANGSMITH_TRACING else "false"
            os.environ["LANGSMITH_TRACING"] = "true" if self.LANGSMITH_TRACING else "false"
            os.environ["LANGCHAIN_PROJECT"] = self.LANGSMITH_PROJECT
            os.environ["LANGSMITH_PROJECT"] = self.LANGSMITH_PROJECT

    @property
    def is_gemini_configured(self) -> bool:
        return bool(self.GEMINI_API_KEY and self.GEMINI_API_KEY.strip())

    @property
    def is_langsmith_configured(self) -> bool:
        return bool(self.LANGSMITH_API_KEY and self.LANGSMITH_API_KEY.strip() and self.LANGSMITH_TRACING)

    def masked_gemini_key(self) -> str:
        key = self.GEMINI_API_KEY.strip()
        if not key:
            return "<unset>"
        if len(key) <= 8:
            return "***"
        return f"{key[:4]}...{key[-4:]}"

    def masked_langsmith_key(self) -> str:
        key = self.LANGSMITH_API_KEY.strip()
        if not key:
            return "<unset>"
        if len(key) <= 12:
            return "***"
        return f"{key[:8]}...{key[-6:]}"

    def to_safe_dict(self) -> dict:
        return {
            "app_name": self.APP_NAME,
            "gemini_model": self.GEMINI_MODEL,
            "gemini_configured": self.is_gemini_configured,
            "gemini_key_masked": self.masked_gemini_key(),
            "langsmith_configured": self.is_langsmith_configured,
            "langsmith_project": self.LANGSMITH_PROJECT,
            "langsmith_key_masked": self.masked_langsmith_key(),
            "database_path": str(self.DATABASE_PATH),
            "export_dir": str(self.EXPORT_DIR),
            "max_run_seconds": self.MAX_RUN_SECONDS,
            "max_search_queries": self.MAX_SEARCH_QUERIES,
            "max_pages_fetched": self.MAX_PAGES_FETCHED,
            "max_response_bytes": self.MAX_RESPONSE_BYTES,
            "max_records": self.MAX_RECORDS,
            "log_level": self.LOG_LEVEL,
        }

settings = Settings()
