-- Миграция: Система ключей активации для твинков
-- Дата: 2024

-- Добавляем поле activation_key в таблицу access_grants
ALTER TABLE access_grants 
ADD COLUMN IF NOT EXISTS activation_key TEXT UNIQUE;

-- Добавляем поле linked_to_user_id для твинков (кто является основным аккаунтом)
ALTER TABLE access_grants
ADD COLUMN IF NOT EXISTS linked_to_user_id BIGINT;

-- Создаём индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_access_grants_activation_key ON access_grants(activation_key);
CREATE INDEX IF NOT EXISTS idx_access_grants_linked_to ON access_grants(linked_to_user_id);

-- Комментарии для полей
COMMENT ON COLUMN access_grants.activation_key IS 'Уникальный ключ для активации твинка (только у основных аккаунтов)';
COMMENT ON COLUMN access_grants.linked_to_user_id IS 'ID основного аккаунта (если это твинк)';
