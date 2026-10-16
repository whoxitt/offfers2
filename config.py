import os
from dataclasses import dataclass
from typing import Tuple


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    database_url: str
    offer_help_url: str
    support_url: str
    channel_id: str
    channel_url: str
    users_chat_url: str
    business_debug: bool
    offer_access_mode: str
    offer_always_allowed_ids: Tuple[int, ...]
    offer_expire_hours: float = 24.0
    ton_wallet: str = ""
    payment_amount: float = 10.0


def load_config() -> Config:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    token = os.getenv("BOT_TOKEN", "").strip()
    owner = os.getenv("OWNER_ID", "").strip()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    if not owner.isdigit():
        raise RuntimeError("OWNER_ID must be an integer")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set. Add a PostgreSQL service in Railway and reference its DATABASE_URL.")

    ids = []
    for item in os.getenv("OFFER_ALWAYS_ALLOWED_IDS", "").split(","):
        item = item.strip()
        if item and item.lstrip("-").isdigit():
            ids.append(int(item))

    mode = os.getenv("OFFER_ACCESS_MODE", "owner").strip().lower()
    if mode not in {"owner", "all"}:
        raise RuntimeError("OFFER_ACCESS_MODE must be owner or all")
    expire_hours = float(os.getenv("OFFER_EXPIRE_HOURS", "24"))
    if expire_hours <= 0:
        raise RuntimeError("OFFER_EXPIRE_HOURS must be greater than zero")

    return Config(
        bot_token=token,
        owner_id=int(owner),
        database_url=database_url,
        offer_help_url=os.getenv("OFFER_HELP_URL", "").strip(),
        support_url=os.getenv("SUPPORT_URL", "https://t.me/witensea").strip(),
        channel_id=os.getenv("CHANNEL_ID", "-1004393569613").strip(),
        channel_url=os.getenv("CHANNEL_URL", "https://t.me/dodep222").strip(),
        users_chat_url=os.getenv("USERS_CHAT_URL", "https://t.me/+DrIdLrR8zLs3NWJi").strip(),
        business_debug=_bool(os.getenv("BUSINESS_DEBUG", "0"), False),
        offer_access_mode=mode,
        offer_always_allowed_ids=tuple(ids),
        offer_expire_hours=expire_hours,
        ton_wallet=os.getenv("TON_WALLET", "").strip(),
        payment_amount=float(os.getenv("PAYMENT_AMOUNT", "10.0")),
    )
