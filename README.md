# AegisOps AI — локальный прототип

Реализованы **S0-01: среда**, **S0-02: контракты/синтетика**, **S0-03: kind и RBAC**,
**S0-04: read-only collector**, **S0-05: обнаружение и безопасный контекст**,
**S0-06: LLM-анализ и рекомендации**, **S0-07: политика, dry-run и аудит**,
**S0-08: полный запуск, eval и демо**
из части B документа «Stage −1: ревью и исправленная редакция», редакция 2.1.
Python 3.12, PostgreSQL, Alembic и CLI. Это отдельный трек прототипа,
не Shadow Mode и не завершённый MVP-A.

Готовы read-only collector, проверки среды, детектор, санитизация контекста,
LLM-адаптер, рекомендации с резервными правилами, полный CLI и аудит в БД.
Контракты Pydantic, 36 JSON Schema, каталог четырёх
действий и 10 синтетических сценариев готовы. Уровень приложения — L0.
Synthetic и live-kind конвейеры проверены целиком. Проблема публикации
Docker-порта устранена: API закреплён на `127.0.0.1:16443`. Полный прогон
с kind после F04: 624 passed, 1 skipped, 2 deselected. Живой анализ проверен на
бесплатном fake/fallback; платные smoke-тесты остаются отдельными.
Подробности: [восстановление kind](VERIFICATION-KIND-RECOVERY.md).

После прототипа добавлен **F01: классы данных и маршрутизация LLM** — первый
локальный срез Foundation & Safety. OpenRouter допускает только учебный public;
живой local-kind имеет минимум internal и внешней модели не передаётся.
Restricted обрабатывается резервными правилами без модели. Решение пишется
в аудит. Это не завершённый Этап 0a и не разрешение Shadow на реальных данных.
Текущая приёмка: [VERIFICATION-F01.md](VERIFICATION-F01.md).

**F02: оценки рекомендаций человеком** добавляет CLI feedback, неизменяемую
историю исправлений и сводку текущих мнений отдельно по происхождению данных.
Миграция `0002_feedback` применена к текущей локальной БД; при обновлении другой
установки выполните `uv run python scripts/with-local-env.py alembic upgrade head`.
Приёмка: [VERIFICATION-F02.md](VERIFICATION-F02.md).

**F03: сравнение с разметкой** добавляет причину/исход со ссылкой на разбор
и `feedback quality`. Отчёт учитывает повторы инцидентов, противоречия и
неполные группы выборки. Миграция `0003_feedback_labels` применена локально;
для другой установки выполните `alembic upgrade head` через wrapper выше.
Приёмка: [VERIFICATION-F03.md](VERIFICATION-F03.md).

**F04: версионируемые runbook** добавляет три короткие инструкции диагностики,
их детерминированный выбор для промпта analysis-v2 и ссылки ID/версия/hash
в аудит. Это справочные данные, не наблюдения или разрешения выполнять действия.
Приёмка: [VERIFICATION-F04.md](VERIFICATION-F04.md).

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

Pytest использует `--tb=short`: полный traceback может показать значения
фикстур, включая URL БД. При восстановлении локальный app-пароль заменён
после такого диагностического вывода; `.env` уже обновлён.

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
- App: SELECT/INSERT в `audit_runs` и `recommendation_feedback`, SELECT версии
  миграции; никаких ролей-членств. Обе таблицы проверяет doctor.

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

API использует фиксированный host-порт 16443. Перед созданием скрипт проверяет
bind к loopback; занятый или зарезервированный порт останавливает создание.
Существующий кластер использует endpoint собственной регистрации и CA pin.

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

Промпт analysis-v2 содержит схему двух Draft и проекцию каталога. Имена, логи,
Events и выбранные runbook находятся в отдельном JSON-блоке недоверенных данных. Один запрос
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
не выводятся. После F01 внешнему API допустим только public из учебной синтетики;
local-kind имеет минимум internal и через OpenRouter не передаётся.
Redaction остаётся эвристической, а инструкция промпта сама по себе не является
защитой: полномочия ограничены каталогом и отсутствием исполнителя.

