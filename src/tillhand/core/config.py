"""Settings read from the environment (and `.env` in local dev). One instance per process, made at startup."""

from functools import cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from tillhand.core.constants import SUGGESTION_SIMILARITY_FLOOR

EMBEDDING_DIMENSION = 1024
"""`llama-text-embed-v2` at its default size (#27). The `vector(1024)` column in the migrations must match."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: SecretStr
    """Neon's pooled connection string. The app and the migrations both use it."""

    allowed_hosts: list[str] = ["localhost:*", "127.0.0.1:*"]
    """Host headers the MCP endpoint answers; any other gets HTTP 421. A JSON list in the environment,
    e.g. `ALLOWED_HOSTS='["tillhand-demo.up.railway.app"]'`. `name:*` allows any port."""

    platforms_file: Path = Path("data/platforms.json")
    """The pre-approved Platform registry (#37): Platforms whose profiles are served without a fetch."""

    suggestion_similarity_floor: Annotated[float, Field(ge=-1, le=1)] = SUGGESTION_SIMILARITY_FLOOR
    """The cosine similarity a similar Product needs to be suggested (#46). It depends on the catalog and the
    embedding model, so each Merchant's deployment may tune it; the default was tuned on the skincare seed."""

    pinecone_api_key: SecretStr
    embedding_model: Literal["llama-text-embed-v2"] = "llama-text-embed-v2"
    embedding_dimension: Annotated[int, Field(ge=EMBEDDING_DIMENSION, le=EMBEDDING_DIMENSION)] = (
        EMBEDDING_DIMENSION
    )
    """Pinned: changing it needs a migration of the `vector(1024)` column, not just a new value."""


@cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  (fields come from the environment)
