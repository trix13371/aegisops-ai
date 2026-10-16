# UI10 — сводный отчёт и отдельный AI-разбор

Завершено 16 октября 2026. Выбор сохранённых статических результатов и завершённых
запусков, общая локальная история, явный source-free AI preview и отдельное согласие.
Решение: [ADR-029](docs/decisions/ADR-029-selected-project-review.md).

Проверено:

- Offline unit/contract/safety suite: **1213 passed, 3 skipped**,
  `.local/ui10-tests.xml`. Live LLM/kind/OPA/runner исключены. Пропуски — прежние
  платформенные symlink/POSIX mode случаи. Ruff check и format всего репозитория,
  mypy (**120 source-файлов**), **21 import-контракт**, assets и 57 схем прошли.
- **61 core case**: точные связи UUID/project/profile/environment/time,
  полные canonical DTO hashes, строгие поля и типы, независимые статусы,
  отсутствие составляющих, partial/pending/unconfirmed/truncated. Соответствие
  исходников всегда unverified; обобщённая работоспособность не утверждается.
- **45 repository/privacy cases**: одинаковые входы сохраняют ID/время после
  перезапуска; восстановление индекса после прерванной записи; возвращение старой
  сводки после 20 ссылок без новой AI-попытки. Cross-project, pending, изменённые
  полные источники, неизвестные/дублирующиеся поля, повреждённая история и orphan
  AI result/started блокируются. Canary для четырёх окружений подтверждает отсутствие
  исходников, имён, логов, package names, UUID и хешей в отправляемой проекции.
- **33 новых transport cases**, вместе со старым static AI — **53 passed**:
  точный wire payload, проверка вложенных DTO перед отправкой, actions только из
  фактических categories, unknown/duplicate advice, response limits, invalid schema,
  tool/function/refusal/truncated/HTTP/usage failures. Публичный static explain и
  v1 DTO сохранены; фиксированный HTTPS/model/bounds/no-retry общий приватный.
- **9 worker и 4 PowerShell launcher cases**: O_EXCL started, владение финализацией,
  перепроверка полных refs/hash/payload перед и после запроса, безопасные ошибки,
  ранний UUID, фиксированный worker, ограничение остатка и восстановление env.
  PowerShell использовал изолированные fake key endpoint/worker: реальные
  credentials и провайдер не читались и не вызывались.
- Dashboard suite: **87 passed**. Session/Origin/schema/consent, GET/create без
  child или модели, дедупликация, cross-project/pending отказ, closed preview,
  общий job lock, durable claim и completion/failure/readback. Новые тестовые
  репозитории полностью изолированы tmp_path.
- Wheel собран из sdist offline: `.local/ui10-dist`. Новые модули, UI и операторский
  worker включены; `.local`, DPAPI и runtime env исключены. Static assets и JSON
  schemas сверены. Независимое ревью двух агентов не выявило блокирующих замечаний.
- В реальном браузере создана статическая проверка «Демо · Vitest с зависимостями»
  и сводка с ранее сохранённым успешным TypeScript/Vitest. Проверены выбор источников,
  история, статусы, предупреждения, closed AI preview, unchecked/checked согласие
  и доступность кнопки. Согласие снято; платная кнопка не нажималась. Повторное
  сохранение возвращает прежние ID и время, без новой сводки или AI claim.
  Review: `e93a36d6-c5b7-5812-901e-020c4d490c39`;
  run: `50bdcccd-a0e3-4d12-acbf-b87663bcfff1`.
  Screenshot: `.local/dashboard/screenshots/combined-review.png`.

Платных запросов не было. Runner не менялся, Docker live suite не повторялся;
использован сохранённый результат UI09. Кластер, kill switch, политики, аудит БД
и F06 checkpoint не изменялись. Локальный host перезапущен для загрузки новых маршрутов.

Ограничения: сохранённые проверки могут относиться к разным версиям файлов;
статический профиль не хранит source hash. Хеши DTO не являются подписью или
защитой от оператора, переписывающего всё хранилище. Тестовый вывод недоверенный,
полнота и качество assertions не аттестованы. AI объясняет агрегаты, не читает код,
не меняет статусы и не исполняет рекомендации. История локальная и неподписанная,
multi-tenant/remote execution/публичный хостинг остаются вне этого шага.
