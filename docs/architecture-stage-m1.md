# Текущий срез архитектуры

Основание: часть B Stage −1, редакция 2.1, решения Python/kind от 10 октября.
Связанный общий план до MVP определяет дальнейшие Foundation, Shadow и L2;
локальный прототип не заменяет эти этапы.

S0-01: CLI → проверка локального URL → read-only doctor → PostgreSQL.
Миграции и создание среды выполняются отдельно от приложения.

Планируемый конвейер S0-02…S0-08: ObservationSource → DetectionEngine →
ContextBuilder/sanitizer → DiagnosisEngine → RecommendationEngine →
PolicyChecker → DryRunEngine → AuditStore. Контракты — Pydantic/JSON Schema.
FakeLLMClient обеспечивает бесплатные воспроизводимые тесты.

Сначала synthetic_fixture; local_kind добавляется с read-only SA и guards.
LLM предлагает структурированное действие из каталога. Поля безопасности
и итог dry-run формирует приложение. Исполнителя в прототипе нет.
