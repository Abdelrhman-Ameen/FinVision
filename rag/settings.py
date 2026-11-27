import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


REPO = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = REPO / ".local" / "rag"


@dataclass(frozen=True)
class Settings:
    cache: Path = DEFAULT_CACHE
    api_key: str = field(default="", repr=False)
    model: str = "gemini-3.5-flash-lite"
    provider: str = "gemini"
    ollama_url: str = "http://127.0.0.1:11434"

    @classmethod
    def load(cls) -> "Settings":
        local = dotenv_values(REPO / ".local" / "rag.env", interpolate=False)

        def option(name: str, default: str = "") -> str:
            return (os.environ.get(name) or local.get(name) or default).strip()

        provider = option("RAG_PROVIDER", cls.provider).lower()
        cache = REPO / option("RAG_CACHE_DIR", str(DEFAULT_CACHE))
        if provider == "ollama":
            return cls(cache=cache, provider=provider, model=option("OLLAMA_MODEL", "gemma3:4b"),
                       ollama_url=option("OLLAMA_URL", cls.ollama_url))
        if provider != "gemini":
            raise ValueError("RAG_PROVIDER must be gemini or ollama")
        return cls(
            cache=cache,
            api_key=option("GEMINI_API_KEY"),
            model=option("GEMINI_MODEL", cls.model),
        )
