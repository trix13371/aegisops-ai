# S0-07 — краткая приёмка

15 октября 2026. Политика, описательный dry-run и запись аудита реализованы.

- `review --scenario crashloop-oom` бесплатно проходит analysis → policy →
  dry-run → PostgreSQL. CLI возвращает AuditRecord только после commit INSERT.
  Никакое действие Kubernetes, сбор evidence или исполнение не производится.
- Default-deny, четыре ветви каталога, точные параметры/ID/target/обратимость,
  однозначный Deployment для rollback. Все шесть исполнительных capabilities
  заблокированы. R2 допускается только для описания при L0.
- `input_hash` связывает решение с incident/recommendation/catalog/policy.
  Dry-run пересчитывает решение целиком; подмена предложения, версии, ID,
  blocked capabilities или reasons отвергается. Summary — шаблон приложения.
- AuditStore очищает record, сверяет контекст, evidence, хеши, политику и
  dry-run. Лимит 512 KiB; один INSERT одной транзакцией. Дубликат UUID даёт
  безопасную ошибку, исходная запись сохраняется. UPDATE/DELETE API отсутствует.
- Прогон с локальной PostgreSQL: **377 passed, 20 skipped, 2 deselected**.
  Добавлено 45 тестов. Пропуски прежние: 19 live-kind, 1 Windows symlink;
  2 платных API-теста S0-06 исключены по умолчанию, новых платных smoke-вызовов нет.
  Сохранённый JSONB и вывод CLI не содержат тестовой canary. Ошибки поздних
  policy/dry-run стадий сохраняют failed с валидными evidence, без raw exception.
  Искусственный отказ INSERT не даёт completed JSON; реальные app-права БД
  по-прежнему запрещают UPDATE/DELETE/TRUNCATE/DDL и повышение привилегий.
- Ruff/format, mypy, 4 контракта import-linter, 25 схем и assets прошли.
  Миграция без дрейфа, doctor зелёный; схема PostgreSQL не менялась.
- Pre-commit и сканирование staged-файлов gitleaks прошли без находок.
- DeepSeek подготовил policy/dry-run, Gemini — независимые тесты и ревью.
  Исправлены импорты, несовпадение целевого workload в тестах и контроль
  неизменности предложения через input_hash. Обновлены golden JSON и схемы.
  Предложения ревью очищать все частичные данные при ошибке и запрещать ранний
  environment block без incident не приняты: валидные evidence сохраняются,
  а ранний block по B.8 обязан поддерживаться с явной причиной. Оба случая
  проверены тестами; испорченные структурированные данные по-прежнему отвергаются.

Неподписанный хеш не обеспечивает подлинность. Redaction эвристическая;
владелец БД может менять записи. Hash-chain/WORM не реализованы. Kind API-порт
остаётся недоступным; S0-07 проверен на synthetic и реальной локальной БД.
Полный source pipeline, `run` и eval — S0-08. Уровень остаётся L0.
