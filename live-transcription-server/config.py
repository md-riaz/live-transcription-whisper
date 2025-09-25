"""Application configuration loaded from environment variables."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load variables from a local .env file if present.
load_dotenv()


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _as_log_level(value: str | None, default: int = logging.INFO) -> int:
    if not value:
        return default
    level = logging.getLevelName(value.upper())
    # logging.getLevelName returns the numeric level when it recognizes the name,
    # otherwise it echoes back the input string. Guard for that case.
    return level if isinstance(level, int) else default


@dataclass(frozen=True)
class Settings:
    """Immutable settings object with sane defaults."""

    host: str
    port: int
    transcription_model_id: str
    audio_storage_dir: Path
    log_level: int

    @property
    def log_level_name(self) -> str:
        return logging.getLevelName(self.log_level)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Read configuration from environment variables once per process."""

    settings = Settings(
        host=os.getenv("HOST", "0.0.0.0"),
        port=_as_int(os.getenv("PORT"), 8000),
        transcription_model_id=os.getenv(
            "TRANSCRIPTION_MODEL_ID", "openai/whisper-medium"
        ),
        audio_storage_dir=Path(os.getenv("AUDIO_STORAGE_DIR", "audio_chunks")),
        log_level=_as_log_level(os.getenv("LOG_LEVEL")),
    )

    # Ensure the audio storage directory exists early in the lifecycle.
    settings.audio_storage_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(level=settings.log_level)
    logging.getLogger(__name__).debug(
        "Loaded settings: host=%s port=%s model=%s audio_dir=%s log_level=%s",
        settings.host,
        settings.port,
        settings.transcription_model_id,
        settings.audio_storage_dir,
        settings.log_level_name,
    )

    return settings


# Instantiate settings at import time so other modules can simply import `settings`.
settings = get_settings()
