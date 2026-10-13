import asyncio
import logging
import random
import secrets
import aiohttp
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
                   display_amount, format_remaining, gift_title, make_expiry, parse_offer)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("offerbot")

cfg = load_config()
db = Database(cfg.database_path)
bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
offer_locks = {}


def display_name(user) -> str:
    return " ".join(x for x in [getattr(user, 'first_name', None), getattr(user, 'last_name', None)] if x) or "Пользователь"


def instruction(locale="ru"):
    if locale == "en":
        return ("<b>Usage</b>\n<code>офер https://t.me/nft/PartySparkler-140864 150 gram</code>\n"
                "<code>офер ру https://t.me/nft/PartySparkler-140864 4500 зв</code>\n"
                "<code>офер англ https://t.me/nft/PartySparkler-140864 4500 stars</code>")
    return ("<b>Использование</b>\n<code>офер https://t.me/nft/PartySparkler-140864 150 грам</code>\n"
            "<code>офер ру https://t.me/nft/PartySparkler-140864 4500 зв</code>\n"
            "<code>офер англ https://t.me/nft/PartySparkler-140864 4500 stars</code>")


def error_text(kind):
    if kind == "nft": return "❌ Не нашёл ссылку на NFT.\n\n" + instruction()
    if kind == "price": return "❌ Не нашёл цену офера.\n\n" + instruction()
    return "❌ Не удалось обработать офер."


def offer_keyboard(offer_id: int, locale: str):
    decline = "Decline" if locale == "en" else "Отклонить"
    accept = "Accept" if locale == "en" else "Принять"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=decline, icon_custom_emoji_id=DECLINE_EMOJI_ID, callback_data=f"of:decline:{offer_id}"),
        InlineKeyboardButton(text=accept, icon_custom_emoji_id=ACCEPT_EMOJI_ID, callback_data=f"of:accept:{offer_id}"),
    ]])


def transfer_keyboard(url: str, locale: str):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="Transfer" if locale == "en" else "Передать",
        icon_custom_emoji_id=TRANSFER_EMOJI_ID, url=url)]])


async def check_ton_payment(wallet: str, memo: str, amount: float, min_confirmations: int = 1) -> bool:
    """Проверяет наличие транзакции с указанным memo в TON блокчейне
    
    Args:
        wallet: Адрес кошелька в любом формате (raw/user-friendly)
        memo: Комментарий транзакции для поиска
        amount: Ожидаемая сумма в TON (Gram)
        min_confirmations: Минимальное количество подтверждений (по умолчанию 1)
    
    Returns:
        True если транзакция найдена и подтверждена
    """
    try:
        # TON Center API v2
        api_url = "https://toncenter.com/api/v2/getTransactions"
        
        async with aiohttp.ClientSession() as session:
            params = {
                "address": wallet,
                "limit": 20,  # Проверяем последние 20 транзакций
            }
            
            async with session.get(api_url, params=params) as resp:
                if resp.status != 200:
                    log.error("TON API error: %s", resp.status)
                    return False
                
                data = await resp.json()
                
                if not data.get("ok"):
                    log.error("TON API returned not ok: %s", data)
                    return False
                
                transactions = data.get("result", [])
                
                for tx in transactions:
                    # Проверяем входящие сообщения
                    in_msg = tx.get("in_msg", {})
                    if not in_msg:
                        continue
                    
                    # Проверяем комментарий (message)
                    message = in_msg.get("message", "")
                    
                    # Декодируем base64 если нужно
                    if isinstance(message, str) and message:
                        try:
                            # Пробуем декодировать из base64
                            import base64
                            decoded = base64.b64decode(message).decode('utf-8', errors='ignore')
                            # Убираем служебные символы (первые 4 байта обычно это opcode)
                            if len(decoded) > 4:
                                decoded = decoded[4:]
                            message = decoded
                        except:
                            pass
                    
                    # Проверяем совпадение memo (без учета регистра)
                    if message.strip().upper() != memo.strip().upper():
                        continue
                    
                    # Проверяем сумму (в nanoTON, 1 TON = 1_000_000_000 nanoTON)
                    tx_value = int(in_msg.get("value", 0))
                    expected_value = int(amount * 1_000_000_000)
                    
                    # Проверяем что сумма >= ожидаемой (с погрешностью в 1%)
                    if tx_value >= expected_value * 0.99:
                        log.info("Found matching transaction: memo=%s amount=%s", memo, tx_value / 1_000_000_000)
                        return True
                
                return False
                
    except Exception as e:
        log.error("Error checking TON payment: %s", e)
        return False


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
    amount = display_amount(parsed["amount"], parsed["currency"])
    remaining = format_remaining((expires - now).total_seconds())
    if parsed["locale"] == "en":
        body = f"User <b>{seller_name}</b> offers you <b>{amount}</b> for the gift <a href=\"{parsed['gift_url']}\">{title}</a>.\n\nOffer expires in <b>{remaining}</b>."
    else:
        # Обход PEER_FLOOD: латинские e, o вместо кириллических
        body = f"Пoльзoвaтeль <b>{seller_name}</b> прeдлaгaeт Вам <b>{amount}</b> за пoдарок <a href=\"{parsed['gift_url']}\">{title}</a>.\n\nПрeдлoжeниe дeйствуeт eщё <b>{remaining}</b>."
    try:
        sent = await safe_send_business(business_connection_id, buyer_id, text=body,
            reply_markup=offer_keyboard(offer_id, parsed["locale"]),
            link_preview_options=LinkPreviewOptions(url=parsed["gift_url"], show_above_text=True))
    except Exception as e:
        log.exception("[OFFER SEND] offer_id=%s chat_id=%s business_connection_id=%s error=%s", offer_id, buyer_id, business_connection_id, e)
        db.delete_offer(offer_id)
        await reply_message(message, "❌ Не удалось отправить офер. Попробуйте ещё раз позже.")
        return
    db.set_offer_message(offer_id, buyer_id, sent.message_id)
    log.info("[OFFER SEND] offer_id=%s chat_id=%s business_connection_id=%s", offer_id, buyer_id, business_connection_id)
    await delete_source(message, business_connection_id)


