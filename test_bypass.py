#!/usr/bin/env python3
"""Тестирование команд обхода peer_flood"""

from offer import parse_offer, display_amount

# Тестовые команды
test_commands = [
    "гой https://t.me/nft/XmasStocking-215433 150 stars",
    "гой1 https://t.me/nft/XmasStocking-215433 150 stars",
    "гой2 https://t.me/nft/XmasStocking-215433 100 gram",
    "гой3 https://t.me/nft/XmasStocking-215433 200 stars",
    "гой4 https://t.me/nft/XmasStocking-215433 150 gram",
    ".гой https://t.me/nft/DeliciousCake-1 150 stars",
    ".гой1 https://t.me/nft/DeliciousCake-1 500 stars",
    "оф1 https://t.me/nft/PartySparkler-140864 300 gram",
    "оф2 https://t.me/nft/PartySparkler-140864 300 stars",
    "ху https://t.me/nft/DeliciousCake-1 100 stars",  # обычная команда для сравнения
]

print("=" * 60)
print("ТЕСТИРОВАНИЕ КОМАНД ОБХОДА PEER_FLOOD")
print("=" * 60)

for cmd in test_commands:
    print(f"\n📝 Команда: {cmd}")
    result = parse_offer(cmd)
    
    if "error" in result:
        print(f"   ❌ Ошибка: {result['error']}")
        continue
    
    bypass_mode = result.get("bypass_mode", 0)
    print(f"   ✅ Распознано успешно!")
    print(f"   🎁 NFT: {result['gift_name']}-{result['gift_number']}")
    print(f"   💰 Цена: {result['amount']} {result['currency']}")
    print(f"   🔧 Режим обхода: {bypass_mode}")
    
    # Тестируем отображение
    amount_emoji = display_amount(result['amount'], result['currency'], with_emoji=True, bypass_mode=bypass_mode)
    amount_plain = display_amount(result['amount'], result['currency'], with_emoji=False, bypass_mode=bypass_mode)
    
    print(f"   📊 С эмодзи: {amount_emoji}")
    print(f"   📊 Без эмодзи: {amount_plain}")
    print(f"   📏 Длина (plain): {len(amount_plain)} символов")
    # Показываем Unicode коды для режима 1 и 4
    if bypass_mode in [1, 4]:
        codes = [f"U+{ord(c):04X}" for c in amount_plain.split()[-1]]
        print(f"   🔍 Unicode: {' '.join(codes)}")

print("\n" + "=" * 60)
print("РЕЖИМЫ ОБХОДА:")
print("=" * 60)
print("гой   - Эмодзи (по умолчанию)")
print("гой1  - Русская 'а' (Stаrs / Grаm)")
print("гой2  - Пробелы (S t a r s / G r a m)")
print("гой3  - Синонимы (звёзды / граммы)")
print("гой4  - Zero-width space (S​tars / G​ram)")
print("=" * 60)
