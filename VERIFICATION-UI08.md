# UI08 — изолированные проверки JavaScript/TypeScript

Завершено 16 октября 2026. Добавлены «Тесты JS/TS», фиксированный профиль
javascript-checks-v1 / node-js-v1, TypeScript и Node test. Прежние Python-запуски,
окружения и локальная история сохраняются. [ADR-027](docs/decisions/ADR-027-isolated-javascript-checks.md).

Проверено:

- Полный выбранный offline unit/contract/dashboard/launcher/F01 прогон:
  **743 passed, 3 skipped**, `.local/ui08-tests.xml`. Пропуски Windows
  symlink/POSIX mode. Ruff/format, mypy (**109 source-файлов**) и
  **20 import-контрактов** прошли. Wheel собран из sdist offline и проверен.
- Настоящий Docker JS/TS suite: **8 passed, 8 deselected**,
  `.local/ui08-node-docker.xml`. Python regression suite:
  **8 passed, 8 deselected**, `.local/ui08-python-docker.xml`.
  Всего 16 различных живых cases, разделённых по профилю.
- JS/TS: синтаксическая ошибка, TS type mismatch, assertion failure, отсутствующий
  внешний пакет, no_tests, остановка зависания, bounded output. Успешный TS-тест
  проверяет non-root/RO/caps/NoNewPrivs/seccomp/resource limits, запрет сети,
  отсутствие ключей/.env/Docker socket и неизменность исходника. package scripts
  с exit123 и tsconfig с несуществующим extends не мешают фиксированному профилю.
  Удаление контейнеров подтверждено после каждого случая. Образ по digest,
  npm lock/integrity и --ignore-scripts подготовлен отдельно от проектов.
- DTO/repository/HTTP: профиль соответствует окружению и consent; неверные
  настройки отклоняются. Пара инструментов и snapshot language связаны с профилем;
  старые Python JSON без новых полей читаются без перезаписи. Смена Python→JS
  не сравнивается как исправление даже при одинаковом image ID. JS image pin
  инвалидируется при изменении общего Python guest capture/unpack.
- Два агента выполнили независимое ревью и расширение тестов. Добавлены 7 worker
  boundary cases: чужие/переставленные/неполные checks отвергаются с cleanup;
  неподходящий/неполный снимок останавливает запуск до docker create/start;
  сохранена совместимость старой истории и разделение окружений.
- Ревью нашло P2: uppercase расширения входили в snapshot, но пропускались guest.
  Исправлено: неканонический регистр JS/TS отклоняет весь snapshot до чтения файла.
  Четыре mixed lower/uppercase regression cases прошли; агент подтвердил закрытие.
- В браузере добавлен «Демо · JavaScript и TypeScript» в отдельной папке
  `.local/dashboard/qa-javascript`. Проверены новая кнопка, consent, профиль,
  запуск и сохранённый отчёт: TypeScript/Node test passed, container removed.
  UUID `cafc9f24-385b-4b4b-9180-da152991bead`, snapshot
  `4b782ba8df0a4af813828fb9c7a66d4e994fd2e390f159a786a606541dae807a`.
  После остановки браузерной сессии чтение служебной error page заблокировано
  browser URL policy; финальный screenshot и повторная UI-проверка сравнения
  не выполнены. Локальный сервис перезапущен, authenticated HTTP API подтвердил
  сохранённый результат и выдачу новых assets. Сравнение покрыто unit-тестами.
- Все три image pins валидны; собственных runner-контейнеров после проверок нет.
  Платные модели, PostgreSQL/kind/OPA live suite не запускались, F06 не менялся.

Ограничения: JS checkJs=false (синтаксис), TS fixed strict/noEmit/NodeNext,
node:test по ограниченному filename pattern. JSX/TSX, enum/transforms,
Jest/Vitest/React/Vite, произвольные зависимости и сборка проекта не поддержаны.
package.json влияет на обычную ESM/CJS/import семантику, но не выбирает команды.
Успех относится к выбранному снимку и не доказывает качество assertions.
Нет аттестации и независимого доказательства результатов кода в контейнере;
прежние доверительные допущения Docker/kernel/user/filesystem сохраняются.