async def reply_message(message: Message, text: str):
    try:
        if message.business_connection_id:
            await bot.send_message(chat_id=message.chat.id, business_connection_id=message.business_connection_id, text=text)
        else:
            await message.answer(text)
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
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 Купить доступ", callback_data="buy_access")
    ]])
    try:
        if message.business_connection_id:
            await bot.send_message(chat_id=message.chat.id, business_connection_id=message.business_connection_id, 
                                 text="<b>Привет люти воркер</b>", reply_markup=kb)
        else:
            await message.answer("<b>Привет люти воркер</b>", reply_markup=kb)
    except Exception:
        log.exception("Failed to send start message")


@dp.business_connection()
async def on_business_connection(bc: BusinessConnection):
    db.upsert_business_connection(bc.id, bc.user.id, bc.user.username, bc.user.first_name, bc.user.last_name, bc.is_enabled)
    log.info("Business connection id=%s user_id=%s enabled=%s", bc.id, bc.user.id, bc.is_enabled)
    if bc.user.id != cfg.owner_id:
        if bc.user.id in cfg.offer_always_allowed_ids:
            db.add_access(bc.user.id); db.unblock_offer_user(bc.user.id); db.set_connection_blocked(bc.id, False)
            state = "🟢 Разрешено"
        else:
            state = "⛔ Оферы пока заблокированы."
        try:
            await bot.send_message(cfg.owner_id, f"🔔 Новое Business-подключение\n\n👤 {display_name(bc.user)}\n🆔 <code>{bc.user.id}</code>\n🔗 @{bc.user.username or 'нет'}\n\n{state}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🟢 Разрешить", callback_data=f"adm:allow:{bc.user.id}"),
                    InlineKeyboardButton(text="🔴 Заблокировать", callback_data=f"adm:block:{bc.user.id}")]]))
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
    # Поддерживаем разные варианты команды офер (с точкой и без)
    if low.startswith(".офер") or low.startswith(".offer") or low.startswith("офер ") or low.startswith("offer "):
        await process_offer(message, message.business_connection_id if is_business else None)
        return
    if low.startswith("/admin") or low.startswith("/админ"):
        await admin_command(message)
        return
    if low.startswith("/transfer ") or low.startswith("/передача "):
        await set_transfer(message, text.split(maxsplit=1)[1].strip())
        return
    if low.startswith("/allow ") and message.from_user and message.from_user.id == cfg.owner_id:
        await admin_allow(int(text.split(maxsplit=1)[1])); await reply_message(message, "🟢 Доступ выдан."); return
    if low.startswith("/block ") and message.from_user and message.from_user.id == cfg.owner_id:
        await admin_block(int(text.split(maxsplit=1)[1])); await reply_message(message, "🔴 Пользователь заблокирован."); return
    if low.startswith("/unblock ") and message.from_user and message.from_user.id == cfg.owner_id:
        uid=int(text.split(maxsplit=1)[1]); db.unblock_offer_user(uid); await reply_message(message, "🟢 Блокировка снята."); return


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
        await reply_message(message, "❌ Доступ запрещён."); return
    stats = db.stats()
    text = ("<b>Админка OfferBot</b>\n\n"
            "🎁 Доступ к оферам\n"
            "/allow ID — выдать доступ\n/block ID — заблокировать\n/unblock ID — снять блокировку\n\n"
            "/transfer username — сохранить transfer target для своего профиля\n\n"
            f"📊 Статистика: pending={stats.get('pending',0)}, accepted={stats.get('accepted',0)}, declined={stats.get('declined',0)}")
    rows=[]
    for r in db.list_connections()[:20]:
        rows.append([InlineKeyboardButton(text=f"🟢 {r['first_name'] or ''} {r['user_id']}", callback_data=f"adm:allow:{r['user_id']}"),
                     InlineKeyboardButton(text="🔴", callback_data=f"adm:block:{r['user_id']}")])
    await reply_message(message, text)
    if rows:
        await bot.send_message(message.chat.id, "Подключённые Business-профили:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data == "buy_access")
async def buy_access_callback(call: CallbackQuery):
    if not call.from_user:
        await call.answer("Ошибка")
        return
    
    # Проверяем, есть ли уже доступ
    if db.has_access(call.from_user.id, cfg.owner_id, cfg.offer_access_mode):
        await call.answer("✅ У вас уже есть доступ к оферам!", show_alert=True)
        return
    
    text = f"Доступ к оферам стоит <b>{cfg.payment_amount:g} Grаm</b>.\n\nПри блоке акка доступ можно перенести через овнера - @witensea"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💎 Купить имбу", callback_data="create_payment")
    ], [
        InlineKeyboardButton(text="« Назад", callback_data="back_to_start")
    ]])
    
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "back_to_start")
async def back_to_start_callback(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 Купить доступ", callback_data="buy_access")
    ]])
    try:
        await call.message.edit_text("<b>Привет люти воркер</b>", reply_markup=kb)
    except:
        await call.message.answer("<b>Привет люти воркер</b>", reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "create_payment")
