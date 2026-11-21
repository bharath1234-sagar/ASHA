"""Project configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    provider: str
    llm_provider: str
    db_path: str
    beneficiaries_path: str
    host: str
    port: int
    gemini_model: str
    require_verified_danger: bool
    encryption_key: str = field(repr=False, default="")
    gnani_api_key: str | None = field(repr=False, default=None)
    gnani_base_url: str = "https://api.vachana.ai"
    gemini_api_key: str | None = field(repr=False, default=None)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def load_config(env: dict | None = None) -> Config:
    if env is None:
        _load_dotenv()
        env = os.environ

    problems: list[str] = []

    key = (env.get("VFR_ENCRYPTION_KEY") or "").strip()
    if not key:
        problems.append(
            "VFR_ENCRYPTION_KEY is missing. Run: python scripts/setup_env.py"
        )
    else:
        try:
            from cryptography.fernet import Fernet
            Fernet(key.encode())
        except Exception:
            problems.append(
                "VFR_ENCRYPTION_KEY is not a valid Fernet key. "
                "Check .env and run: python scripts/setup_env.py"
            )

    provider = (env.get("PROVIDER") or "mock").lower()
    if provider not in ("mock", "gnani"):
        problems.append("PROVIDER must be 'mock' or 'gnani'.")

    gnani_key = (env.get("GNANI_API_KEY") or "").strip() or None
    if provider == "gnani" and not gnani_key:
        problems.append("GNANI_API_KEY is required when PROVIDER=gnani.")

    gem_key = (env.get("GEMINI_API_KEY") or "").strip() or None
    llm = (env.get("LLM_PROVIDER") or "auto").lower()
    if llm == "auto":
        llm = "gemini" if gem_key else "mock"
    if llm not in ("mock", "gemini"):
        problems.append("LLM_PROVIDER must be auto, mock or gemini.")
    if llm == "gemini" and not gem_key:
        problems.append("GEMINI_API_KEY is required when LLM_PROVIDER=gemini.")

    try:
        port = int(env.get("PORT") or 8000)
    except ValueError:
        port = 0
        problems.append("PORT must be a number.")

    if problems:
        raise ConfigError(
            "Configuration problems:\n  - " + "\n  - ".join(problems)
        )

    return Config(
        provider=provider,
        llm_provider=llm,
        db_path=env.get("VFR_DB_PATH") or str(ROOT / "data" / "vfr.db"),
        beneficiaries_path=(
            env.get("VFR_BENEFICIARIES")
            or str(ROOT / "assets" / "synthetic_beneficiaries.json")
        ),
        host=env.get("HOST") or "127.0.0.1",
        port=port,
        gemini_model=env.get("GEMINI_MODEL") or "gemini-2.5-flash",
        require_verified_danger=(
            (env.get("DANGER_REQUIRE_VERIFIED") or "0") == "1"
        ),
        encryption_key=key,
        gnani_api_key=gnani_key,
        gnani_base_url=(
            env.get("GNANI_BASE_URL") or "https://api.vachana.ai"
        ).rstrip("/"),
        gemini_api_key=gem_key,
    )
