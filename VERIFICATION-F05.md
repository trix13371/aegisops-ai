# F05 — OPA PDP и PEP перед dry-run

Проверено локально 15 октября 2026. Только L0, без исполнения действий.

`review/run --authorization opa` требуют согласия независимой Rego policy,
Python PolicyChecker и двух чтений аварийного управления. Отказы/сбои OPA
не переключаются на Python. Прежний default `--authorization python` остаётся
offline-режимом демо/eval без OPA и нового kill switch.

OPA 1.21.2 запущен отдельным скрытым процессом на `127.0.0.1:18181` с проверкой
официального SHA-256. Сервер загружает только decision.rego, api_authz.rego и
data.json. Управляющие API и чтение decision через GET отвергаются HTTP 401;
разрешён POST единственного decision endpoint. Никаких proxy/redirects или
изменяемого приложением URL. Deadline 2 секунды, предел ответа 8192 байта.

Проверено:

- Полный pytest с PostgreSQL, настоящими kind и OPA: **688 passed, 1 skipped,
  2 deselected**. Пропущен недоступный Windows symlink; платные тесты исключены.
  Включены 20 kind-тестов и 17 тестов настоящего OPA.
- 25 Rego-тестов: четыре действия, запреты по namespace, параметрам, цели,
  классу, owner, версии bundle, привязке incident, исполнению и approval;
  проверяется отсутствие каждого обязательного ключа.
- Python safety/transport: строгие bool и response schema, undefined/null,
  дубликаты, лишние поля, привязка хешей, oversize/redirect/status/deadline,
  отсутствие/повреждение control, блокировка global/cluster/action,
  изменение control после PDP, невозможность преодолеть Python deny.
- CLI с настоящим OPA: `allowed → completed` с dry-run, `kill_switch → blocked`
  без dry-run, остановка OPA → `opa_unavailable/blocked`. Все три записи
  повторно прочитаны из PostgreSQL и совпали с результатами CLI.
- `run --source local-kind --authorization opa`: 10 наблюдений, 6 инцидентов,
  6 разрешённых PEP receipt, completed. Использован бесплатный fixture-v2;
  все 6 записей совпали при чтении из БД.
- Ruff/format, mypy (69 файлов), 10 import-контрактов, 40 JSON Schema и эталоны,
  синхронизация package assets, metadata bundle, Alembic check и doctor
  local-kind прошли. Миграция не нужна: receipt — optional поле JSONB.
- Wheel собран из sdist, извлечён отдельно. Импорт именно из wheel,
  проверка bundle, offline-анализ и настоящий OPA decision прошли.
  [Wheel smoke](docs/verification/f05-opa/wheel-smoke.json).
- CI дополнен закреплённым Linux OPA с SHA-256, Rego и live OPA тестами;
  удалённый workflow не запускался.

[Сводка с UUID и readback](docs/verification/f05-opa/summary.json).
DeepSeek подготовил Rego-тесты; проверка каждого scope/ключа исправлена с
existential на universal перед приёмкой. Gemini независимо просмотрел PEP,
transport, audit binding, Rego и launcher; существенных замечаний не нашёл.
Секреты и телеметрия внешним моделям не передавались, платного анализа
инцидентов в приёмке нет.

OPA оставлен работающим, глобальная аварийная блокировка включена.
Старый offline-режим и исторические записи без receipt продолжают работать.
Receipt — локальная запись решения, не криптографическая аттестация.
Оператор/ОС/loopback-порт доверены. Нет workload identity, подписанных bundles,
HA, lease/fencing или гарантий устранения гонок перед будущим исполнителем.
Это не завершение MVP-A и не production-допуск.
Решение: [ADR-014](docs/decisions/ADR-014-opa-dry-run-pep.md).
