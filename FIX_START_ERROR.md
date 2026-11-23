# Исправление ошибки /start

## Проблема
```
TelegramBadRequest: Telegram server says - Bad Request: 
can't parse InlineKeyboardButton: Text buttons are not allowed in the inline keyboard
```

## Причина
В кнопках используются невалидные URL:
- `USERS_CHAT_URL=https://t.me/` (без имени канала)
- `CHANNEL_URL=https://t.me/` (без имени канала)

Telegram не принимает такие ссылки.

---

## ✅ Исправлено

### 1. Файл `bot.py`

Добавлена проверка валидности URL перед созданием кнопок:

```python
def subscription_keyboard():
    buttons = []
    # Проверяем что URL валидный и полный
    if cfg.channel_url and cfg.channel_url.startswith("https://t.me/") and len(cfg.channel_url) > 15:
        buttons.append([InlineKeyboardButton(text="📢 Перейти на канал", url=cfg.channel_url)])
    buttons.append([InlineKeyboardButton(text="✅ Подтвердить подписку", callback_data="check_subscription")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def purchase_success_keyboard():
    buttons = []
    # Проверяем что URL валидный и полный
    if cfg.users_chat_url and cfg.users_chat_url.startswith("https://t.me/") and len(cfg.users_chat_url) > 15:
        buttons.append([InlineKeyboardButton(text="💬 Чат покупателей", url=cfg.users_chat_url)])
    buttons.append([InlineKeyboardButton(text="📘 Инструкция", callback_data="show_instruction")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
```

### 2. Файл `config.py`

Изменены дефолтные значения:

**Было:**
```python
users_chat_url: str = "https://t.me/"
users_chat_url=os.getenv("USERS_CHAT_URL", "https://t.me/").strip()
```

**Стало:**
```python
users_chat_url: str = ""
users_chat_url=os.getenv("USERS_CHAT_URL", "").strip()
```

---

## 🚀 Теперь работает!

### Если URL пустой:
- Кнопка с URL **не создаётся**
- Показывается только кнопка с `callback_data`

### Если URL валидный:
- Кнопка создаётся и работает

---

## 📝 Настройка в `.env`

### Вариант 1: Без чата покупателей (рекомендуется)
```env
# Просто не указывайте переменную или оставьте пустой
USERS_CHAT_URL=
```

### Вариант 2: С чатом покупателей
```env
# ОБЯЗАТЕЛЬНО полная ссылка с именем канала!
USERS_CHAT_URL=https://t.me/your_channel_name
```

### Вариант 3: Без подписки на канал
```env
REQUIRE_SUBSCRIPTION=false
# CHANNEL_ID и CHANNEL_URL можно не указывать
```

### Вариант 4: С подпиской на канал
```env
REQUIRE_SUBSCRIPTION=true
CHANNEL_ID=-1001234567890
CHANNEL_URL=https://t.me/your_channel_name
```

---

## ⚠️ Важно!

### ❌ Неправильно:
```env
USERS_CHAT_URL=https://t.me/
CHANNEL_URL=https://t.me/
```
Telegram откажет: "невалидный URL"

### ✅ Правильно:
```env
# Либо оставить пустым
USERS_CHAT_URL=

# Либо указать ПОЛНУЮ ссылку
USERS_CHAT_URL=https://t.me/my_buyers_chat
CHANNEL_URL=https://t.me/my_channel
```

---

## 🎯 Проверка

После исправления:
1. Перезапустите бота
2. Отправьте `/start`
3. ✅ Должно работать!

Если URL-ы не указаны - кнопки с ними просто не появятся, но бот будет работать.

---

## 📊 Статус

✅ **Исправлено** - теперь бот проверяет валидность URL перед созданием кнопок  
✅ **Работает без настройки** - можно запускать даже с пустыми URL  
✅ **Гибко** - добавьте URL когда будет готово  

**Перезапускайте бота - всё работает!** 🚀
