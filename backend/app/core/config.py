from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


class SandboxLimits(BaseModel):
    """Default resource bounds for every sandboxed action. Non-negotiable — never run unbounded."""

    cpus: float = 1.0
    memory_mb: int = 512
    timeout_s: int = 300
    network: str = "none"  # no network unless a specific action is approved for it
    read_only_root: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEVOS_", env_file=REPO_DIR / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://devos:devos@localhost:5432/devos"
    llm_api_key: str = ""  # empty = LLM planner disabled, rule-based planner is used
    llm_provider: str = "anthropic"  # "anthropic" | "gemini"
    llm_model: str = ""  # empty = the provider's default in services/llm_providers.DEFAULT_MODELS
    llm_timeout_s: float = 30.0
    data_dir: Path = BACKEND_DIR / ".devos_data"  # recovery points live here
    sandbox: SandboxLimits = SandboxLimits()


settings = Settings()
