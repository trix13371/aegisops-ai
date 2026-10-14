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
JSON Schema и эталоны проверяются контрактными тестами (25 после S0-06).
Валидация DTO не означает, что строка санитизирована: это отдельный механизм.

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
CLI observe показывает только счётчики; сырые снапшоты остаются в памяти.

S0-05: DetectionEngine → очищенный Incident → ContextBuilder → DiagnosisContext.
Срабатывание задаёт текущий waiting reason, а не число прошлых рестартов.
Дедупликация включает origin и владельца, окно задано DetectionConfig.
Evidence формируются по allowlist, ограничиваются ContextLimits и очищаются
до сериализации. Контекст сохраняет ID evidence и хешируется канонически.
CLI detect работает на synthetic/local-kind и может показать очищенный контекст.

S0-06: AnalysisEngine → LLMClient → единый AnalysisDraft (диагноз + рекомендация).
Один запрос вместо двух независимых экономит контекст; максимум одна повторная
попытка. Раздельная привязка Draft сохраняет проверки diagnosis/evidence,
каталога, параметров и целевого workload (ADR-007). Поля безопасности назначает
приложение; недоступность, невалидный ответ или лимит вызывают rule fallback.
Промпт и контекст хешируются; метаданные готовы к аудиту следующего шага.
LLMLimits/RunBudget ограничивают каждый запрос и всю серию. FakeLLMClient
читает только подготовленные ответы по хешу. OpenAICompatibleClient поддерживает
фиксированный OpenRouter HTTPS и явно выбранный loopback endpoint; ключи раздельны.
Engine/prompt/fallback не могут импортировать транспорт или инфраструктуру.
CLI analyze работает без БД.

S0-07: ReviewService для одного Incident → AnalysisEngine → PolicyChecker →
DryRunEngine → PostgresAuditStore.append. CLI review выбирает один synthetic
сценарий. PolicyChecker разрешает только описание четырёх точных действий;
все реальные исполнительные capabilities заблокированы. Решение содержит
хеш incident/recommendation/catalog/policy; dry-run пересчитывает решение.
Summary строится шаблоном приложения, эффекты не проверяются. AuditStore
санитизирует и сверяет контекст, evidence, policy и dry-run; транзакционный
INSERT под app-ролью, без update/delete API. completed возвращается только
после записи. Ошибка БД прерывает CLI. Полный сбор источника, ранний отказ
среды, run и eval остаются S0-08 (ADR-008).

Планируемый конвейер S0-02…S0-08: ObservationSource → DetectionEngine →
ContextBuilder/sanitizer → DiagnosisEngine → RecommendationEngine →
PolicyChecker → DryRunEngine → AuditStore. Контракты — Pydantic/JSON Schema.
FakeLLMClient обеспечивает бесплатные воспроизводимые тесты.

Сначала synthetic_fixture; local_kind добавляется с read-only SA и guards.
LLM предлагает структурированное действие из каталога. Поля безопасности
и итог dry-run формирует приложение. Исполнителя в прототипе нет.