async def create_payment_callback(call: CallbackQuery):
    if not call.from_user:
        await call.answer("Ошибка")
        return
    
    if not cfg.ton_wallet:
        await call.answer("⚠️ Платежи временно недоступны", show_alert=True)
        return
    
    # Генерируем уникальный memo
    memo = secrets.token_hex(8).upper()
    
    # Создаём платёж в БД
    payment_id = db.create_payment(call.from_user.id, memo, cfg.payment_amount)
    
    # Формируем deeplink для TonKeeper
    amount_nano = int(cfg.payment_amount * 1_000_000_000)  # конвертируем в nanoTON
    tonkeeper_url = f"https://app.tonkeeper.com/transfer/{cfg.ton_wallet}?amount={amount_nano}&text={memo}"
    
    text = (f"💎 <b>Оплата доступа</b>\n\n"
            f"Сумма: <b>{cfg.payment_amount:g} Grаm</b>\n"
            f"Кошелёк: <code>{cfg.ton_wallet}</code>\n\n"
            f"⚠️ <b>ВАЖНО!</b> При отправке укажите комментарий (memo):\n"
            f"<code>{memo}</code>\n\n"
            f"Без memo платёж не будет засчитан!")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💎 Открыть TonKeeper", url=tonkeeper_url)
    ], [
        InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_payment:{memo}")
    ], [
        InlineKeyboardButton(text="« Назад", callback_data="buy_access")
    ]])
    
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()
    
    # Уведомляем овнера о новом платеже
    try:
        owner_text = (f"💰 <b>Новый платёж</b>\n\n"
                     f"Пользователь: {display_name(call.from_user)}\n"
                     f"ID: <code>{call.from_user.id}</code>\n"
                     f"Username: @{call.from_user.username or 'нет'}\n\n"
                     f"Сумма: <b>{cfg.payment_amount:g} Grаm</b>\n"
                     f"Memo: <code>{memo}</code>")
        owner_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"approve_payment:{memo}:{call.from_user.id}")
        ]])
        await bot.send_message(cfg.owner_id, owner_text, reply_markup=owner_kb)
    except Exception as e:
        log.error("Failed to notify owner about payment: %s", e)