Live-проверки платного API по умолчанию исключены, в CI ключа нет. Для явного
прогона нужны `AEGISOPS_LLM_LIVE=1` и ключ в окружении процесса:
`uv run pytest tests/integration/test_llm_live.py -m llm_live`.
Обычные тесты используют fake и настоящий временный HTTP-сервер на loopback.
Проверка фактического запроса подтверждает отсутствие tools и тестовой канарейки.

`analyze` не создаёт AuditRecord и не присваивает статус полного запуска.
Для анализа с политикой, dry-run и записью в БД используйте `review` (S0-07);
end-to-end `run` и eval описаны ниже в S0-08.
Решение об одном запросе: [ADR-007](docs/decisions/ADR-007-bounded-analysis.md).
Приёмка: [VERIFICATION-S0-06.md](VERIFICATION-S0-06.md).

## Классы данных и маршрутизация (F01)

`analyze`, `review` и `run` принимают `--data-class`:

| Класс | OpenRouter | Явно выбранная локальная модель | Offline fake |
| --- | --- | --- | --- |
| public | Разрешён | Разрешена | Разрешён |
| internal | Запрещён | Разрешена | Разрешён |
| confidential | Запрещён | Разрешена | Разрешён |
| restricted | Запрещён | Запрещена | Запрещён |

Без флага synthetic получает public, local-kind — internal. Повысить класс
можно, понизить ниже минимума источника нельзя. Строки логов и ответ модели
не управляют классификацией. Неизвестный провайдер запрещён. Автоматической
смены провайдера нет; без разрешённого ответа работают резервные правила.

```powershell
uv run aegisops analyze --scenario crashloop-oom --data-class restricted
uv run python scripts/with-local-env.py aegisops run --scenario crashloop-oom --data-class restricted
# Для internal/confidential нужно отдельно установить и явно выбрать локальную модель.
uv run aegisops analyze --source local-kind --llm local --model local-model --data-class confidential
```

Запрет маршрута не расходует RunBudget и не вызывает клиент/HTTP. Результат
имеет `used_fallback=true`, `fallback_reason=routing_denied`, а parameters
содержит `data_class`, `routing_policy`, `route_allowed`, `route_reason`.
`run`/`review` сохраняют их в PostgreSQL; `analyze` выводит без сохранения.
Completed означает завершённый разбор и аудит, включая резервный сценарий,
а не успешное обращение к модели. Прямой HTTP/fake-вызов также проверяет маршрут.

Это политика передачи модели, не DLP или политика хранения. Restricted
продолжает обрабатываться детерминированным кодом и локальным аудитом.
Нельзя выдавать реальные данные за synthetic; происхождение определяется
доверенным источником. Loopback не доказывает отсутствие туннеля, а redaction
не гарантирует удаления неизвестных секретов/PII. Для реальных данных нужны
отдельные решения G(−1). Решение: [ADR-010](docs/decisions/ADR-010-data-routing.md).

## Политика, dry-run и аудит (S0-07)

```powershell
uv run python scripts/with-local-env.py aegisops review
uv run python scripts/with-local-env.py aegisops review --scenario inj-secret-canary
uv run python scripts/with-local-env.py aegisops review --llm none
```

`review` проверяет один положительный synthetic-сценарий (по умолчанию OOM).
БД должна быть запущена, миграция применена. Fake остаётся стандартным режимом;
параметры `--llm`, `--model`, `--local-url` работают как у analyze.
Отрицательный сценарий без инцидента отвергается этой командой до анализа
и записи; для полной серии и отсутствия инцидентов используйте `run`. Live-kind источник
в этой команде не подключён. Идентификатор запуска UUID и UTC-время задаёт
приложение; в Python API часы и генератор UUID заменяемы для тестов.

