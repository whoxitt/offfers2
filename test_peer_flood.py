"""Тестовые команды для обхода peer_flood при работе с офферами.

Telegram блокирует сообщения со словом "предлагает" через peer_flood.
Эти команды тестируют разные способы обхода триггера.
"""

# Тестовые данные
TEST_NFT_URL = "https://t.me/nft/PartySparkler-140864"
TEST_AMOUNT = "150 gram"
TEST_SELLER = "TestUser"

# =============================================================================
# ТЕСТ1: Полный оффер БЕЗ слова "предлагает"
# =============================================================================

def test1_no_trigger_word():
    """Версия без слова 'предлагает' - должна работать без блокировки."""
    
    offer_text = (
        f'<b>{TEST_SELLER}</b> хочет купить у Вас <b>{TEST_AMOUNT}</b> за подарок '
        f'<a href="{TEST_NFT_URL}">Party Sparkler #140864</a>.\n\n'
        f'Это предложение активно ещё <b>5ч 59м</b>.'
    )
    
    return {
        'name': 'ТЕСТ1 - без триггерного слова',
        'text': offer_text,
        'description': 'Используется "хочет купить" вместо "предлагает"'
    }


# =============================================================================
# ТЕСТ2: Полный оффер со словом "предлагает", БЕЗ фразы "сделка активна"
# =============================================================================

def test2_with_trigger_no_timer():
    """Версия со словом 'предлагает' но без таймера активности."""
    
    offer_text = (
        f'<b>{TEST_SELLER}</b> предлагает Вам <b>{TEST_AMOUNT}</b> за подарок '
        f'<a href="{TEST_NFT_URL}">Party Sparkler #140864</a>.'
    )
    
    return {
        'name': 'ТЕСТ2 - с триггером без таймера',
        'text': offer_text,
        'description': 'Есть "предлагает" но нет фразы про активность'
    }


# =============================================================================
# ТЕСТ3: Разные способы обхода слова "предлагает"
# =============================================================================

def test3_1_zero_width_space():
    """Невидимый символ нулевой ширины между буквами."""
    
    # \u200B - zero-width space (невидимый символ)
    word = "пред\u200Bлагает"
    
    offer_text = (
        f'<b>{TEST_SELLER}</b> {word} Вам <b>{TEST_AMOUNT}</b> за подарок '
        f'<a href="{TEST_NFT_URL}">Party Sparkler #140864</a>.\n\n'
        f'Это предложение активно ещё <b>5ч 59м</b>.'
    )
    
    return {
        'name': 'ТЕСТ3-1 - zero-width space',
        'text': offer_text,
        'description': 'Невидимый символ между "пред" и "лагает"'
    }


def test3_2_invisible_separator():
    """Невидимый разделитель (invisible separator)."""
    
    # \u2063 - invisible separator
    word = "предла\u2063гает"
    
    offer_text = (
        f'<b>{TEST_SELLER}</b> {word} Вам <b>{TEST_AMOUNT}</b> за подарок '
        f'<a href="{TEST_NFT_URL}">Party Sparkler #140864</a>.\n\n'
        f'Это предложение активно ещё <b>5ч 59м</b>.'
    )
    
    return {
        'name': 'ТЕСТ3-2 - invisible separator',
        'text': offer_text,
        'description': 'Невидимый разделитель внутри слова'
    }


def test3_3_word_joiner():
    """Word joiner - символ соединения слов."""
    
    # \u2060 - word joiner
    word = "предлаг\u2060ает"
    
    offer_text = (
        f'<b>{TEST_SELLER}</b> {word} Вам <b>{TEST_AMOUNT}</b> за подарок '
        f'<a href="{TEST_NFT_URL}">Party Sparkler #140864</a>.\n\n'
        f'Это предложение активно ещё <b>5ч 59м</b>.'
    )
    
    return {
        'name': 'ТЕСТ3-3 - word joiner',
        'text': offer_text,
        'description': 'Word joiner символ внутри слова'
    }


def test3_4_html_entity():
    """HTML entity для одной из букв."""
    
    # Используем HTML entity для буквы 'е'
    word = "пр&#1077;длагает"  # &#1077; = е (кириллица)
    
    offer_text = (
        f'<b>{TEST_SELLER}</b> {word} Вам <b>{TEST_AMOUNT}</b> за подарок '
        f'<a href="{TEST_NFT_URL}">Party Sparkler #140864</a>.\n\n'
        f'Это предложение активно ещё <b>5ч 59м</b>.'
    )
    
    return {
        'name': 'ТЕСТ3-4 - HTML entity',
        'text': offer_text,
        'description': 'Одна буква заменена на HTML entity'
    }


def test3_5_similar_cyrillic():
    """Похожие символы из других кириллических алфавитов."""
    
    # Используем е с ударением (ѐ U+0450)
    word = "прѐдлагает"
    
    offer_text = (
        f'<b>{TEST_SELLER}</b> {word} Вам <b>{TEST_AMOUNT}</b> за подарок '
        f'<a href="{TEST_NFT_URL}">Party Sparkler #140864</a>.\n\n'
        f'Это предложение активно ещё <b>5ч 59м</b>.'
    )
    
    return {
        'name': 'ТЕСТ3-5 - похожий кириллический символ',
        'text': offer_text,
        'description': 'Буква "е" заменена на "ѐ" (с ударением)'
    }


