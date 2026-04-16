"""Application configuration loaded from environment variables via pydantic-settings."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the Social Content Engine.

    All values are read from environment variables or a `.env` file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── General ──────────────────────────────────────────────
    app_name: str = "social-content-engine"
    debug: bool = False

    # ── Database ─────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./storage/social_engine.db"

    # ── Directus ─────────────────────────────────────────────
    directus_url: str = "http://localhost:8055"
    directus_access_token: str = ""
    directus_email: str = ""
    directus_password: str = ""
    directus_sources_collection: str = "content_sources"
    directus_ideas_collection: str = "content_ideas"
    directus_drafts_collection: str = "content_drafts"
    directus_publish_targets_collection: str = "publish_targets"
    directus_carousel_assets_collection: str = "carousel_assets"
    directus_workflow_events_collection: str = "workflow_events"

    # ── Qdrant ───────────────────────────────────────────────
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "content_chunks"
    qdrant_mode: Literal["server", "local", "memory"] = "local"
    qdrant_local_path: str = "./storage/qdrant_data"

    # ── Embedding ────────────────────────────────────────────
    # BAAI/bge-large-en-v1.5 — 1024-dim, top-ranked open retrieval model
    # on MTEB for English. Significantly better than MiniLM-L6 (384-dim)
    # for domain-specific B2B / technical text.
    # ⚠️  Switching from MiniLM: delete the Qdrant collection and re-ingest
    #     all sources (dimension change is breaking).
    # First run downloads ~1.3 GB from Hugging Face automatically.
    embedding_provider: Literal["local", "openai"] = "local"
    embedding_model_local: str = "BAAI/bge-large-en-v1.5"
    embedding_model_openai: str = "text-embedding-3-small"
    embedding_dimension: int = 1024  # bge-large-en=1024; MiniLM=384; OpenAI small=1536
    openai_api_key: str = ""

    # ── AI Generation ────────────────────────────────────────
    generation_provider: Literal["groq", "anthropic", "openrouter"] = "openrouter"

    # OpenRouter — Llama 3.3 Instruct (primary provider)
    # Get your key at https://openrouter.ai/keys
    openrouter_api_key: str = ""
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct"
    # Optional: identifies your app in OpenRouter dashboard
    openrouter_site_url: str = "https://www.dataclap.digital"
    openrouter_site_name: str = "DataClap Digital"

    # Groq (free tier — Llama 3.3 70B, fallback)
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Anthropic (paid — Claude, fallback)
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-4-6"

    # ── Image Generation (Gemini — optional) ─────────────────
    gemini_api_key: str = ""

    # ── Deep Research (Parallel Search API) ─────────────────
    # Get your key at https://platform.parallel.ai/
    parallel_api_key: str = ""

    # ── LinkedIn OAuth2 ──────────────────────────────────────
    linkedin_access_token: str = ""
    linkedin_person_urn: str = ""

    # ── Twitter / X ──────────────────────────────────────────
    x_api_key: str = ""
    x_api_secret: str = ""
    x_access_token: str = ""
    x_access_token_secret: str = ""
    x_bearer_token: str = ""

    # ── Postiz ───────────────────────────────────────────────
    postiz_api_url: str = "https://api.postiz.com/public/v1"
    postiz_api_key: str = ""
    postiz_linkedin_integration_id: str = ""
    postiz_x_integration_id: str = ""
    postiz_default_delay_minutes: int = 60
    postiz_webhook_secret: str = ""

    # ── Storage ──────────────────────────────────────────────
    storage_dir: str = "./storage"
    images_dir: str = "./storage/images"

    @property
    def storage_path(self) -> Path:
        """Return the resolved storage directory, creating it if absent."""
        p = Path(self.storage_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def images_path(self) -> Path:
        """Return the resolved images directory, creating it if absent."""
        p = Path(self.images_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def effective_embedding_dimension(self) -> int:
        """Return the vector dimension based on the active provider and model."""
        if self.embedding_provider == "openai":
            return 1536
        return self.embedding_dimension


settings = Settings()
