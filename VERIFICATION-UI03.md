# UI03 — правила контейнеров и рекомендации

Завершено 16 октября 2026. Новые локальные сканирования сохраняют rules_version
local-static-v2. Расширены выбранные проверки Kubernetes/Compose; отчёт показывает
важность, относительный файл, путь настройки и фиксированную рекомендацию.
Старые отчёты читаются с правилами v1, без пересчёта или перезаписи истории.

Проверено:

- Unit + contract + dashboard HTTP safety: **572 passed, 3 skipped**; результаты
  `.local/ui03-tests.xml`. Пропуски относятся к Windows symlink/POSIX mode.
- После добавления регрессии общего бюджета объектов и API-полей: финальная
  выборка project/dashboard **71 passed, 1 skipped**; `.local/ui03-final-targeted.xml`.
  Общий набор live PostgreSQL/kind/OPA в этой задаче не запускался.
- Проверены containers/initContainers/ephemeralContainers, CronJob внутри List
  во втором YAML-документе, hostIPC, hostPath, привилегии, capabilities, CPU+memory
  limits, явный тег/digest с портом registry. Безопасные примеры не дают замечаний.
- Compose: host network/pid/ipc, SYS_ADMIN, прямые socket-монтирования short/long
  bind и Windows npipe; read-only не подавляет предупреждение. Именованный том
  с target docker.sock не считается прямым bind доступа к Docker daemon.
- Неверные типы, ConfigMap/неизвестный kind не вызывают падения/Pod warnings.
  Более 100 элементов, глубокий List, общий бюджет объектов и 200 findings
  дают partial вместо успешного результата. Canary-значения/имена не копируются.
- Прежний отчёт без rules_version/location/recommendation читается как v1.
  HTTP возвращает v2 и рекомендации; существующие проверки отсутствия исполнения,
  исключений секретных файлов и контроля Host/Origin/session прошли.
- В браузере зарегистрирована отдельная учебная папка с одним compose.yaml.
  Проверка нашла **2 замечания**: SYS_ADMIN и Docker socket read-only; путь и
  «Что сделать» видны. Проверен прежний отчёт AegisOps AI: v1, 457 файлов,
  0 замечаний. Escape закрывает drawer; ошибок JS нет. Демо оставлено в интерфейсе.
- Ruff/format, mypy (**97 source-файлов**) и **17 import-контрактов** прошли.
  Wheel собран из sdist; новый модуль и UI assets входят в пакет, приватные
  .local/ключи не входят. Зависимости, DB migrations и incident schemas не менялись.

Браузерный отчёт: `4fd07713-c0c4-45fb-99bc-e8f7cfb01e92`, screenshot
`.local/dashboard/screenshots/container-report.jpg` (локально, вне Git).
Проектный код/тесты/сервисы учебной папки не запускались, модели не вызывались.
Project reports не входят в F06 audit; записи audit_runs этой проверкой не создаются.

Ограничения: выбранные эвристики, без schema validation, разрешения Compose
переменных/наследования/внешних файлов, полного Pod Security/SAST и анализа
runtime-поведения. Предупреждения требуют оценки назначения приложения.
Исправлений исходников и автозапуска команд нет; ограничения UI02 сохраняются.
Решение: [ADR-021](docs/decisions/ADR-021-local-static-rules-v2.md).
