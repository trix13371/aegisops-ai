# AegisOps AI — локальный прототип

Реализованы **S0-01: среда**, **S0-02: контракты/синтетика**, **S0-03: kind и RBAC**,
**S0-04: read-only collector**
из части B документа «Stage −1: ревью и исправленная редакция», редакция 2.1.
Python 3.12, PostgreSQL, Alembic и CLI. Это отдельный трек прототипа,
не Shadow Mode и не завершённый MVP-A.

Готовы защитный read-only клиент, collector и проверка среды. Детектор,
рекомендации, executor и LLM-вызовы ещё не реализованы.
Контракты Pydantic, 24 JSON Schema, каталог четырёх
действий и 10 синтетических сценариев готовы. Уровень приложения — L0.
Следующая задача S0-05 — обнаружение, контекст и санитизация.

## Сценарии без БД и LLM

```powershell
uv run aegisops scenario list
uv run aegisops scenario show crashloop-exit-1
uv run python scripts/export-schemas.py --check
uv run python scripts/sync-assets.py --check
```

`scenario show` показывает метаданные и статусы, скрывая сырой текст логов и Events.
Это просмотр фикстуры, не диагноз или исполнение. scenarios/crashloop содержит
ручные фикстуры, scenarios/local-kind — захваты наших workload в локальном kind.
Ожидания в expected.yaml
будут использоваться для проверки детектора и рекомендаций позднее.

Входы каталога/сценариев находятся в actions/ и scenarios/. Их копии внутри
src/aegisops/assets/ включены в wheel. После изменения канонических входов
выполните `uv run python scripts/sync-assets.py`; CI проверяет равенство.
schemas/ и examples/contracts/ содержат контракты и эталонные JSON.
После изменения DTO экспортируйте схемы через scripts/export-schemas.py и
пересмотрите эталоны. Связи ID, UTC и лимиты байтов проверяет runtime:
JSON Schema не заменяет эти проверки, санитизацию и политику.

## Установка с нуля

