import os
from dataclasses import dataclass
from pathlib import Path


def _bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    database_path: str
    offer_help_url: str
    business_debug: bool
    offer_access_mode: str
    offer_always_allowed_ids: tuple[int, ...]
    offer_expire_hours: float = 5.9833333333  # 5ч 59м
    ton_wallet: str = ""
    payment_amount: float = 10.0  # Grаm
    tonkeeper_deeplink: str = "https://app.tonkeeper.com/transfer/{address}?amount={amount}&text={memo}"


def load_config() -> Config:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    token = os.getenv("BOT_TOKEN", "").strip()
    owner = os.getenv("OWNER_ID", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    if not owner.isdigit():
        raise RuntimeError("OWNER_ID must be an integer")

    raw_ids = os.getenv("OFFER_ALWAYS_ALLOWED_IDS", "").strip()
    ids = []
    for item in raw_ids.split(","):
        item = item.strip()
        if item and item.lstrip("-").isdigit():
            ids.append(int(item))

    mode = os.getenv("OFFER_ACCESS_MODE", "owner").strip().lower()
    if mode not in {"owner", "all"}:
        raise RuntimeError("OFFER_ACCESS_MODE must be owner or all")

    return Config(
        bot_token=token,
        owner_id=int(owner),
        database_path=os.getenv("DATABASE_PATH", "bot_database.db").strip() or "bot_database.db",
        offer_help_url=os.getenv("OFFER_HELP_URL", "").strip(),
        business_debug=_bool(os.getenv("BUSINESS_DEBUG", "1"), True),
        offer_access_mode=mode,
        offer_always_allowed_ids=tuple(ids),
        offer_expire_hours=float(os.getenv("OFFER_EXPIRE_HOURS", "5.9833333333")),
        ton_wallet=os.getenv("TON_WALLET", "").strip(),
        payment_amount=float(os.getenv("PAYMENT_AMOUNT", "10.0")),
    )
