import re
from datetime import datetime, timedelta, timezone

NFT_RE = re.compile(r"(?:https?://)?t\.me/nft/([^/?#\s]+?)-(\d+)(?:/)?$", re.I)
PRICE_RE = re.compile(
    r"(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<currency>грам(?:м)?|gram(?:s)?|зв(?:ёзд|езд)?|stars?|star|руб(?:лей|ля|ль)?|руб|р|rub(?:les?)?)\b",
    re.I,
)

DECLINE_EMOJI_ID = "5778527486270770928"
ACCEPT_EMOJI_ID = "5776375003280838798"
TRANSFER_EMOJI_ID = "5879770735999717115"
GIFT_EMOJI_ID = "6032937473162614352"

LANGUAGES = {
    "англ": "en", "английский": "en", "английском": "en", "en": "en", "eng": "en", "english": "en",
    "китай": "cn", "кит": "cn", "китайский": "cn", "cn": "cn", "zh": "cn", "chinese": "cn",
    "араб": "ar", "арабский": "ar", "ar": "ar", "arabic": "ar",
    "рус": "ru", "русский": "ru", "ru": "ru", "russian": "ru",
}


def normalize_nft(raw: str):
    m = NFT_RE.fullmatch(raw.strip().rstrip(".,;"))
    if not m:
        return None
    name, number = m.group(1), int(m.group(2))
    return {"gift_name": name, "gift_number": number, "gift_url": f"https://t.me/nft/{name}-{number}"}


def parse_offer(text: str):
    tokens = text.strip().split()
    locale = "ru"
    # Language may be placed immediately after command or at the end.
    if len(tokens) > 1:
        lang = LANGUAGES.get(tokens[1].lower().strip(".,;:"))
        if lang:
            locale = lang
            tokens.pop(1)
    if len(tokens) > 1:
        lang = LANGUAGES.get(tokens[-1].lower().strip(".,;:"))
        if lang:
            locale = lang
            tokens.pop()

    nft = None
    for token in tokens[1:]:
        candidate = normalize_nft(token)
        if candidate:
            nft = candidate
            break
    if not nft:
        return {"error": "nft", "locale": locale}

    price = PRICE_RE.search(text)
    if not price:
        return {"error": "price", "locale": locale, **nft}
    amount = float(price.group("amount").replace(",", "."))
    currency_raw = price.group("currency").lower()
    if currency_raw.startswith(("грам", "gram")):
        currency = "gram"
    elif currency_raw.startswith(("р", "rub")):
        currency = "rub"
    else:
        currency = "stars"
    return {**nft, "amount": amount, "currency": currency, "locale": locale}


def display_amount(amount: float, currency: str) -> str:
    value = f"{amount:g}"
    if currency == "gram":
        return f"{value} Grаm"
    if currency == "rub":
        return f"{value}₽"
    return f"{value} Stаrs"


def gift_title(name: str, number: int) -> str:
    return re.sub(r"(?<![a-z])(?=[A-Z])", " ", name).strip() + f" #{number}"


def format_remaining(seconds: float) -> str:
    return f"{max(0, int(seconds)) // 3600}ч"


def make_expiry(now=None, hours=24):
    now = now or datetime.now(timezone.utc)
    return now + timedelta(hours=hours)
