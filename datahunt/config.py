import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

class Settings:
    APP_NAME: str = "DataHunt"
    
    # LLM Provider Configuration ("nvidia" or "gemini")
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "nvidia")

    # NVIDIA NIM Settings (Free Preview endpoints up to 40 RPM)
    NVIDIA_API_KEY: str = os.getenv("NVIDIA_API_KEY", "")
    NVIDIA_BASE_URL: str = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    NVIDIA_PRIMARY_MODEL: str = os.getenv("NVIDIA_PRIMARY_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
    NVIDIA_FALLBACK_MODEL: str = os.getenv("NVIDIA_FALLBACK_MODEL", "mistralai/mistral-large-4-1t-a52b")
    NVIDIA_MAX_OUTPUT_TOKENS: int = int(os.getenv("NVIDIA_MAX_OUTPUT_TOKENS", "2048"))
    NVIDIA_TEMPERATURE: float = float(os.getenv("NVIDIA_TEMPERATURE", "0.1"))
    NVIDIA_RPM: int = int(os.getenv("NVIDIA_RPM", "40"))          # max NVIDIA API calls per minute
    SEARCH_BATCH_SIZE: int = int(os.getenv("SEARCH_BATCH_SIZE", "20"))  # search tasks dispatched in parallel per iteration

    # Optional FreeHire public job-catalogue integration
    FREEHIRE_ENABLED: bool = os.getenv("FREEHIRE_ENABLED", "false").lower() in ("true", "1", "yes")
    FREEHIRE_API_URL: str = os.getenv("FREEHIRE_API_URL", "https://freehire.me/api/v1")
    FREEHIRE_API_KEY: str = os.getenv("FREEHIRE_API_KEY", "")
    FREEHIRE_MAX_REQUESTS_PER_RUN: int = int(os.getenv("FREEHIRE_MAX_REQUESTS_PER_RUN", "20"))

    # Brave Search API (free tier — 2000 req/month; fallback when DDG rate-limits)
    BRAVE_API_KEY: str = os.getenv("BRAVE_API_KEY", "")
    # Serper.dev — Google Search API (2500 free queries/month, no CC)
    SERPER_API_KEY: str = os.getenv("SERPER_API_KEY", "")
    # Tavily Search — AI-optimized (1000 free queries/month, no CC)
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
    # SearXNG Metasearch (Self-hosted aggregator — Google/Bing/DDG, no API keys, no rate limits)
    SEARXNG_URL: str = os.getenv("SEARXNG_URL", "").rstrip("/")
    # Primary Search Provider Override ("auto", "serper", "tavily", "brave", "searxng", "ddg")
    PRIMARY_SEARCH_PROVIDER: str = os.getenv("PRIMARY_SEARCH_PROVIDER", "auto").lower()
    # Direct ATS Ingestion Enabled (Query public Greenhouse/Lever/Ashby APIs directly)
    DIRECT_ATS_ENABLED: bool = os.getenv("DIRECT_ATS_ENABLED", "true").lower() in ("true", "1", "yes")

    # Gemini LLM Settings (kept intact for fallback / alternate provider)
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
    MAX_RUN_SECONDS: int = int(os.getenv("MAX_RUN_SECONDS", "1800"))
    DRAIN_TIMEOUT_SECONDS: int = int(os.getenv("DRAIN_TIMEOUT_SECONDS", "600"))
    MAX_SEARCH_QUERIES: int = int(os.getenv("MAX_SEARCH_QUERIES", "150"))
    MAX_PAGES_FETCHED: int = int(os.getenv("MAX_PAGES_FETCHED", "300"))
    MAX_BROWSER_PAGES: int = int(os.getenv("MAX_BROWSER_PAGES", "12"))
    MAX_RESPONSE_BYTES: int = int(os.getenv("MAX_RESPONSE_BYTES", "5000000"))
    MAX_RECORDS: int = int(os.getenv("MAX_RECORDS", "500"))
    MAX_TOOL_STEPS: int = int(os.getenv("MAX_TOOL_STEPS", "50"))
    MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "200"))
    MAX_CONCURRENT_RUNS: int = int(os.getenv("MAX_CONCURRENT_RUNS", "2"))
    
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

    # Run Defaults
    DEFAULT_FRESHNESS: str = os.getenv("DEFAULT_FRESHNESS", "0sec")
    DEFAULT_COLLECTION_MODE: str = os.getenv("DEFAULT_COLLECTION_MODE", "thorough")

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
    def is_nvidia_configured(self) -> bool:
        return bool(self.NVIDIA_API_KEY and self.NVIDIA_API_KEY.strip())

    @property
    def is_gemini_configured(self) -> bool:
        return bool(self.GEMINI_API_KEY and self.GEMINI_API_KEY.strip())

    @property
    def is_langsmith_configured(self) -> bool:
        return bool(self.LANGSMITH_API_KEY and self.LANGSMITH_API_KEY.strip() and self.LANGSMITH_TRACING)

    def masked_nvidia_key(self) -> str:
        key = self.NVIDIA_API_KEY.strip()
        if not key:
            return "<unset>"
        if len(key) <= 8:
            return "***"
        return f"{key[:4]}...{key[-4:]}"

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

    def masked_serper_key(self) -> str:
        key = self.SERPER_API_KEY.strip()
        if not key:
            return ""
        if len(key) <= 8:
            return "***"
        return f"{key[:4]}...{key[-4:]}"

    def masked_brave_key(self) -> str:
        key = self.BRAVE_API_KEY.strip()
        if not key:
            return ""
        if len(key) <= 8:
            return "***"
        return f"{key[:4]}...{key[-4:]}"

    def masked_tavily_key(self) -> str:
        key = self.TAVILY_API_KEY.strip()
        if not key:
            return ""
        if len(key) <= 8:
            return "***"
        return f"{key[:4]}...{key[-4:]}"

    def to_safe_dict(self) -> dict:
        return {
            "app_name": self.APP_NAME,
            "llm_provider": self.LLM_PROVIDER,
            "nvidia_primary_model": self.NVIDIA_PRIMARY_MODEL,
            "nvidia_fallback_model": self.NVIDIA_FALLBACK_MODEL,
            "nvidia_configured": self.is_nvidia_configured,
            "nvidia_key_masked": self.masked_nvidia_key(),
            "gemini_model": self.GEMINI_MODEL,
            "gemini_configured": self.is_gemini_configured,
            "gemini_key_masked": self.masked_gemini_key(),
            "langsmith_configured": self.is_langsmith_configured,
            "langsmith_project": self.LANGSMITH_PROJECT,
            "langsmith_key_masked": self.masked_langsmith_key(),
            "database_path": str(self.DATABASE_PATH),
            "export_dir": str(self.EXPORT_DIR),
            "max_run_seconds": self.MAX_RUN_SECONDS,
            "drain_timeout_seconds": self.DRAIN_TIMEOUT_SECONDS,
            "max_iterations": self.MAX_ITERATIONS,
            "max_search_queries": self.MAX_SEARCH_QUERIES,
            "max_pages_fetched": self.MAX_PAGES_FETCHED,
            "max_response_bytes": self.MAX_RESPONSE_BYTES,
            "max_records": self.MAX_RECORDS,
            "max_concurrent_runs": self.MAX_CONCURRENT_RUNS,
            "default_freshness": self.DEFAULT_FRESHNESS,
            "default_collection_mode": self.DEFAULT_COLLECTION_MODE,
            "log_level": self.LOG_LEVEL,
            "nvidia_rpm": self.NVIDIA_RPM,
            "search_batch_size": self.SEARCH_BATCH_SIZE,
            "freehire_enabled": self.FREEHIRE_ENABLED,
            "freehire_api_url": self.FREEHIRE_API_URL,
            "freehire_configured": bool(self.FREEHIRE_API_KEY.strip()),
            "freehire_max_requests_per_run": self.FREEHIRE_MAX_REQUESTS_PER_RUN,
            "searxng_url": self.SEARXNG_URL,
            "searxng_configured": bool(self.SEARXNG_URL.strip()),
            "primary_search_provider": self.PRIMARY_SEARCH_PROVIDER,
            "direct_ats_enabled": self.DIRECT_ATS_ENABLED,
            "serper_configured": bool(self.SERPER_API_KEY.strip()),
            "serper_key_masked": self.masked_serper_key(),
            "tavily_configured": bool(self.TAVILY_API_KEY.strip()),
            "tavily_key_masked": self.masked_tavily_key(),
            "brave_configured": bool(self.BRAVE_API_KEY.strip()),
            "brave_key_masked": self.masked_brave_key(),
        }

    def load_from_db(self) -> None:
        """Load persistent settings overrides from SQLite database."""
        try:
            from datahunt.db.settings_repository import SettingsRepository
            from datahunt.db.connection import get_connection
            repo = SettingsRepository(conn_factory=lambda: get_connection(self.DATABASE_PATH))
            overrides = repo.get_all()
            for k, v in overrides.items():
                target_key = k.upper()
                if target_key in EDITABLE_SETTINGS:
                    converter = EDITABLE_SETTINGS[target_key]
                    try:
                        if converter == bool:
                            setattr(self, target_key, str(v).lower() in ("true", "1", "yes"))
                        else:
                            setattr(self, target_key, converter(v))
                    except Exception:
                        pass
        except Exception:
            pass

    def update_and_persist(self, updates: dict) -> dict:
        """Atomically persist updates to database and synchronize to .env."""
        from datahunt.db.settings_repository import SettingsRepository
        from datahunt.db.connection import get_connection
        repo = SettingsRepository(conn_factory=lambda: get_connection(self.DATABASE_PATH))
        persisted = {}

        for k, v in updates.items():
            target_key = k.upper()
            if target_key not in EDITABLE_SETTINGS:
                continue

            # Skip masked or empty API keys
            if "KEY" in target_key and (not v or "***" in str(v) or "..." in str(v) or "<unset>" in str(v)):
                continue

            converter = EDITABLE_SETTINGS[target_key]
            try:
                if converter == bool:
                    val = str(v).lower() in ("true", "1", "yes")
                else:
                    val = converter(v)

                # Validation guardrails
                if target_key in ("MAX_RUN_SECONDS", "MAX_RECORDS", "MAX_SEARCH_QUERIES", "MAX_PAGES_FETCHED", "NVIDIA_RPM", "SEARCH_BATCH_SIZE", "FREEHIRE_MAX_REQUESTS_PER_RUN", "MAX_CONCURRENT_RUNS") and val <= 0:
                    continue
                if target_key == "DRAIN_TIMEOUT_SECONDS" and val < 0:
                    continue
                if target_key == "LLM_PROVIDER" and val not in ("nvidia", "gemini"):
                    continue
                if target_key == "DEFAULT_FRESHNESS" and val not in ("0sec", "24h", "30d", "all_time"):
                    continue
                if target_key == "DEFAULT_COLLECTION_MODE" and val not in ("thorough", "fast"):
                    continue

                setattr(self, target_key, val)
                persisted[target_key] = str(val)
            except (ValueError, TypeError):
                continue

        if persisted:
            repo.set_many(persisted)
            self._sync_to_env_file(persisted)

        return self.to_safe_dict()

    def _sync_to_env_file(self, updates: dict) -> None:
        """Synchronize key-value overrides into .env file for cold restart durability."""
        env_path = Path(".env")
        existing_lines = []
        if env_path.exists():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    existing_lines = f.readlines()
            except Exception:
                existing_lines = []

        updated_keys = set()
        new_lines = []
        for line in existing_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _ = stripped.split("=", 1)
                k = k.strip().upper()
                if k in updates:
                    new_lines.append(f"{k}={updates[k]}\n")
                    updated_keys.add(k)
                    continue
            new_lines.append(line)

        for k, v in updates.items():
            if k not in updated_keys:
                if new_lines and not new_lines[-1].endswith("\n"):
                    new_lines.append("\n")
                new_lines.append(f"{k}={v}\n")

        try:
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
        except Exception:
            pass

    def reset_to_defaults(self) -> dict:
        """Clear database overrides and restore settings from default environment."""
        try:
            from datahunt.db.settings_repository import SettingsRepository
            from datahunt.db.connection import get_connection
            repo = SettingsRepository(conn_factory=lambda: get_connection(self.DATABASE_PATH))
            repo.clear_all()
        except Exception:
            pass

        # Clear synced overrides from .env if present (preserve API keys and credentials)
        env_path = Path(".env")
        if env_path.exists():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                new_lines = [
                    line for line in lines
                    if not any(
                        line.strip().startswith(f"{k}=")
                        for k in EDITABLE_SETTINGS
                        if not any(sec in k for sec in ("KEY", "TOKEN", "SECRET", "URL"))
                    )
                ]
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
            except Exception:
                pass

        self.MAX_RUN_SECONDS = 1800
        self.DRAIN_TIMEOUT_SECONDS = 600
        self.MAX_RECORDS = 500
        self.MAX_SEARCH_QUERIES = 150
        self.MAX_PAGES_FETCHED = 300
        self.MAX_CONCURRENT_RUNS = 2
        self.LLM_PROVIDER = "nvidia"
        self.NVIDIA_PRIMARY_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
        self.NVIDIA_FALLBACK_MODEL = "mistralai/mistral-large-4-1t-a52b"
        self.NVIDIA_RPM = 40
        self.SEARCH_BATCH_SIZE = 20
        self.GEMINI_MODEL = "auto"
        self.DEFAULT_FRESHNESS = "0sec"
        self.DEFAULT_COLLECTION_MODE = "thorough"
        self.LOG_LEVEL = "INFO"

        return self.to_safe_dict()


