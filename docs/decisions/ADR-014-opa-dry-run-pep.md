# ADR-014 — OPA PDP и PEP перед описательным dry-run

Принято: 15 октября 2026. F05, п. 4.3 Foundation & Safety. Только L0.

OPA/Rego независимо проверяет четыре действия каталога. Python PolicyChecker
остаётся вторым барьером и эталоном parity-тестов. В явно выбранном режиме
`--authorization opa` PEP допускает dry-run только при согласии обоих.
Ошибка/отказ OPA не переключает режим на Python. `--authorization python`
сохраняет прежний автономный offline-прототип и является явным локальным режимом,
не production-допуском. У демо/старого eval по умолчанию остаётся этот режим.

PEP расположен отдельно от чистой policy/dry_run и HTTP-клиента. В OPA уходит
ограниченная проекция идентификаторов, цели, параметров и классов, без логов,
evidence, промптов или секретов. HTTP — только фиксированный loopback endpoint,
без proxy/redirects, с ограничениями тела и deadline. Управление policy/data API
OPA запрещено отдельной authz-политикой; разрешён только POST фиксированного
decision endpoint. Runtime запускает разработческий скрипт, не приложение.

Rego, API-authz и версия каталога связываются SHA-256 в packaged data.json.
PEP проверяет версии/хеши и привязку ответа к точному input_hash. Это проверка
локального контракта, не подпись, удалённая аттестация или workload identity.
Владелец хоста/процесс, занявший loopback порт, остаётся доверенной границей.

Kill switch — отдельный локальный файл разработчика с поколением, глобальным,
кластерным и action-блокированием. Отсутствие/повреждение файла блокирует PEP.
Состояние читается перед OPA и непосредственно перед dry-run; изменение поколения
во время проверки отменяет разрешение. Это не HA/out-of-band production-канал,
не lease/fencing и не устранение гонки перед будущим реальным исполнением.

AuditRecord получает optional pep_receipt: входную проекцию, безопасный ответ
OPA или фиксированную причину ошибки и снимки управления. Пишется прежним INSERT
в JSONB, без миграции. Deny хранится как blocked без dry_run_result; Python-решение
может быть allowed при отказе независимого PEP. Перед append чисто проверяются
связи, результат PEP и описательный шаблон, без повторного сетевого вызова.
Исторические записи без receipt продолжают читаться как прежний offline-трек.

OPA подтверждает лишь допустимость описания в L0: Kubernetes writes, shell,
secret access, data deletion и изменение policy/audit приложению недоступны.
Подписанные bundles/аудит, SPIFFE/Vault, HA и настоящий исполнитель — позднее.

Контракт и серверные флаги сверены с официальными [REST API](https://www.openpolicyagent.org/docs/rest-api)
и [CLI](https://www.openpolicyagent.org/docs/cli). Runtime закреплён на
[OPA v1.21.2](https://github.com/open-policy-agent/opa/releases/tag/v1.21.2),
SHA-256 Windows/Linux проверяется до запуска в developer script/CI.
