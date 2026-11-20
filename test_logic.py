#!/usr/bin/env python3
"""Тест логики with_emoji"""

def test_logic():
    # Симулируем настройку
    offer_amount_style = "premium"  # или "plain"
    
    test_cases = [
        {"bypass_mode": 0, "desc": "гой (обычный, режим 0)"},
        {"bypass_mode": 1, "desc": "гой1 (русская 'а')"},
        {"bypass_mode": 2, "desc": "гой2 (пробелы)"},
        {"bypass_mode": 3, "desc": "гой3 (синонимы)"},
        {"bypass_mode": 4, "desc": "гой4 (zero-width)"},
    ]
    
    print("=" * 60)
    print(f"НАСТРОЙКА: offer_amount_style = '{offer_amount_style}'")
    print("=" * 60)
    
    for case in test_cases:
        bypass_mode = case["bypass_mode"]
        
        # Новая логика
        use_emoji = (offer_amount_style != "plain") and (bypass_mode == 0)
        
        print(f"\n{case['desc']}")
        print(f"  bypass_mode = {bypass_mode}")
        print(f"  use_emoji = {use_emoji}")
        print(f"  → {'ЭМОДЗИ ⭐💎' if use_emoji else 'ТЕКСТ (обход)'}")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    test_logic()