EDITABLE_SETTINGS = {
    "MAX_RUN_SECONDS": int,
    "DRAIN_TIMEOUT_SECONDS": int,
    "MAX_RECORDS": int,
    "MAX_SEARCH_QUERIES": int,
    "MAX_PAGES_FETCHED": int,
    "MAX_BROWSER_PAGES": int,
    "MAX_ITERATIONS": int,
    "MAX_CONCURRENT_RUNS": int,
    "LLM_PROVIDER": str,
    "NVIDIA_API_KEY": str,
    "NVIDIA_PRIMARY_MODEL": str,
    "NVIDIA_FALLBACK_MODEL": str,
    "NVIDIA_RPM": int,
    "GEMINI_API_KEY": str,
    "GEMINI_MODEL": str,
    "SEARCH_BATCH_SIZE": int,
    "FREEHIRE_ENABLED": bool,
    "FREEHIRE_API_URL": str,
    "FREEHIRE_MAX_REQUESTS_PER_RUN": int,
    "BRAVE_API_KEY": str,
    "SERPER_API_KEY": str,
    "TAVILY_API_KEY": str,
    "SEARXNG_URL": str,
    "PRIMARY_SEARCH_PROVIDER": str,
    "DIRECT_ATS_ENABLED": bool,
    "DEFAULT_FRESHNESS": str,
    "DEFAULT_COLLECTION_MODE": str,
    "LOG_LEVEL": str,
}

settings = Settings()
try:
    settings.load_from_db()
except Exception:
    pass

