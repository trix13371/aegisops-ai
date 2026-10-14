# AegisOps AI — локальный прототип

Реализованы **S0-01: среда**, **S0-02: контракты/синтетика**, **S0-03: kind и RBAC**,
**S0-04: read-only collector**, **S0-05: обнаружение и безопасный контекст**,
**S0-06: LLM-анализ и рекомендации**
из части B документа «Stage −1: ревью и исправленная редакция», редакция 2.1.
Python 3.12, PostgreSQL, Alembic и CLI. Это отдельный трек прототипа,
не Shadow Mode и не завершённый MVP-A.

Готовы read-only collector, проверки среды, детектор, санитизация контекста,
LLM-адаптер и рекомендации с резервными правилами.
Контракты Pydantic, 25 JSON Schema, каталог четырёх
действий и 10 синтетических сценариев готовы. Уровень приложения — L0.
Следующая задача S0-07 — политика, dry-run и запись результата в аудит.

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

Входы каталога/сценариев находятся в actions/ и scenarios/; промпты и ответы
для бесплатных тестов — в prompts/ и fixtures/. Их копии внутри
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
сохраняются командой observe. Логи по умолчанию не запрашиваются.

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

## Обнаружение и контекст (S0-05)

```powershell
uv run aegisops detect
uv run aegisops detect --scenario crashloop-oom --show-context
uv run aegisops detect --scenario inj-secret-canary --show-context
uv run python scripts/cluster-local.py refresh-reader
uv run aegisops detect --source local-kind
```

Для synthetic команда работает без БД, Kubernetes и API-ключа. По умолчанию
проверяет все 10 фикстур: 6 положительных и 4 отрицательных. Для local-kind
использует существующий read-only доступ и собирает ограниченные логи.
Вывод содержит инциденты и хеши; `--show-context` добавляет очищенные evidence.
Параметр `--scenario` относится только к synthetic.

Правило — точный `waiting_reason == CrashLoopBackOff`, включая init-контейнеры.
Прошлые рестарты, OOM без текущего CrashLoop, ImagePullBackOff и
CreateContainerConfigError сами по себе инцидент не создают. Это обнаружение,
а не диагноз. При обработке батча выбирается последнее падающее наблюдение;
здоровая реплика не скрывает падающую. История восстановления здесь не хранится.

ID зависит от origin, cluster, namespace, kind/name владельца, контейнера,
типа и пятиминутного UTC-окна. Замена Pod внутри того же владельца в одном
окне сохраняет ID. `DetectionConfig` позволяет задать окно, минимум рестартов
(по умолчанию 0), лимиты снапшотов и инцидентов. Превышение лимитов — отказ,
а не частичный результат. Владелец ReplicaSet соответствует ADR-006.

`ContextLimits` задаёт объём логов, строк, Events и evidence. Статус сохраняется
первым, затем хвост лога и последние Events. Ссылки evidence остаются связанными
с Incident. SHA-256 вычисляется по каноническому JSON очищенного контекста и
лимитов; это проверка воспроизводимости, не подпись или защита от администратора.

Redaction применяется до обрезки и сериализации: password/API key assignments,
Bearer/Basic, JWT, URI с credentials, connection strings и private-key blocks.
Все свободные строковые поля и attributes проходят очистку; строки событий
и логов сохраняют `untrusted=true`. Инструкции из логов остаются данными.
Распознавание эвристическое: произвольные, закодированные и неизвестные формы
секретов не гарантированно удаляются. Распознаваемый секрет, разрезанный между
evidence, вызывает отказ. Детектор и context builder не имеют доступа к
сервисам или credentials; границу проверяет import-linter.

Короткая приёмка: [VERIFICATION-S0-05.md](VERIFICATION-S0-05.md).

## Диагноз и рекомендации (S0-06)

```powershell
uv run aegisops analyze
uv run aegisops analyze --scenario crashloop-oom
uv run aegisops analyze --scenario inj-secret-canary --llm none
```

По умолчанию `--llm fake`: шесть учебных ответов по хешу промпта, без БД,
сети и API-ключа. Четыре отрицательных сценария не создают результатов анализа.
Фикстуры написаны вручную по expected.yaml и проверяют механику конвейера,
а не качество диагностики модели. После изменения промпта или его входов
пересоздайте их `uv run python scripts/prepare-llm-fixtures.py`, затем
синхронизируйте assets. `--llm none` демонстрирует отказ провайдера: неизвестная
причина и передача человеку, с явным `used_fallback=true`.

