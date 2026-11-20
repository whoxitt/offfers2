-- Добавляем колонку для хранения режима обхода peer_flood
ALTER TABLE gift_offers ADD COLUMN IF NOT EXISTS bypass_mode INTEGER NOT NULL DEFAULT 0;