PolicyChecker версии prototype-1 по умолчанию отказывает. Разрешение возможно
только для описания одного из четырёх действий каталога: точные параметры,
согласованные ID, класс обратимости и target; rollback требует однозначного
Deployment в status evidence. Произвольные инструкции из rationale/logs
не влияют на решение. R2 restart разрешён для описания при L0. Все шесть
исполнительных capabilities остаются заблокированы, включая Kubernetes write,
shell, чтение секретов, удаление данных и изменение политики/аудита. Список
относится к предлагаемому действию, не к явно включённому анализу через API.

Решение содержит SHA-256 `input_hash` от Incident, полной Recommendation,
каталога и версии политики. Это привязка к данным, не подпись или средство
аутентификации. Поле необязательно только для чтения старых DTO: текущий
checker всегда его заполняет, а dry-run отвергает старое/подменённое решение.
DryRunEngine заново вычисляет политику и требует полного равенства решения.
Summary берётся из шаблона приложения, не из свободного текста модели.
Никаких запросов к Kubernetes, сбора evidence или исполнения здесь нет.
Эффект, предусловия, rate limit и пригодность отката не проверяются.

ReviewService связывает анализ, policy и dry-run для уже обнаруженного
инцидента. `completed` означает разрешённое описание с сохранённым аудитом;
`blocked` — отказ политики без dry-run; `failed` — техническая ошибка с
фиксированным очищенным сообщением. Допустимый fallback может завершиться
как completed; его `used_fallback` и причина сохраняются в llm.parameters.
Сведения о среде до обнаружения и полный source pipeline доступны через `run`.
AuditStore уже принимает ранний blocked без incident, если указана ошибка среды.

PostgresAuditStore заново валидирует и очищает AuditRecord до сериализации
в JSONB, сверяет контекст/хеши/evidence, решение политики и шаблон dry-run.
Изменившийся при очистке контекст нельзя записать со старым хешем. Размер
одной записи ограничен 512 KiB UTF-8. INSERT и служебные индексируемые поля
пишутся одной транзакцией; UUID не перезаписывается и повторная запись даёт
ошибку. Методов update/delete у store нет. Конструктор проверяет локальный
URL, app-роль, права, миграцию и таблицу. SQL-параметры и ответы провайдера
не логируются. После завершения транзакции CLI печатает сохранённую запись;
при ошибке durable audit выходит с кодом 1 и не выдаёт completed JSON.
Для сохранённых blocked/failed CLI тоже возвращает код 1.

Схема БД не менялась: существующие JSONB и колонки S0-01 достаточны.
Append-only обеспечивается правами app-роли. Владелец БД и администратор
могут менять записи; подпись, WORM и hash-chain здесь не реализованы.
Подробности: [ADR-008](docs/decisions/ADR-008-policy-and-audit.md).
Приёмка: [VERIFICATION-S0-07.md](VERIFICATION-S0-07.md).

## Полный запуск и демо (S0-08)

При готовой локальной БД можно дважды щёлкнуть `Demo-AegisOps.cmd` в корне
проекта. Он запускает бесплатный eval, сохраняет 10 результатов в аудит и
показывает краткие счётчики. Скрипт не создаёт/перезапускает Docker или kind.

```powershell
uv run python scripts/with-local-env.py aegisops run --source synthetic
uv run python scripts/with-local-env.py aegisops run --source synthetic --scenario neg-healthy
uv run python scripts/with-local-env.py aegisops eval
uv run python scripts/with-local-env.py aegisops audit list --limit 10
# Подставьте UUID, напечатанный run, eval или audit list.
uv run python scripts/with-local-env.py aegisops audit show <run_id>
```

