# Проверка S0-01

14 октября 2026. Локальная приёмка выполнена на Windows, Python 3.12.14,
uv 0.13.1, PostgreSQL 17 в Linux-контейнере Docker Desktop.

## Результат

- 41 тест прошёл, включая настоящий PostgreSQL, без оплачиваемых LLM-запросов.
- Ruff lint/format, mypy strict, import-linter — прошли.
- `alembic upgrade head` применён ролью owner; `alembic check` не нашёл дрейфа.
- `doctor` прошёл под ролью app без LLM-ключа и Kubernetes credentials.
- Отдельная чистая virtualenv установлена через `uv sync --frozen`; doctor прошёл.
- gitleaks 8.30.1 не обнаружил секретов в подготовленных Git-файлах.
- pip-audit проверил установленное окружение: известных уязвимостей не обнаружено.

Тесты подтвердили SELECT/INSERT и запрет UPDATE/DELETE/TRUNCATE, создания
постоянной/временной таблицы, ALTER TABLE и SET ROLE owner. Неверные status,
origin, autonomy_level и schema_version отклоняются ограничениями БД.
Временная выдача лишних прав, роли-членства или CREATEDB приводит к отказу doctor;
тесты восстанавливают права. URL и синтетические canary не выходят в сообщения
и форматированные traceback. Проверяется dispose при ошибке драйвера.

GitHub Actions настроен, **удалённый job не запускался**. Интеграция kind
не относится к S0-01 и пока отсутствует. JSON-контракты, санитизация, рекомендация,
dry-run и полный AuditRecord будут реализованы следующими задачами.
Порог 90% критичных policy/dry_run/sanitizer относится к их появлению;
этих модулей в текущем срезе нет. Не заявляется production-готовность.

## Источники технических решений

- [uv — установка](https://docs.astral.sh/uv/getting-started/installation/).
- [SQLAlchemy — декларативные таблицы](https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html).
- [Alembic — миграции](https://alembic.sqlalchemy.org/en/latest/tutorial.html).
- [PostgreSQL — GRANT](https://www.postgresql.org/docs/current/sql-grant.html).
- [OpenRouter — reasoning и общий лимит токенов](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).

Windows-архив gitleaks 8.30.1 скачан из официального release и проверен:
SHA256 d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e.
CI actions закреплены по разрешённым из официальных GitHub tags commit SHA.
