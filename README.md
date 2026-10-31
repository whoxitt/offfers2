# OfferBot

Telegram-бот для создания и отправки NFT-оферов через Telegram Business.

## Что умеет бот

- Бесплатный доступ для всех пользователей.
- Создание оферов на NFT в Stars, Gram или рублях.
- Русский, английский, китайский и арабский языки.
- Принятие и отклонение оферов, кнопка передачи подарка.
- Личный кабинет с данными пользователя, статусом Business и настройкой получателя.
- Реферальная программа и поддержка.
- Администратор может заблокировать нарушителя командой `/block USER_ID`.

## Как пользоваться

1. Откройте Telegram → **Настройки → Telegram для бизнеса → Чат-боты**.
2. Подключите OfferBot и разрешите доступ к сообщениям.
3. В Business-чате отправьте офер:

   `офер https://t.me/nft/PartySparkler-140864 150 stars`

Валюта: `stars`, `gram` или `2000 р`. Язык в конце команды: `en`, `cn` или `ar`. По умолчанию русский.

Чтобы указать получателя подарка, отправьте `/transfer @username`. Личный кабинет и инструкция доступны в меню бота.

## Запуск на Railway

1. Создайте сервис **PostgreSQL**.
2. Подключите репозиторий с файлами проекта как сервис бота.
3. В Variables добавьте:
   - `BOT_TOKEN` — токен от BotFather.
   - `OWNER_ID` — числовой Telegram ID администратора.
   - `DATABASE_URL` — Reference Variable PostgreSQL, например `${{Postgres.DATABASE_URL}}`.
4. Start Command: `python bot.py`.
5. Запустите деплой и проверьте логи.

При первом запуске бот автоматически создаёт таблицы PostgreSQL.

## Дополнительные настройки

- `REQUIRE_SUBSCRIPTION=false` — подписка на канал не требуется (по умолчанию).
- `REQUIRE_SUBSCRIPTION=true` — включить обязательную подписку.
- `CHANNEL_ID` и `CHANNEL_URL` — ID и ссылка канала, если подписка включена.
- `SUPPORT_URL` — контакт поддержки.
- `USERS_CHAT_URL` — ссылка на чат пользователей.
- `OFFER_EXPIRE_HOURS=24` — срок действия офера.
- `BUSINESS_DEBUG=0` — отключить подробные Business-логи.

Платёжные настройки больше не нужны. `TON_WALLET`, `PAYMENT_AMOUNT` и `CRYPTOPAY_API_TOKEN` можно удалить из Variables.

## Проверка

```bash
python -m py_compile bot.py config.py database.py offer.py
```

Для работы нужны действующие `BOT_TOKEN`, `OWNER_ID` и `DATABASE_URL`. `.env` не добавляйте в GitHub.
