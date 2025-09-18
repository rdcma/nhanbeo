import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def load_env() -> None:
    """Load environment variables from .env if present."""
    load_dotenv()


def get_orders_json_path(default_relative: str = "examples/orders_priority.json") -> str:
    load_env()
    path = os.getenv("ORDERS_JSON", default_relative)
    # Convert to absolute path for stability
    abs_path = str(Path(path).expanduser().resolve())
    return abs_path


def get_default_conversation_id() -> str:
    load_env()
    return os.getenv(
        "CONVERSATION_ID_DEFAULT",
        "792129147307154_24089184430742730",
    )


def get_redis_url() -> Optional[str]:
    load_env()
    return os.getenv("REDIS_URL", None)


def get_llm_model_name(default: str = "gemini-1.5-flash") -> str:
    # Keep consistent with product_qa defaults when available
    load_env()
    return os.getenv("GOOGLE_MODEL", default)


def get_poscake_base(default: str = "http://160.250.216.28:13886") -> str:
    load_env()
    return os.getenv("POSCAKE_BASE", default)


