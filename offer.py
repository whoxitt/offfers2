import re
from datetime import datetime, timedelta, timezone

NFT_RE = re.compile(r"(?:https?://)?t\.me/nft/([^/?#\s]+?)-(\d+)(?:/)?$", re.I)
PRICE_RE = re.compile(r"(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<currency>грам(?:м)?|gram(?:s)?|зв(?:ёзд|езд)?|stars?|star|р(?:уб(?:л)?)?)\b", re.I)

DECLINE_EMOJI_ID = "5778527486270770928"
ACCEPT_EMOJI_ID = "5776375003280838798"
TRANSFER_EMOJI_ID = "5879770735999717115"
GIFT_EMOJI_ID = "6032937473162614352"
CONFIRM_EMOJI_ID = "5800595351589195950"  # Красный эмодзи для кнопки подтверждения
PREMIUM_EMOJI_ID = "5825794181183836432"  # Legacy constant retained for compatibility; unused
DONE_EMOJI_ID = "5800595351589195950"  # Legacy constant retained for compatibility; unused
STARS_EMOJI_ID = "5321485469249198987"  # Премиум звёздочка для Stars
GRAM_EMOJI_ID = "5255894863303450180"  # Эмодзи для Gram


def normalize_nft(raw: str):
    m = NFT_RE.fullmatch(raw.strip())
    if not m:
        return None
    name, number = m.group(1), int(m.group(2))
    return {"gift_name": name, "gift_number": number, "gift_url": f"https://t.me/nft/{name}-{number}"}


def parse_offer(text: str, command_prefix: str = "ху"):
    """Parse an offer and recognize the requested language anywhere in the command.

    Supported examples:
      <command> https://t.me/nft/PartySparkler-140864 150 грам китай
      .<command> араб https://t.me/nft/PartySparkler-140864 150 stars
    """
    command_prefix = (command_prefix or "ху").strip().lstrip(".")
    
    escaped_prefix = re.escape(command_prefix)
    test_match = re.match(rf'^\.?{escaped_prefix}(\d+)$', text.strip(), re.I)
    if test_match:
        level = int(test_match.group(1))
        return {
            "gift_name": "DeliciousCake",
            "gift_number": 1,
            "gift_url": "https://t.me/nft/DeliciousCake-1",
            "amount": 100,
            "currency": "gram",
            "locale": "ru",
            "test_level": level
        }
    
    # Тестовые команды для проверки обходов: оф1, оф2, оф3, оф4
    bypass_test_match = re.match(r'^\.?оф([1-4])\s+(.+)', text.strip(), re.I)
    if bypass_test_match:
        bypass_mode = int(bypass_test_match.group(1))
        rest_of_command = bypass_test_match.group(2)
        
        # Парсим остальную часть команды (NFT ссылка + цена)
        tokens = rest_of_command.split()
        nft = None
        for token in tokens:
            candidate = normalize_nft(token)
            if candidate:
                nft = candidate
                break
        
        price = PRICE_RE.search(rest_of_command)
        if not nft or not price:
            return {"error": "test_format", "locale": "ru"}
        
        amount = float(price.group("amount").replace(",", "."))
        currency_raw = price.group("currency").lower()
        if currency_raw.startswith(("грам", "gram")):
            currency = "gram"
        elif currency_raw.startswith(("р", "руб")):
            currency = "rub"
        else:
            currency = "stars"
        
        return {
            **nft,
            "amount": amount,
            "currency": currency,
            "locale": "ru",
            "bypass_mode": bypass_mode
        }
    
    tokens = text.strip().split()
    if not tokens or tokens[0].strip().lower().lstrip(".") != command_prefix.lower():
        return {"error": "command", "locale": "ru"}
    locale = "ru"
    language_aliases = {
        "en": "en", "eng": "en", "англ": "en", "английский": "en", "английском": "en", "english": "en",
        "cn": "cn", "zh": "cn", "chinese": "cn", "中文": "cn", "китай": "cn", "кидай": "cn", "кит": "cn", "китайский": "cn", "китайском": "cn",
        "ar": "ar", "arab": "ar", "arabic": "ar", "عربي": "ar", "العربية": "ar", "عرب": "ar", "араб": "ar", "арабский": "ar", "арабском": "ar",
        "ru": "ru", "рус": "ru", "русский": "ru", "русском": "ru",
    }
    # Language can be placed after the command or at the end, as users naturally write it.
    # Remove the language token before looking for the NFT URL, but keep the original text for price parsing.
    for i in range(1, len(tokens)):
        token = tokens[i].strip(".,:;!?()[]{}<>\"'`).").lower()
        if token in language_aliases:
            locale = language_aliases[token]
            tokens.pop(i)
            break

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
    elif currency_raw.startswith(("р", "руб")):
        currency = "rub"
    else:
        currency = "stars"
    return {**nft, "amount": amount, "currency": currency, "locale": locale}


def display_amount(amount: float, currency: str, with_emoji: bool = False, bypass_mode: int = 0) -> str:
    """
    bypass_mode:
    0 - с эмодзи (with_emoji=True)
    1 - русская 'а' в Stars и Gram (Stаrs / Grаm)
    2 - разделение пробелами (S t a r s / G r a m)
    3 - замена на синонимы (звёзды / граммы)
    4 - zero-width space между буквами
    """
    value = f"{amount:g}"
    
    if with_emoji or bypass_mode == 0:
        if currency == "gram":
            return f'{value}<tg-emoji emoji-id="{GRAM_EMOJI_ID}">💎</tg-emoji>'
        elif currency == "rub":
            return f"{value}₽"
        return f'{value}<tg-emoji emoji-id="{STARS_EMOJI_ID}">⭐</tg-emoji>'
    
    # Обходы для текстового формата
    if bypass_mode == 1:
        # Русская 'а' (U+0430) вместо латинской 'a'
        if currency == "gram":
            return f"{value} Grаm"  # 'а' - русская
        elif currency == "rub":
            return f"{value}₽"
        return f"{value} Stаrs"  # 'а' - русская
    
    elif bypass_mode == 2:
        # Разделение пробелами
        if currency == "gram":
            return f"{value} G r a m"
        elif currency == "rub":
            return f"{value}₽"
        return f"{value} S t a r s"
    
    elif bypass_mode == 3:
        # Синонимы
        if currency == "gram":
            return f"{value} граммы"
        elif currency == "rub":
            return f"{value}₽"
        return f"{value} звёзды"
    
    elif bypass_mode == 4:
        # Zero-width space (U+200B) между буквами
        zwsp = "\u200b"
        if currency == "gram":
            return f"{value} G{zwsp}r{zwsp}am"
        elif currency == "rub":
            return f"{value}₽"
        return f"{value} S{zwsp}t{zwsp}ars"
    
    # Дефолт: обычный формат (может вызвать peer_flood)
    if currency == "gram":
        return f"{value} Gram"
    elif currency == "rub":
        return f"{value}₽"
    return f"{value} Stars"


def gift_title(name: str, number: int) -> str:
    return re.sub(r"(?<![a-z])(?=[A-Z])", " ", name).strip() + f" #{number}"


def format_remaining(seconds: float, locale: str = "ru") -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    if locale == "cn":
        return f"{hours}小时"
    elif locale == "ar":
        return f"{hours} ساعة"
    elif locale == "en":
        return f"{hours}h"
    else:
        return f"{hours}ч"


def make_expiry(now=None, hours=6):
    now = now or datetime.now(timezone.utc)
    return now + timedelta(hours=hours)
