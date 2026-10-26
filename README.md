# 🎄 Новогодний ивент: две версии бота

Одна игровая логика, два фронтенда:

| | **Telegram** | **Яндекс Мессенджер** |
|---|---|---|
| Запуск | `python main.py` / `docker compose up -d tg-bot` | `python -m yim` / `docker compose --profile yim up -d yim-bot` |
| White­list | `whitelist_tg.txt` — Telegram username без `@` | `whitelist_yim.txt` — staff-логины |
| Идентификация | username при `/start` | staff login из update (надёжнее: логин не меняется) |
| Интерфейс | inline-кнопки + команды | только команды (кнопок в Bot API Мессенджера нет) |
| Токен | `BOT_TOKEN` от @BotFather | `MESSENGER_ROBOT_TOKEN` (OAuthTeam робота `robot-*`) |
| Админы | `ADMIN_IDS` (числовые TG ID) | `ADMIN_LOGINS` (staff-логины) |
| Сеть | публичный интернет | доступ к `bp.mssngr.yandex.net` (корпсеть/VPN) |

БД и схема (`db_init.sql`) общие для обеих версий; можно запускать обе одновременно
на одной базе.

## Игровая механика (актуальная концепция)

- **Украшение вместо полива**: раз в 3 часа участник вешает **шар** на ёлку.
  База — 1 шар, бонус за серию (шкала: 1→+0, 2→+1, 3→+2, 5→+3, 7→+5, 14→+7, 21→+10, 28→+12 шаров).
  Ночное украшение (вне 08:00–23:59) вешает шары, но **не растит серию**.
- **Анонимный рейтинг**: участники видят только своё место и отставание от лидера
  («до лидера N шаров»). Полный топ скрыт до финала; полный reveal — админ-командой
  в конце ивента.
- **Ачивка за участие**: 🎄 «Я в деле!» выдаётся при регистрации автоматически.
- **Снежки**: 3 в сутки, уведомление цели.
- **Прозрачность для организатора**: каждое действие пишется в `event_log`
  (кто/что/когда) + `/admin_list` с логинами и полными данными.

## Команды

**Пользователи** (в TG работают и кнопки):
| Команда | Действие |
|---|---|
| `/start` | регистрация / меню |
| `/decor` | повесить шар |
| `/snow <имя>` (в YIM: `<логин>`) | кинуть снежок; без аргумента — список участников |
| `/top` | моё место в номинациях (анонимно) |
| `/me` | моя статистика и ачивки |

**Админы**:
| Команда | Действие |
|---|---|
| `/admin` (`/admin_stats`) | сводка ивента + последние события из лога |
| `/admin_list` | все участники: логины, id, шары, серии, снежки |
| `/admin_rating` | полный топ по 4 номинациям (не анонимный) |
| `/admin_add` / `/admin_remove <username>` | управление whitelist |
| `/admin_register <id> <Имя>` | принудительная регистрация (после закрытия, только TG) |
| `/admin_block` / `/admin_unblock <id>` | блокировка (только TG) |
| `/admin_log N` | последние N записей журнала событий |
| `/admin_final` | полный reveal итогов (в TG) |

## Настройка Яндекс Мессенджера (для YIM-версии)

1. Staff-аккаунт робота с префиксом `robot-*` (заводится отдельно).
2. OAuthTeam-токен под роботом:
   https://oauth.yandex-team.ru/authorize?response_type=token&client_id=f45aca80c81144dd95dc150319c327a3
   → `MESSENGER_ROBOT_TOKEN` в `.env`.
3. Регистрация бота: `POST https://bp.mssngr.yandex.net/team/register` c заголовком
   `Authorization: OAuthTeam <токен>`, затем переключить тип на новый API:
   ```bash
   curl -X POST https://bp.mssngr.yandex.net/team/update \
     -H "Authorization: OAuthTeam $MESSENGER_ROBOT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"type":"botplatform_internal"}'
   ```
4. Проверка: `GET /public/bot/v1/self/get` → `{"ok": true...}`.
5. Участники должны **первыми написать** боту `/start` — отправка первыми
   со стороны бота запрещена (`permission_denied`).
6. Машина с ботом должна видеть `bp.mssngr.yandex.net` (корпсеть/VPN);
   HTTPS-сертификат — Yandex Internal CA (клиент сам подхватывает bundle из
   `/etc/ssl/certs/YandexInternalCA.pem` или `REQUESTS_CA_BUNDLE`).

Документация: https://docs.yandex-team.ru/mssngr-backend/pages/bp/bot-api/methods

## Запуск

```bash
cp .env.example .env         # заполнить токены/даты
docker compose up -d --build tg-bot     # Telegram
docker compose --profile yim up -d --build yim-bot   # Яндекс Мессенджер
```

Без Docker — любая версия запускается локально (`python main.py` или
`python -m yim`) при запущенном PostgreSQL и заполненном `.env`.

## Переменные окружения

| Переменная | Значение по умолчанию |
|---|---|
| `TZ_NAME` | Europe/Moscow |
| `DECOR_COOLDOWN_HOURS` | 3 |
| `SNOWBALLS_PER_DAY` | 3 |
| `DAY_WINDOW_START` / `DAY_WINDOW_END` | 8 / 23 (часы серии) |
| `REGISTRATION_START/END`, `EVENT_START/END` | 27.11–02.12 / 01.12–31.12 |
| `WHITELIST_FILE` / `WHITELIST_FILE_YIM` | whitelist_tg.txt / whitelist_yim.txt |