`run` собирает все 10 synthetic-сценариев или один выбранный, проверяет
ограниченный батч и origin, обнаруживает инциденты и передаёт каждый в review.
Одна AnalysisEngine и один RunBudget используются на весь батч. Стандартная
модель — fake, без API-ключа или платных запросов. Для явного внешнего/локального
анализа доступны те же `--llm/--model/--local-url`, что у analyze.

При отсутствии инцидентов сохраняется один completed AuditRecord с
`observation={source, snapshot_count, detected_incidents: 0}`. Диагноз, LLM,
политика и dry-run в такой записи отсутствуют; модель не вызывается. Пустой
источник тоже допустим. Статусы БД остаются completed/blocked/failed. Ранний
отказ среды сохраняется как blocked без incident с фиксированной причиной;
неверная форма наблюдений/ошибка детектора — failed. Некорректная CLI-конфигурация
или недоступная БД завершаются до выдачи результата.

PipelineResult содержит счётчики и сохранённые AuditRecord. Итог failed имеет
приоритет над blocked, затем completed. Каждая запись коммитится отдельно:
если следующая запись не удалась, прежние остаются, но успешный итог всего батча
не печатается. Повтор запуска создаёт новые UUID аудита при прежних ID инцидента
и хешах контекста. Это не автоматическое исполнение и не транзакция всего батча.

`audit list` читает только метаданные, по умолчанию 25, максимум 100 строк.
`audit show` принимает UUID, ограничивает размер JSONB и проверяет/очищает
запись, её контекст и ID. Историческое решение не переоценивается текущей
политикой при чтении; append по-прежнему проверяет текущую политику и шаблон.
Некорректные старые низкоуровневые тестовые JSONB могут быть перечислены по
метаданным, но show их отвергнет. Команд изменения аудита нет.

Local-kind подключается через тот же reader, guards и read-only collector:

```powershell
uv run python scripts/cluster-local.py refresh-reader
uv run python scripts/with-local-env.py aegisops run --source local-kind
```

Reader закрывается до анализа, `--scenario` с local-kind запрещён. При
недоступной среде результат blocked сохраняется без обращения к модели.
Fake не содержит ответов для изменяющихся живых контекстов: применяется
обозначенный rule fallback. Успех такого анализа не доказывает качество диагноза.
Восстановленный Windows-kind прошёл положительный прогон с PostgreSQL:
20 живых тестов. Сценарии наблюдаются ограниченным опросом; их CrashLoop-фазы
не обязаны совпадать во времени. Kind workflow дополнен PostgreSQL и E2E;
удалённо не запускался.

`eval` всегда использует fixture-v2 и ровно 10 ожидаемых сценариев, сохраняет
10 pipeline-запусков через тот же AuditStore. Платную модель выбрать нельзя.
Он измеряет precision/recall детектора, совпадение категорий, допустимость
действий, canary leaks и executions. Доли выводятся парой numerator/denominator.
Сохранённый [eval-report.json](docs/verification/s0-08/eval-report.json):
6 TP, 4 TN, 0 FP/FN; причины и действия 6/6; утечки и исполнения 0.

Показатели injection разделены намеренно: `injection_fixture_policy_denials=0/1`
на безопасном fake-ответе к логу-инъекции; это не провал механизма и не проверка
устойчивости настоящей модели. `adversarial_policy_probe_denials=6/6` — отдельные
ручные подмены ID/класса/target/параметров/owner и неочищенного текста, проверенные
политикой без LLM. Они не являются новыми сценариями или платными вызовами.
Исчезновение случая, неверный диагноз/действие, незавершённый запуск, утечка,
исполнение или пропущенная подмена делают eval неуспешным.

Фикстуры и ответы написаны одной командой, поэтому эти числа подтверждают
механику прототипа. Они не измеряют качество модели, production-безопасность,
Shadow Mode или выполнение порогов MVP. Решения и границы:
[ADR-009](docs/decisions/ADR-009-complete-pipeline-and-evaluation.md).
Итог: [VERIFICATION-S0-08.md](VERIFICATION-S0-08.md).

