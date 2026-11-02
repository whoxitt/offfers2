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
    business_debug: bool
    offer_access_mode: str
    offer_always_allowed_ids: Tuple[int, ...]
    require_subscription: bool = True
    channel_id: str = ""
    channel_url: str = ""
    support_url: str = "https://t.me/witensea"
    users_chat_url: str = "https://t.me/"
    offer_expire_hours: float = 24.0
    bot_username: str = ""
    # Настройки оплаты
    payment_price: float = 150.0
    ton_wallet: str = ""
    cryptobot_token: str = ""


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
        raise RuntimeError("DATABASE_URL is not set; add a PostgreSQL reference in Railway Variables")

    ids = []
    for item in os.getenv("OFFER_ALWAYS_ALLOWED_IDS", "").split(","):
        item = item.strip()
        if item and item.lstrip("-").isdigit():
            ids.append(int(item))

    # Доступ к оферам теперь платный
    mode = "paid"

    return Config(
        bot_token=token,
        owner_id=int(owner),
        database_url=database_url,
        offer_help_url=os.getenv("OFFER_HELP_URL", "").strip(),
        business_debug=_bool(os.getenv("BUSINESS_DEBUG", "1"), True),
        offer_access_mode=mode,
        offer_always_allowed_ids=tuple(ids),
        require_subscription=_bool(os.getenv("REQUIRE_SUBSCRIPTION", "false"), False),
        channel_id=os.getenv("CHANNEL_ID", "").strip(),
        channel_url=os.getenv("CHANNEL_URL", "").strip(),
        support_url=os.getenv("SUPPORT_URL", "https://t.me/witensea").strip(),
        users_chat_url=os.getenv("USERS_CHAT_URL", "https://t.me/").strip(),
        offer_expire_hours=float(os.getenv("OFFER_EXPIRE_HOURS", "24")),
        bot_username=os.getenv("BOT_USERNAME", "").strip().lstrip("@"),
        payment_price=float(os.getenv("PAYMENT_PRICE", "150")),
        ton_wallet=os.getenv("TON_WALLET", "").strip(),
        cryptobot_token=os.getenv("CRYPTOBOT_TOKEN", "").strip(),
    )
