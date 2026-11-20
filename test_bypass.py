#!/usr/bin/env python3
"""Тестирование команд обхода peer_flood"""

from offer import parse_offer, display_amount

# Тестовые команды
test_commands = [
    ("гой https://t.me/nft/XmasStocking-215433 150 stars", "Обычная (эмодзи)"),
    ("гой1 https://t.me/nft/XmasStocking-215433 150 stars", "Русская 'а'"),
    ("гой2 https://t.me/nft/XmasStocking-215433 100 gram", "Пробелы"),
    ("гой3 https://t.me/nft/XmasStocking-215433 200 stars", "Синонимы"),
    ("гой4 https://t.me/nft/XmasStocking-215433 150 gram", "Zero-width space"),
]

print("=" * 70)
print("ТЕСТИРОВАНИЕ КОМАНД ОБХОДА PEER_FLOOD")
print("=" * 70)

for cmd, name in test_commands:
    print(f"\n📝 {name}: {cmd}")
    result = parse_offer(cmd)
    
    if "error" in result:
        print(f"   ❌ Ошибка: {result['error']}")
        continue
    
    bypass_mode = result.get("bypass_mode", 0)
    
    # Симулируем логику бота
    offer_amount_style = "premium"  # настройка по умолчанию
    use_emoji = (offer_amount_style != "plain") and (bypass_mode == 0)
    
    print(f"   ✅ Распознано! bypass_mode={bypass_mode}, use_emoji={use_emoji}")
    print(f"   🎁 NFT: {result['gift_name']}-{result['gift_number']}")
    print(f"   💰 Цена: {result['amount']} {result['currency']}")
    
    # Отображаем сумму
    amount_display = display_amount(
        result['amount'], 
        result['currency'], 
        with_emoji=use_emoji,
        bypass_mode=bypass_mode
    )
    
    print(f"   📊 Отображение: {amount_display}")
    
    if bypass_mode > 0:
        # Показываем что это ТЕКСТ, не эмодзи
        if "tg-emoji" not in amount_display:
            print(f"   ✅ ТЕКСТОВЫЙ ОБХОД РАБОТАЕТ!")
        else:
            print(f"   ❌ ОШИБКА: Показывает эмодзи вместо текста!")

print("\n" + "=" * 70)
print("ИТОГ:")
print("=" * 70)
print("гой   → Эмодзи (может вызвать peer_flood)")
print("гой1  → 150 Stаrs (русская 'а')")
print("гой2  → 100 G r a m (пробелы)")
print("гой3  → 200 звёзды (синонимы)")
print("гой4  → 150 G​r​am (zero-width space)")
print("=" * 70)