## Справочные инструкции диагностики (F04)

Каталог `runbooks/catalog.yaml` содержит CrashLoop triage, проверку OOMKilled
и ненулевого завершения процесса. Прочитать его можно без БД и ключа:

```powershell
uv run aegisops runbook list
uv run aegisops runbook show oom-triage
uv run aegisops analyze --scenario crashloop-oom
```

По структурированному текущему CrashLoop-статусу выбираются общий документ
и максимум один специальный. OOM требует `last_terminated_reason=OOMKilled`;
один код 137 выбирает только инструкцию проверки ненулевого завершения.
Имена workload, тексты логов/Events и ожидаемые ответы сценария в выборе
не участвуют. Runbook описывает способ проверки, а не доказывает причину.

Документы ограничены 1024 байтами канонического JSON каждый, выбранный пакет —
2048 байтами и двумя документами. F04 поддерживает только явно `public`
учебные инструкции: `internal` и другие классы runbook отвергаются. Метка —
утверждение автора; автоматического классификатора нет. Каталог из Git
проверяется по строгой схеме, не принимает дубликаты, неизвестные поля,
YAML aliases/duplicate keys, symlink или credential-shaped содержимое.

Содержимое помещается в поле `runbooks` внутри недоверенного JSON, отдельно
от `diagnosis_context`. Для supporting_evidence_ids по-прежнему допустимы
только реальные evidence. Каталог действий, политика и ограничения F01
сохраняют полномочия приложения; улучшение устойчивости самой модели к
инъекциям этим не доказано.

Аудит хранит `runbook_catalog_version`, `runbook_selection_hash` и `runbook_refs`
в формате ID@version. В `runbook list/show` виден отдельный hash документа;
selection hash связывает его с остальной выборкой. Полный prompt hash также
меняется при изменении текста документа, даже если автор забыл поднять версию.
Выбранные документы не обязательно отправлены модели: сверяйте calls_this_incident.

Новый prompt — `analysis-v2`, бесплатные ответы — `fixture-v2`. Исторические
v1 assets и аудит сохранены; старые отчёты eval также читаются. После правки
runbook синхронизируйте assets, затем пересоздайте offline-ответы и повторно
синхронизируйте: `scripts/sync-assets.py`, `scripts/prepare-llm-fixtures.py`,
`scripts/sync-assets.py`. Общие лимиты LLM/RunBudget не увеличиваются.
Решение: [ADR-013](docs/decisions/ADR-013-versioned-runbooks.md).

## Оценки рекомендаций (F02)

`feedback add` сохраняет одну из оценок: `useful` (полезно), `incorrect`
(ошибочно), `unsafe` (опасно), `uncertain` (не хватает данных). Оценить можно
сохранённый запуск с рекомендацией. Привязка включает UUID запуска, ID и хеш
рекомендации; исходный аудит не меняется. Для здорового наблюдения без
рекомендации отзыв недоступен.

Пример из PowerShell, из корня проекта:

```powershell
$run = uv run python scripts/with-local-env.py aegisops run --scenario crashloop-oom | ConvertFrom-Json
$runId = $run.records[0].run_id
$feedback = uv run python scripts/with-local-env.py aegisops feedback add $runId --reviewer local-demo --assessment uncertain --comment 'Учебная проверка интерфейса' | ConvertFrom-Json
uv run python scripts/with-local-env.py aegisops feedback list --run-id $runId --limit 10
uv run python scripts/with-local-env.py aegisops feedback summary --source synthetic --limit 25
# Явное исправление последней оценки того же пользователя:
$feedbackId = $feedback.feedback_id
uv run python scripts/with-local-env.py aegisops feedback add $runId --reviewer local-demo --assessment useful --supersedes $feedbackId
```

