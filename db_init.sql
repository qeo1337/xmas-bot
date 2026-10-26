-- Схема БД новогоднего ивента. Применяется автоматически при старте контейнера db.
-- tg_id — числовой идентификатор пользователя платформы (Telegram ID или id из update Мессенджера).

CREATE TABLE IF NOT EXISTS whitelist (
    username      TEXT PRIMARY KEY     -- в TG-версии: telegram username без @; в версии Мессенджера: staff login
);

CREATE TABLE IF NOT EXISTS users (
    tg_id            BIGINT PRIMARY KEY,
    username         TEXT,             -- username TG / staff login Мессенджера
    display_name     TEXT,
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ornaments        INTEGER NOT NULL DEFAULT 0,   -- шаров на ёлке (бывш. height)
    last_decor       TIMESTAMPTZ,
    streak           INTEGER NOT NULL DEFAULT 0,
    best_streak      INTEGER NOT NULL DEFAULT 0,
    streak_last_day  DATE,
    decor_total      INTEGER NOT NULL DEFAULT 0,
    snowballs_left   INTEGER NOT NULL DEFAULT 0,
    snowballs_day    DATE,
    snowballs_sent   INTEGER NOT NULL DEFAULT 0,
    snowballs_recv   INTEGER NOT NULL DEFAULT 0,
    blocked          BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS achievements (
    tg_id       BIGINT NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
    code        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tg_id, code)
);

-- Полный лог всех действий: для организаторов всё прозрачно (кто, что, когда)
CREATE TABLE IF NOT EXISTS event_log (
    id         BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    tg_id      BIGINT,
    username   TEXT,
    action     TEXT NOT NULL,
    details    TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_ornaments ON users (ornaments DESC);
CREATE INDEX IF NOT EXISTS idx_users_sent ON users (snowballs_sent DESC);
CREATE INDEX IF NOT EXISTS idx_users_recv ON users (snowballs_recv DESC);
CREATE INDEX IF NOT EXISTS idx_users_streak ON users (best_streak DESC);
CREATE INDEX IF NOT EXISTS idx_event_log_ts ON event_log (ts);
