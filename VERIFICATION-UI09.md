# UI09 — подготовленный Vitest и совместимость проекта

Завершено 16 октября 2026. Новое окружение node-vitest-v1, package inspection,
объяснимый отказ до исполнения и сохранение совместимости фактического снимка.
Решение: [ADR-028](docs/decisions/ADR-028-prepared-vitest-and-compatibility.md).

Проверено:

- Offline unit/contract/dashboard/launcher/F01: **831 passed, 3 skipped**,
  `.local/ui09-tests.xml`. Пропуски Windows symlink/POSIX mode. Ruff/format,
  mypy (**112 source-файлов**) и **20 import-контрактов** прошли. Wheel собран
  из sdist offline; новые модули/UI/операторские runner sources включены, .local
  и секреты исключены.
- **65 pure compatibility cases**: четыре секции зависимостей, safe names,
  union cap100, UTF8/JSON/dedup/NaN/overflow, недоверенные sources, workspaces,
  mixed/Jest, scripts без исполнения, version-unverified и неизвестные deps.
- **19 дополнительных boundary cases** от агента: ограниченное корневое чтение,
  invalid input/link target never opened, отсутствие nested reads, gate по snapshot
  bytes вместо изменившегося live файла, недоступные dependencies/uppercase
  environment directives до create, ложный passed с issues запрещён, разные
  Node/Vitest environments не сравниваются при одном image/hash.
- HTTP: обязательная сессия, чтение safe DTO без raw scripts, invalid package
  объясняется enum-причиной, чтение не вызывает child/job, Vitest требует своей
  фиксированной пары consent/environment. Общие hardening flags проверены для
  Python, Node и Vitest профилей.
- Реальный Vitest Docker suite: **8 passed, 16 deselected**,
  `.local/ui09-vitest-docker.xml`. Успешные TS-тесты с extensionless imports,
  ignored vite/vitest config, poisoned tsconfig extends, no npm scripts,
  неизменность host source, отсутствие .env/keys/socket, non-root/RO/ресурсные
  лимиты/network deny. Отдельно assertion/type/only failures, no_tests,
  синхронный hang и bounded output, unsupported dependency до исполнения.
- Прежний Python/Node Docker regression: **16 passed, 8 deselected**,
  `.local/ui09-regression-docker.xml`. Всего **24 разных live cases**.
  Удаление owned контейнеров подтверждено после каждого запуска.
- Два агента реализовали detector/tests и guest wrapper, затем выполнили независимое
  ревью. Реальный прогон выявил внутреннее чтение Vite tsconfig несмотря на
  overrides; заменой настроек только в гостевой копии проблема исправлена.
  Финальное ревью не выявило блокеров. При not_started/pre-create причинах UI
  теперь честно показывает «Контейнер: Не создавался».
- В браузере добавлен «Демо · Vitest с зависимостями» в отдельной
  `.local/dashboard/qa-vitest`. Автовыбор Vitest, version warning, переключение
  на несовместимый Node с disabled запуском даже после checkbox, возврат Vitest
  со сбросом consent проверены. Настоящий UI-запуск прошёл TypeScript/Vitest,
  контейнер удалён. ID `50bdcccd-a0e3-4d12-acbf-b87663bcfff1`, snapshot
  `16a544832c62f8691ab4b9901de69c68a4229b56d98953ae67e191eeebbac70f`.
  Screenshots: `.local/dashboard/screenshots/vitest-incompatible.png`, `vitest-report.png`.
- Старые history/results читаются с default None/empty новых полей; image pins
  валидны. Локальный PostgreSQL был остановлен: существующий loopback контейнер
  запущен для чтения UI-аудита, миграции/данные/политики не менялись. Платные модели
  не вызывались, F06 checkpoint не выпускался, cluster live suite не запускался.

Ограничения: только подготовленные прямые vitest/vite/typescript/@types-node,
без воспроизведения project constraints/lock и произвольного installer. React,
Jest, JSX/TSX, workspaces, custom environments и сборка приложения пока вне scope.
Node environment и fixed config могут отличаться от проекта: это прямо показано.
Тесты и логи недоверенные; нет аттестации или гарантии полезных assertions,
сохраняются Docker/kernel/user/filesystem допущения.
