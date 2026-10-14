# AegisOps AI — локальный прототип

Реализованы **S0-01: среда**, **S0-02: контракты/синтетика**, **S0-03: kind и RBAC**
из части B документа «Stage −1: ревью и исправленная редакция», редакция 2.1.
Python 3.12, PostgreSQL, Alembic и CLI. Это отдельный трек прототипа,
не Shadow Mode и не завершённый MVP-A.

Готовы защитный read-only клиент и проверка среды. Collector, детектор,
рекомендации, executor и LLM-вызовы ещё не реализованы.
Контракты Pydantic, 24 JSON Schema, каталог четырёх
действий и 10 синтетических сценариев готовы. Уровень приложения — L0.
Следующая задача S0-04 — read-only collector и нормализация.

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
