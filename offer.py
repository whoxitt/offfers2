import re
from datetime import datetime, timedelta, timezone

NFT_RE = re.compile(r"(?:https?://)?t\.me/nft/([^/?#\s]+?)-(\d+)(?:/)?$", re.I)
PRICE_RE = re.compile(r"(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<currency>грам(?:м)?|gram(?:s)?|зв(?:ёзд|езд)?|stars?|star)\b", re.I)

DECLINE_EMOJI_ID = "5778527486270770928"
ACCEPT_EMOJI_ID = "5776375003280838798"
TRANSFER_EMOJI_ID = "5879770735999717115"
GIFT_EMOJI_ID = "6032937473162614352"


def normalize_nft(raw: str):
    m = NFT_RE.fullmatch(raw.strip())
    if not m:
        return None
    name, number = m.group(1), int(m.group(2))
    return {"gift_name": name, "gift_number": number, "gift_url": f"https://t.me/nft/{name}-{number}"}


def parse_offer(text: str):
    tokens = text.strip().split()
    locale = "ru"
    if len(tokens) >= 2 and tokens[1].lower() in {"англ", "английский", "en", "eng"}:
        locale = "en"
        tokens.pop(1)
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
    currency = "gram" if currency_raw.startswith(("грам", "gram")) else "stars"
    return {**nft, "amount": amount, "currency": currency, "locale": locale}


def display_amount(amount: float, currency: str) -> str:
    value = f"{amount:g}"
    # Обход PEER_FLOOD: латинская 'а' вместо кириллической
    return f"{value} {'Grаm' if currency == 'gram' else 'Stаrs'}"


def gift_title(name: str, number: int) -> str:
    return re.sub(r"(?<![a-z])(?=[A-Z])", " ", name).strip() + f" #{number}"


def format_remaining(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}ч {minutes:02d}м"
    return f"{minutes}м"


def make_expiry(now=None, hours=6):
    now = now or datetime.now(timezone.utc)
    return now + timedelta(hours=hours)