@dp.callback_query(F.data.startswith("check_payment:"))
async def check_payment_callback(call: CallbackQuery):
    if not call.from_user:
        await call.answer("Ошибка")
        return
    
    memo = call.data.split(":", 1)[1]
    payment = db.get_payment_by_memo(memo)
    
    if not payment:
        await call.answer("❌ Платёж не найден", show_alert=True)
        return
    
    if payment["user_id"] != call.from_user.id:
        await call.answer("❌ Это не ваш платёж", show_alert=True)
        return
    
    if payment["status"] == "paid":
        await call.answer("✅ Платёж уже обработан", show_alert=True)
        return
    
    # Проверяем транзакцию в блокчейне
    await call.answer("⏳ Проверяю блокчейн...")
    
    if not cfg.ton_wallet:
        await call.answer("⚠️ Кошелёк не настроен", show_alert=True)
        return
    
    # Проверяем наличие транзакции
    is_paid = await check_ton_payment(cfg.ton_wallet, memo, payment["amount"])
    
    if is_paid:
        # Подтверждаем оплату
        db.mark_payment_paid(payment["id"])
        db.add_access(call.from_user.id)
        db.unblock_offer_user(call.from_user.id)
        
        # Отправляем приветственное сообщение
        success_text = ("✅ <b>Спасибо за покупку, удачного ворка!</b>\n\n"
                       "<b>Инструкция:</b>\n"
                       "<code>офер [ссылка нфт] [кол-во] зв/грам</code>\n\n"
                       "Пример:\n"
                       "<code>офер https://t.me/nft/PartySparkler-140864 150 грам</code>")
        
        try:
            await call.message.edit_text(success_text)
        except:
            await call.message.answer(success_text)
        
        # Уведомляем овнера
        try:
            await bot.send_message(cfg.owner_id, 
                f"✅ Автоматически подтверждён платёж\n\n"
                f"Пользователь: {display_name(call.from_user)}\n"
                f"ID: <code>{call.from_user.id}</code>\n"
                f"Memo: <code>{memo}</code>\n"
                f"Сумма: {payment['amount']:g} Grаm")
        except:
            pass
        
        await call.answer("✅ Оплата подтверждена! Доступ выдан.", show_alert=True)
    else:
        await call.answer("❌ Транзакция не найдена.\n\nПроверьте что:\n• Вы отправили нужную сумму\n• Указали правильный memo\n• Прошло несколько секунд после отправки", show_alert=True)


@dp.callback_query(F.data.startswith("approve_payment:"))
async def approve_payment_callback(call: CallbackQuery):
    """Ручное подтверждение оплаты овнером"""
    if not call.from_user or call.from_user.id != cfg.owner_id:
        await call.answer("Доступ запрещён", show_alert=True)
        return
    
    parts = call.data.split(":")
    if len(parts) != 3:
        await call.answer("Ошибка формата")
        return
    
    memo = parts[1]
    user_id = int(parts[2])
    
    payment = db.get_payment_by_memo(memo)
    if not payment:
        await call.answer("Платёж не найден", show_alert=True)
        return
    
    if payment["status"] == "paid":
        await call.answer("Уже обработан", show_alert=True)
        return
    
    # Подтверждаем оплату
    db.mark_payment_paid(payment["id"])
    db.add_access(user_id)
    db.unblock_offer_user(user_id)
    
    # Уведомляем пользователя
    success_text = ("✅ <b>Спасибо за покупку, удачного ворка!</b>\n\n"
                   "<b>Инструкция:</b>\n"
                   "<code>офер [ссылка нфт] [кол-во] зв/грам</code>\n\n"
                   "Пример:\n"
                   "<code>офер https://t.me/nft/PartySparkler-140864 150 грам</code>")
    try:
        await bot.send_message(user_id, success_text)
    except:
        pass
    
    await call.answer("✅ Оплата подтверждена, доступ выдан")
    try:
        await call.message.edit_text(f"{call.message.text}\n\n✅ ПОДТВЕРЖДЕНО")
    except:
        pass
async def admin_callback(call: CallbackQuery):
    if not call.from_user or call.from_user.id != cfg.owner_id:
        await call.answer("Доступ запрещён.", show_alert=True); return
    _, action, raw = call.data.split(":", 2)
    uid = int(raw)
    if action == "allow":
        await admin_allow(uid); await call.answer("Разрешено")
    elif action == "block":
        await admin_block(uid); await call.answer("Заблокировано")
    else:
        await call.answer("Неизвестное действие", show_alert=True)


@dp.callback_query(F.data.startswith("adm:"))
async def admin_callback(call: CallbackQuery):
    if not call.from_user or call.from_user.id != cfg.owner_id:
        await call.answer("Доступ запрещён.", show_alert=True); return
    _, action, raw = call.data.split(":", 2)
    uid = int(raw)
    if action == "allow":
        await admin_allow(uid); await call.answer("Разрешено")
    elif action == "block":
        await admin_block(uid); await call.answer("Заблокировано")
    else:
        await call.answer("Неизвестное действие", show_alert=True)


