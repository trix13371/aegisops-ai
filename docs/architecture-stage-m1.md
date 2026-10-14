# Текущий срез архитектуры

Основание: часть B Stage −1, редакция 2.1, решения Python/kind от 10 октября.
Связанный общий план до MVP определяет дальнейшие Foundation, Shadow и L2;
локальный прототип не заменяет эти этапы.

S0-01: CLI → проверка локального URL → read-only doctor → PostgreSQL.
Миграции и создание среды выполняются отдельно от приложения.

S0-02: domain/common+models задают ограниченные frozen DTO; bind_diagnosis
связывает вывод с известным инцидентом и evidence, bind_recommendation
проверяет каталог и ставит ID, класс обратимости и поля безопасности.
Standalone Diagnosis проверяет структуру: на границе недоверенного вывода
обязательно использовать bind_diagnosis/validate_diagnosis с инцидентом.
AuditRecord дополнительно сверяет связи вложенных сущностей.

actions/catalog.py проверяет четыре определения и параметры. sources/synthetic.py
читает фиксированные файлы, не обращаясь к кластеру. CLI scenario list/show
не требует БД или API-ключа. source наблюдения не выполняет детекцию.
JSON Schema и 24 эталона проверяются контрактными тестами. Валидация DTO
не означает, что строка санитизирована: этот механизм появляется в S0-05.

S0-03: env_guard принимает только project reader config и проверяет loopback,
CA pin, регистрацию, контекст, SA subject и TTL. safe_client вручную создаёт
SDK Configuration, разрешает только GET по списку путей и точному origin,
отключает redirects и проверяет expiry/namespace marker при каждом чтении.
Kubernetes SDK импортируется только этим модулем.

Developer scripts создают/удаляют собственный kind, настраивают RBAC и capture.
Они не являются интерфейсом исполнения приложения.

S0-04: KubernetesReadOnlySource → safe_client → allowlist-нормализация →
PodSnapshot. ObservationSource объединяет synthetic и local_kind. Контролируемая
пагинация, UID-фильтр Events, строгие статусы, UTF-8 лимиты, инъекция clock/ID.
Истинный ownerReference сохраняется, включая ReplicaSet (ADR-006).
CLI observe показывает только счётчики. Текст остаётся в памяти до S0-05.

Планируемый конвейер S0-02…S0-08: ObservationSource → DetectionEngine →
ContextBuilder/sanitizer → DiagnosisEngine → RecommendationEngine →
PolicyChecker → DryRunEngine → AuditStore. Контракты — Pydantic/JSON Schema.
FakeLLMClient обеспечивает бесплатные воспроизводимые тесты.

Сначала synthetic_fixture; local_kind добавляется с read-only SA и guards.
LLM предлагает структурированное действие из каталога. Поля безопасности
и итог dry-run формирует приложение. Исполнителя в прототипе нет.