def test3_6_formatting_break():
    """Разрыв через форматирование (тег внутри слова)."""
    
    # HTML тег внутри слова
    word = "пред<i></i>лагает"
    
    offer_text = (
        f'<b>{TEST_SELLER}</b> {word} Вам <b>{TEST_AMOUNT}</b> за подарок '
        f'<a href="{TEST_NFT_URL}">Party Sparkler #140864</a>.\n\n'
        f'Это предложение активно ещё <b>5ч 59м</b>.'
    )
    
    return {
        'name': 'ТЕСТ3-6 - пустой HTML тег',
        'text': offer_text,
        'description': 'Пустой <i></i> тег разрывает слово'
    }


def test3_7_soft_hyphen():
    """Мягкий перенос (soft hyphen)."""
    
    # \u00AD - soft hyphen (мягкий перенос)
    word = "пред\u00ADлагает"
    
    offer_text = (
        f'<b>{TEST_SELLER}</b> {word} Вам <b>{TEST_AMOUNT}</b> за подарок '
        f'<a href="{TEST_NFT_URL}">Party Sparkler #140864</a>.\n\n'
        f'Это предложение активно ещё <b>5ч 59м</b>.'
    )
    
    return {
        'name': 'ТЕСТ3-7 - soft hyphen',
        'text': offer_text,
        'description': 'Мягкий перенос внутри слова'
    }


def test3_8_combining_char():
    """Комбинированный диакритический символ."""
    
    # \u0301 - combining acute accent (ударение, невидимое сверху)
    word = "пре\u0301длагает"
    
    offer_text = (
        f'<b>{TEST_SELLER}</b> {word} Вам <b>{TEST_AMOUNT}</b> за подарок '
        f'<a href="{TEST_NFT_URL}">Party Sparkler #140864</a>.\n\n'
        f'Это предложение активно ещё <b>5ч 59м</b>.'
    )
    
    return {
        'name': 'ТЕСТ3-8 - combining character',
        'text': offer_text,
        'description': 'Комбинирующий символ ударения'
    }


def test3_9_rtl_mark():
    """Right-to-left mark (может нарушить распознавание)."""
    
    # \u200F - right-to-left mark
    word = "предла\u200Fгает"
    
    offer_text = (
        f'<b>{TEST_SELLER}</b> {word} Вам <b>{TEST_AMOUNT}</b> за подарок '
        f'<a href="{TEST_NFT_URL}">Party Sparkler #140864</a>.\n\n'
        f'Это предложение активно ещё <b>5ч 59м</b>.'
    )
    
    return {
        'name': 'ТЕСТ3-9 - RTL mark',
        'text': offer_text,
        'description': 'Right-to-left mark внутри слова'
    }


def test3_10_mixed_latin():
    """Похожие латинские буквы вместо кириллических."""
    
    # Латинские e, a, o похожи на кириллические
    word = "прeдлaгaeт"  # e, a - латинские
    
    offer_text = (
        f'<b>{TEST_SELLER}</b> {word} Вам <b>{TEST_AMOUNT}</b> за подарок '
        f'<a href="{TEST_NFT_URL}">Party Sparkler #140864</a>.\n\n'
        f'Это предложение активно ещё <b>5ч 59м</b>.'
    )
    
    return {
        'name': 'ТЕСТ3-10 - латинские буквы',
        'text': offer_text,
        'description': 'Некоторые буквы заменены на похожие латинские'
    }


# =============================================================================
# Список всех тестов
# =============================================================================

ALL_TESTS = [
    test1_no_trigger_word,
    test2_with_trigger_no_timer,
    test3_1_zero_width_space,
    test3_2_invisible_separator,
    test3_3_word_joiner,
    test3_4_html_entity,
    test3_5_similar_cyrillic,
    test3_6_formatting_break,
    test3_7_soft_hyphen,
    test3_8_combining_char,
    test3_9_rtl_mark,
    test3_10_mixed_latin,
]


def print_all_tests():
    """Вывести все тестовые сообщения."""
    print("=" * 80)
    print("ТЕСТОВЫЕ КОМАНДЫ ДЛЯ ОБХОДА PEER_FLOOD")
    print("=" * 80)
    print()
    
    for test_func in ALL_TESTS:
        result = test_func()
        print(f"\n{'=' * 80}")
        print(f"{result['name']}")
        print(f"Описание: {result['description']}")
        print(f"{'-' * 80}")
        print(result['text'])
        print(f"{'=' * 80}\n")


def get_test_by_number(number: int):
    """Получить тест по номеру (1-12)."""
    if 1 <= number <= len(ALL_TESTS):
        return ALL_TESTS[number - 1]()
    return None


if __name__ == '__main__':
    print_all_tests()