Нужны Docker с Linux-контейнерами, Git и [uv](https://docs.astral.sh/uv/getting-started/installation/).
Python uv устанавливает или находит по `.python-version`. Для Windows команды
работают в PowerShell. Выполняйте из корня этого проекта:

```powershell
uv sync --frozen
uv run python scripts/init-local-env.py
docker compose up -d --wait
uv run python scripts/with-local-env.py alembic upgrade head
uv run python scripts/with-local-env.py aegisops doctor
```

Первый скрипт создаёт `.env` с независимыми случайными паролями. Он не
перезаписывает существующий файл и не печатает значения. Приложение само не
читает `.env`: разработческий wrapper загружает его буквально, без shell/eval.
Для обычной команды `uv run aegisops doctor` задайте `AEGISOPS_DATABASE_URL`
в окружении процесса самостоятельно; не вставляйте секрет в историю терминала.

`doctor` проверяет подключение, актуальность миграции, роль и права БД.
Он не требует LLM-ключа или Kubernetes credentials. При отсутствии конфигурации,
неверном адресе, недоступной БД или лишних привилегиях возвращает код 1.
Проверка не доказывает безопасность всей платформы.

В текущей среде uv установлен локально, без изменения PATH:

```powershell
$uv = 'C:\Users\takta\Documents\Codex\2026-10-09\new-chat\work\tooling\uv\bin\uv.exe'
& $uv run python scripts/with-local-env.py aegisops doctor
```

## Проверки

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run python scripts/with-local-env.py alembic check
uv run python scripts/with-local-env.py pytest
uv run pip-audit
gitleaks git --redact --no-banner
```

Для unit/contract/safety без PostgreSQL: `uv run pytest -m 'unit or contract or safety'`.
Интеграционные тесты пропускаются без конфигурации БД; полный приёмочный прогон
обязательно выполняется с БД. Тесты проверяют реальные запреты INSERT/SELECT
против UPDATE/DELETE/TRUNCATE/DDL, ограничения таблицы, дрейф схемы, повышение
прав и отказ `doctor`. INSERT/SELECT разрешены, остальные перечисленные операции
запрещены. Временные изменения прав в тестах восстанавливаются в `finally`.
Не запускайте интеграционные тесты параллельно против одной БД.

Pre-commit: `uv run pre-commit install`. uv и gitleaks должны быть в PATH
процесса Git; точные версии инструментов описаны в VERIFICATION.md.
GitHub Actions подготовлен, но удалённый запуск не выполнен. Job использует
отдельную тестовую БД и фиксированные публичные пароли только для одноразового
CI-сервиса. Платных LLM-вызовов в CI нет. Отдельный kind workflow подготовлен,
но удалённо не запускался.

## Локальная БД и границы

Compose-проект `aegisops-prototype-s001` публикует PostgreSQL только на
`127.0.0.1:55432`. Данные хранятся в отдельном named volume. Инициализация
ролей выполняется только при первом старте пустого volume. Если меняете пароли
в `.env`, существующие роли автоматически не обновляются.

- Bootstrap: настройка отдельных ролей, приложение его не использует.
- Owner: миграции, схема и таблица.
- App: SELECT/INSERT в `audit_runs`, SELECT версии миграции; никаких ролей-членств.

Адрес приложения ограничен loopback IP, локальным именем БД и ролью app.
Только loopback не гарантирует отсутствие туннеля к иной БД; пользователь
должен использовать созданную здесь локальную среду.

Остановка без удаления данных: `docker compose stop`. Повторный старт:
`docker compose up -d --wait`. Команда удаления volume намеренно не входит
в обычный путь. `alembic downgrade` удаляет таблицу аудита — это разработческий
инструмент владельца БД, не функция приложения.

Обычная таблица PostgreSQL не защищена от владельца или администратора:
подписанный аудит, WORM и production-защита относятся к последующим этапам.
Секреты, kubeconfig, `.venv` и локальные отчёты исключены из Git.
Политика безопасности: docs/security/threat-model-prototype.md.
Результат S0-02 и учёт расходов: [VERIFICATION-S0-02.md](VERIFICATION-S0-02.md).

## Локальный kind (S0-03)

Нужны Docker Linux engine, kind 0.33.0 и kubectl 1.36.1. Текущая Windows-среда:

```powershell
$env:AEGISOPS_KIND_BIN = 'C:/Users/takta/Documents/Codex/2026-10-09/new-chat/work/tooling/kind/kind.exe'
uv run python scripts/cluster-local.py up
uv run python scripts/kind-scenarios.py apply
uv run python scripts/kind-scenarios.py capture --timeout 300
uv run python scripts/with-local-env.py aegisops doctor --source local-kind
$env:AEGISOPS_KIND_LIVE = '1'
uv run python scripts/with-local-env.py pytest
```

Admin/reader kubeconfig, CA и регистрация находятся в .local/kind/, вне Git.
User KUBECONFIG и ~/.kube/config не используются. Reader запрашивает токен
на 10 минут; после истечения приложение отказывает. Обновление разработчиком:
`uv run python scripts/cluster-local.py refresh-reader`.
Поэтому doctor local-kind может потребовать refresh после длительного перерыва.
Кластер сейчас оставлен запущенным для следующей задачи.

Скрипты принимают только aegisops-demo: context, server/CA, регистрация и
Docker ownership проверяются. Namespace использует restricted Pod Security.
Reader имеет pods/events get/list/watch, pods/log get и namespace marker get
только для aegisops-demo (ADR-005). Kubernetes добавляет discovery и
selfsubjectreview права, не дающие изменения workload или чтения Secrets.

Очистка: `uv run python scripts/cluster-local.py down`. PostgreSQL остаётся.
Cleanup допускается без доступного namespace, но context/CA/Docker ownership
всё равно проверяются. Не отключайте guards для восстановления доступа.
Приёмка и расходы: [VERIFICATION-S0-03.md](VERIFICATION-S0-03.md).

## Наблюдение (S0-04)

```powershell
uv run python scripts/cluster-local.py refresh-reader
uv run aegisops observe
uv run aegisops observe --include-logs
```

Команда не требует БД или ключа LLM. Выводит только количество снапшотов,
контейнеров, Events и доступных log tails. Сырые тексты не выводятся и не
сохраняются: redaction появится в S0-05. Логи по умолчанию не запрашиваются.

`KubernetesReadOnlySource.collect()` реализует `ObservationSource` и возвращает
те же DTO, что `SyntheticSource`. Использует только `ReadOnlyClient` и повторно
проверяет среду в конце сбора. Выбирает поля статуса и metadata по allowlist;
spec, env, annotations и labels не используются. Поддерживает init-контейнеры.
Pod без опубликованных container statuses пока не включается в результат.

Пределы: 200 Pod (2 страницы по 100), 256 прочитанных Events на Pod
(4 страницы по 64); сохраняются последние 64 по времени. Events привязаны
к UID и namespace, поэтому события от старого Pod с тем же именем исключены.
Продолжение списка сверх предела или ошибка доступа прерывают весь сбор.
Snapshot ID зависит от UID, resourceVersion и времени наблюдения; clock и
фабрика ID заменяются в тестах.

Владелец берётся из controller ownerReference: для Deployment Pod это
ReplicaSet (ADR-006). Дополнительных RBAC-прав на чтение ReplicaSets нет.
Опциональный log tail относится к первому контейнеру в порядке init/обычный,
затем имя; это ограничение текущего DTO с одним полем лога. До 100 строк и
16 KiB UTF-8. Отсутствующие логи (400/404) допустимы; 401/403, ошибки сети
или маркера среды приводят к отказу. Клиент закрывает вызывающий код.

Короткая приёмка: [VERIFICATION-S0-04.md](VERIFICATION-S0-04.md).
