-- Сброс БД новогоднего ивента (ручной вариант, если /delete_db недоступен).
-- Выполнить: psql -U xmas -d xmas -f reset_db.sql
-- Whitelist НЕ трогаем — это список участников, а не игровые данные.

BEGIN;
DELETE FROM event_log;
DELETE FROM achievements;
DELETE FROM users;
COMMIT;

-- Для полного сброса (включая whitelist):
-- DELETE FROM whitelist;