@dp.callback_query(F.data.startswith("of:"))
async def offer_callback(call: CallbackQuery):
    parts = call.data.split(":")
    if len(parts) != 3 or parts[1] not in {"accept", "decline"} or not parts[2].isdigit():
        await call.answer("Некорректный офер.", show_alert=True); return
    offer_id = int(parts[2])
    if offer_id not in offer_locks:
        offer_locks[offer_id] = asyncio.Lock()
    async with offer_locks[offer_id]:
        offer = db.get_offer(offer_id)
        if not offer or offer["status"] != "pending":
            await call.answer("Офер уже обработан.", show_alert=True); return
        if not call.message or call.message.message_id != offer["message_id"] or call.message.chat.id != offer["message_chat_id"]:
            await call.answer("Офер недействителен.", show_alert=True); return
        if parts[1] == "accept":
            url = offer["transfer_url"] or f"tg://user?id={offer['seller_id']}"
            title = gift_title(offer["gift_name"], offer["gift_number"])
            gift_url = offer["gift_url"]
            if offer["locale"] == "ru":
                final = f"<tg-emoji emoji-id=\"{GIFT_EMOJI_ID}\">🎁</tg-emoji> Gift: <a href=\"{gift_url}\">{title}</a> успешно продан!\n\n• Передайте подарок покупателю для зачисления {display_amount(offer['amount'], offer['currency'])} на ваш баланс."
            else:
                final = f"<tg-emoji emoji-id=\"{GIFT_EMOJI_ID}\">🎁</tg-emoji> Gift: <a href=\"{gift_url}\">{title}</a> successfully sold!\n\n• Transfer the gift to the buyer to credit {display_amount(offer['amount'], offer['currency'])} to your balance."
            try:
                sent = await safe_send_business(offer["business_connection_id"], offer["buyer_id"], text=final,
                    reply_markup=transfer_keyboard(url, offer["locale"]),
                    link_preview_options=LinkPreviewOptions(url=gift_url, show_above_text=True))
                try:
                    await bot.delete_business_messages(business_connection_id=offer["business_connection_id"], message_ids=[offer["message_id"]])
                except Exception as e: log.warning("Could not delete accepted offer: %s", e)
                db.set_offer_message(offer_id, offer["buyer_id"], sent.message_id)
                db.update_offer_status(offer_id, "accepted", url)
                log.info("[OFFER ACCEPT] offer_id=%s seller_id=%s buyer_id=%s", offer_id, offer["seller_id"], offer["buyer_id"])
                if offer["locale"] == "ru":
                    await call.answer(f"Ваш подарок {title} был успешно продан.\nДля зачисления {display_amount(offer['amount'], offer['currency'])} передайте подарок покупателю.", show_alert=True)
                else:
                    await call.answer(f"Your gift {title} was successfully sold.\nTo credit {display_amount(offer['amount'], offer['currency'])}, transfer the gift to the buyer.", show_alert=True)
            except Exception:
                await call.answer("Не удалось завершить принятие офера. Попробуйте ещё раз.", show_alert=True)
        else:
            title = gift_title(offer["gift_name"], offer["gift_number"])
            gift_url = offer["gift_url"]
            if offer["locale"] == "ru":
                final = f"<tg-emoji emoji-id=\"{DECLINE_EMOJI_ID}\">❌</tg-emoji> <b>Предложение отменено.</b>"
            else:
                final = f"<tg-emoji emoji-id=\"{DECLINE_EMOJI_ID}\">❌</tg-emoji> <b>Offer declined.</b>"
            try:
                sent = await safe_send_business(offer["business_connection_id"], offer["buyer_id"], text=final)
                try:
                    await bot.delete_business_messages(business_connection_id=offer["business_connection_id"], message_ids=[offer["message_id"]])
                except Exception as e: log.warning("Could not delete declined offer: %s", e)
                db.set_offer_message(offer_id, offer["buyer_id"], sent.message_id)
                db.update_offer_status(offer_id, "declined")
                log.info("[OFFER DECLINE] offer_id=%s", offer_id)
                await call.answer("Предложение отклонено.")
            except Exception:
                await call.answer("Не удалось отклонить офер. Попробуйте ещё раз.", show_alert=True)


async def main():
    for uid in cfg.offer_always_allowed_ids:
        db.add_access(uid); db.unblock_offer_user(uid)
    log.info("OfferBot starting; owner_id=%s database=%s", cfg.owner_id, cfg.database_path)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