Внешний API включается явно:

```powershell
# AEGISOPS_LLM_API_KEY уже передан безопасным способом в окружение процесса.
uv run aegisops analyze --scenario crashloop-oom --llm openrouter
# Для локальной модели обязательны её имя и loopback URL с явным портом.
uv run aegisops analyze --scenario crashloop-oom --llm local --model local-model --local-url http://127.0.0.1:11434/v1/chat/completions
```

В этой рабочей среде можно запустить `../aegisops-agent-kit/Analyze-AegisOps.cmd`:
он получает уже подключённый DPAPI-ключ и передаёт его только дочернему процессу.
Этот персональный launcher находится вне репозитория и не нужен для установки.
Ключ не вставляется в команду или исходники. Внешний endpoint фиксирован:
`https://openrouter.ai/api/v1/chat/completions`, модель по умолчанию —
`deepseek/deepseek-v4.1-flash`; переопределение через `--model`.
Локальный endpoint принимает только буквальные `127.0.0.1`/`::1`.
Его необязательный ключ задаётся отдельно: `AEGISOPS_LOCAL_LLM_API_KEY`.
OpenRouter-ключ к локальному серверу не передаётся.

Промпт analysis-v1 содержит схему двух Draft и проекцию каталога. Имена, логи
и Events находятся в отдельном JSON-блоке недоверенных данных. Один запрос
возвращает диагноз и рекомендацию; JSON проверяется приложением. Вход и вывод
очищаются, ссылки должны вести только к переданным evidence. Неизвестное действие,
лишние параметры, чужой workload и поля безопасности от модели отвергаются.
ID, класс обратимости, обязательное одобрение и `execution_performed=false`
назначает приложение. Restart остаётся только рекомендацией; rollback возможен
лишь при подтверждённом owner kind Deployment, не ReplicaSet.

Пределы `LLMLimits`: вход 32 KiB (system+user UTF-8), выход 1600 токенов,
ответ HTTP 64 KiB, таймаут 30 секунд, максимум две попытки на инцидент.
Общий RunBudget на вызов CLI: максимум 12 запросов и 65536 консервативно
зарезервированных токенов. Резерв равен байтам входа плюс максимум выхода
и 128 на оформление; это ограничитель, не счётчик провайдера или цена.
Резерв не возвращается после ошибки. Фактический usage хранится отдельно;
`usage_complete=false` означает, что расход неудачной попытки неизвестен.
Лимиты можно уменьшать через Python API; отдельных CLI-флагов пока нет.

При недоступном/невалидном ответе или исчерпании бюджета включаются правила:
сохранить валидный диагноз, если он получен; иначе unknown с низкой уверенностью.
Для application_exit_nonzero предлагается сбор previous_logs, для oom_killed —
events, для остальных/неоднозначных причин — передача человеку. Ничего не
собирается и не выполняется автоматически. Некорректный вход приложения
отвергается до обращения к модели.

HTTP не использует tools/functions или redirects. TLS проверяется стандартным
контекстом; для OpenRouter поддерживается настроенный системный HTTP CONNECT
proxy. Локальный endpoint не использует proxy. Размер ответа и время чтения
ограничены; разрешение DNS остаётся под контролем ОС, поэтому строгая общая
граница времени DNS не гарантируется. Сырые ответы и ошибки провайдера
не выводятся. Внешнему API допустимы только synthetic_fixture и local_kind.
Redaction остаётся эвристической, а инструкция промпта сама по себе не является
защитой: полномочия ограничены каталогом и отсутствием исполнителя.

Live-проверки платного API по умолчанию исключены, в CI ключа нет. Для явного
прогона нужны `AEGISOPS_LLM_LIVE=1` и ключ в окружении процесса:
`uv run pytest tests/integration/test_llm_live.py -m llm_live`.
Обычные тесты используют fake и настоящий временный HTTP-сервер на loopback.
Проверка фактического запроса подтверждает отсутствие tools и тестовой канарейки.

`analyze` пока не создаёт AuditRecord и не присваивает статус полного запуска.
Политика, dry-run и сохранение в БД — S0-07; end-to-end `run` и eval — S0-08.
Решение об одном запросе: [ADR-007](docs/decisions/ADR-007-bounded-analysis.md).
Приёмка: [VERIFICATION-S0-06.md](VERIFICATION-S0-06.md).
