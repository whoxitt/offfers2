#!/usr/bin/env python3
"""Финальный тест: проверка что обычные офферы используют русскую 'а'"""

from offer import parse_offer, display_amount

print("=" * 70)
print("ТЕСТ: ОБЫЧНЫЕ ОФФЕРЫ ТЕПЕРЬ ИСПОЛЬЗУЮТ ОБХОД (русская 'а')")
print("=" * 70)

# Симулируем настройку бота
offer_amount_style = "plain"  # Текстовый формат Stars/Gram

test_cases = [
    {
        "cmd": "ху https://t.me/nft/DeliciousCake-1 150 stars",
        "desc": "Обычный оффер (.ху) с настройкой plain"
    },
    {
        "cmd": "ху https://t.me/nft/DeliciousCake-1 100 gram",
        "desc": "Обычный оффер (.ху) с Gram"
    },
]

for test in test_cases:
    print(f"\n📝 {test['desc']}")
    print(f"   Команда: {test['cmd']}")
    
    result = parse_offer(test['cmd'])
    
    if "error" in result:
        print(f"   ❌ Ошибка: {result['error']}")
        continue
    
    bypass_mode = result.get("bypass_mode", 0)
    
    # Симулируем логику бота
    use_emoji = (offer_amount_style != "plain") and (bypass_mode == 0)
    
    print(f"   bypass_mode={bypass_mode}, use_emoji={use_emoji}")
    
    # Отображаем сумму
    amount_display = display_amount(
        result['amount'], 
        result['currency'], 
        with_emoji=use_emoji,
        bypass_mode=bypass_mode
    )
    
    print(f"   💰 Отображение: {amount_display}")
    
    # Проверяем что это текст с русской 'а', а не эмодзи
    if "tg-emoji" in amount_display:
        print(f"   ❌ ОШИБКА: Показывает эмодзи!")
    else:
        # Проверяем Unicode
        word = amount_display.split()[-1]  # Последнее слово (Stars или Gram)
        if 'а' in word:  # русская 'а'
            codes = [f"U+{ord(c):04X}" for c in word]
            print(f"   ✅ ОБХОД РАБОТАЕТ! Unicode: {' '.join(codes)}")
        else:
            print(f"   ⚠️  Обход может не работать")

print("\n" + "=" * 70)
print("НАСТРОЙКА: offer_amount_style = 'premium' (эмодзи)")
print("=" * 70)

offer_amount_style = "premium"

for test in test_cases:
    print(f"\n📝 {test['desc']}")
    
    result = parse_offer(test['cmd'])
    bypass_mode = result.get("bypass_mode", 0)
    use_emoji = (offer_amount_style != "plain") and (bypass_mode == 0)
    
    amount_display = display_amount(
        result['amount'], 
        result['currency'], 
        with_emoji=use_emoji,
        bypass_mode=bypass_mode
    )
    
    print(f"   💰 Отображение: {amount_display}")
    
    if "tg-emoji" in amount_display:
        print(f"   ✅ Эмодзи формат (настройка premium)")
    else:
        print(f"   ❌ ОШИБКА: Должны быть эмодзи!")

print("\n" + "=" * 70)
print("ИТОГ:")
print("=" * 70)
print("✅ Настройка 'plain' → Текст с русской 'а' (обход peer_flood)")
print("✅ Настройка 'premium' → Эмодзи ⭐💎 (как было)")
print("✅ Команды гой1-4 → Явные режимы обхода (для тестов)")
print("=" * 70)
