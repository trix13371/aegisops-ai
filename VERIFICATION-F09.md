# F09 — отдельные процессы collector и Control Plane

Проверено локально 15 октября 2026. L0, файловый handoff на одном хосте.

Сборщик читает только зарегистрированный локальный kind, закрывает reader,
очищает наблюдения и подписывает пакет. Другой процесс проверяет пакет,
выполняет detection/offline fallback, обязательный OPA/PEP и сохраняет аудит.
Внешней передачи internal-наблюдений и исполнителя нет.

Проверено:

- Полный pytest с настоящими PostgreSQL, kind и mTLS OPA: **922 passed,
  2 skipped, 2 deselected**. Пропуски — Windows symlink-привилегия и POSIX mode;
  платные LLM smoke-тесты исключены. XML последнего локального прогона находится
  в игнорируемом `.local/f09-tests.xml`, сводка — в
  [checks.json](docs/verification/f09-split/checks.json).
- Подпись Ed25519, другая identity/key/регистрация, подмена содержимого, точные
  границы expiry и future timestamp, неверный origin/cluster/data_class,
  дубликаты snapshots/JSON keys, байтовый лимит, неправильный UUID файла,
  подписанные, но не очищенные логи. Публичные эталоны имеют согласованную подпись.
- Reader закрывается до подписи; известный canary очищается до записи spool.
  Отсутствие enrollment или неверная регистрация не дают открыть reader.
- После restart/expiry готовый пакет возвращает ту же квитанцию без нового
  анализа/INSERT. Другой body с тем же UUID отвергается. При конкурентной подаче
  только один потребитель проходит exclusive claim; второй не вызывает модель.
- Первая/частичная ошибка INSERT, ошибка или обрыв записи receipt, потеря строки
  аудита и несовпадение readback не превращаются в успешный приём. Claim остаётся;
  повторный анализ автоматически не запускается.
- Свежие Python-процессы подтверждают границы импортов и очистку environment.
  Collector не загружает SQLAlchemy/LLM, Control Plane — SDK/private signer.
  Fake env canaries не выводятся. App DB URL остаётся только у Control Plane.
- Две новые интеграционные проверки с настоящими PostgreSQL/OPA: allow и
  kill switch с записью provenance; чтение прежней квитанции после expiry без
  анализа/записи. Для этих тестов control/release reader задаётся тестовой
  фикстурой; mTLS transport и PDP настоящие.
- Отдельно реальные CLI-процессы с текущими операторскими файлами: 10 наблюдений,
  5 инцидентов, completed; второй consume возвращает те же run IDs и добавляет
  **0 строк**. Новая серия под kill switch — blocked, 5 записей. Прежний истёкший
  пакет также прочитан как история. Исполнений и внешних вызовов модели — 0.
  [Ручная проверка](docs/verification/f09-split/manual-checks.json).
- `Demo-Split-AegisOps.cmd`/PowerShell-скрипт проверен при включённом kill switch:
  отдельные процессы, 10 наблюдений, 6 инцидентов, blocked и сохранённая квитанция.
  Скрипт не создаёт identity и не снимает блокировку.
- Ruff/format (213 Python-файлов), mypy (90 source-файлов), **15 import-контрактов**,
  **57 schemas/эталонов**, package assets, Alembic check и doctor прошли.
  Новой миграции нет; raw исторические строки аудита не переписывались.
- **31 Rego-тест** прошёл. pip-audit: известных уязвимостей зависимостей нет;
  локальный неопубликованный aegisops-ai отдельно пропущен сканером.
- Wheel построен из sdist и извлечён вне репозитория. Новые точки входа и роли
  импортируются из wheel с текущими локальными зависимостями; секретные файлы
  не включены. Это не установка в чистую ОС.
  [Wheel smoke](docs/verification/f09-split/wheel-smoke.json).
- Новый ручной signed checkpoint: **1121 строка**, verified,
  missing/modified/unanchored — 0. Состояние после проверки: global_blocked=true,
  control generation 13. [Audit verify](docs/verification/f09-split/audit-verification.json).

Gemini подготовил узкие DTO-тесты, которые исправлены и проверены локально,
и независимо просмотрел публичный код. Три замечания разобраны по фактическому
контракту: отказ второго активного потребителя предусмотрен; произвольных строк
пути от пакета нет, враждебный OS-пользователь исключён из модели защиты;
RunStatus — StrEnum. Сам review тесты не выполнял.
[Разбор](docs/verification/f09-split/independent-review.json).

Ограничения: один trusted host/user, native PEM под ACL/0600, доверенные часы и
spool. Это не изоляция прав ОС, сетевой Cluster Agent/gRPC, HA, durable workflow,
exactly-once исполнение или защита от потери питания. Автоматического reconcile
после неопределённого исхода нет; claim нельзя удалять для принудительного retry.
Internal-файлы не следует публиковать даже после redaction. Receipt/claim сами
не входят в подписанный снимок; F06 закрепляет связанную provenance внутри
audit_runs. Старый монолитный CLI остаётся доступен. Этап 1 целиком не завершён.
Решение: [ADR-018](docs/decisions/ADR-018-local-signed-collector-handoff.md).