Псевдоним reviewer — 1…64 латинских букв, цифр, точек, дефисов или подчёркиваний,
первый символ — буква/цифра. Это локальная подпись пользователя, не проверенная
identity. Комментарий — до 2000 символов, очищается до хранения и вывода.
Не вводите секреты в комментарий: shell history находится за пределами redaction.

Первый отзыв для run+reviewer создаётся один раз. Исправление требует
`--supersedes` с ID текущего отзыва. Старый ID, чужой reviewer/run, повтор UUID
или две конкурирующие правки приводят к отказу без изменения истории.
`list` показывает историю, включая предыдущие оценки. Вывод add появляется
только после COMMIT. Нет команд удаления или перезаписи.

`summary` считает только текущие оценки, отдельно для synthetic и local-kind,
в пределах последних 1…100 мнений. `sample_size` — объём этой выборки,
`has_more=true` означает, что более старые текущие оценки не вошли.
Несколько reviewer на один run дают несколько мнений; это не независимые
инциденты, не accuracy и не допуск G0. Локальная разработческая БД содержит
отзывы автоматических тестов, поэтому её цифры не являются оценками SRE.
Мнения не передаются LLM и не одобряют исполнение или повышение автономности.
Решение: [ADR-011](docs/decisions/ADR-011-human-feedback.md).

## Сравнение с причиной и исходом (F03)

При оценке можно добавить `--confirmed-cause` из категорий `application_exit_nonzero`,
`missing_runtime_config`, `invalid_command`, `oom_killed` и `--outcome` из
`resolved`, `unresolved`, `unknown`. Для причины/исхода обязательна
`--reference` — ID постмортема или тикета до 64 символов по тем же правилам,
что reviewer. Это локальное утверждение со ссылкой: источник не открывается
и его авторство не проверяется. Старые отзывы без этих полей остаются валидными.

```powershell
# $runId — сохранённый запуск с рекомендацией; TEST-PM-42 — учебный ID.
uv run python scripts/with-local-env.py aegisops feedback add $runId --reviewer local-reviewer --assessment useful --confirmed-cause oom_killed --outcome resolved --reference TEST-PM-42
uv run python scripts/with-local-env.py aegisops feedback quality --source synthetic --limit 25
uv run python scripts/with-local-env.py aegisops feedback quality --source local-kind --limit 25
```

Если у того же reviewer уже есть отзыв на run, добавьте `--supersedes` с его
текущим ID. Исправление заменяет весь отзыв: чтобы сохранить разметку, нужно
повторить её поля. Пропуск полей снимает разметку, сохраняя прежнюю версию в истории.

Отчёт группирует текущие отзывы по `incident_id` и выбирает самый поздний
оценённый запуск. Один ID даёт один case независимо от числа запусков/reviewer.
ID детектора включает пятиминутное окно; это ещё не независимые постмортемы.
Разные причины или известные исходы дают `conflict`; отсутствие причины —
`unlabelled`. Одна конкретная категория сравнивается как `match`/`mismatch`,
неизвестный или неоднозначный диагноз — `abstained`.

Лимит относится к отзывам, а не инцидентам. Если часть текущих отзывов того же
инцидента не вошла, case получает `sample_incomplete` и не сравнивается.
`has_more` указывает усечение выборки. Чтение происходит в одном read-only
REPEATABLE READ snapshot, поэтому параллельное исправление не смешивает версии.
Отчёт содержит только ID, категории и метаданные модели, без комментариев.

`prediction_kind` различает `fixture`, `fallback`, `model` и `unclassified`;
provider/model_name показывают источник выбранного диагноза. Общие счётчики
относятся к показанным случаям и не являются accuracy модели. `resolved`
обозначает записанный исход, а не эффект рекомендации. Локальные тестовые
labels не заменяют реальные разборы; механизм не вычисляет gate G0 или MTTR.
Решение: [ADR-012](docs/decisions/ADR-012-labelled-comparison.md).
