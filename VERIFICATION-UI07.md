# UI07 — подготовленные Python-зависимости

Завершено 16 октября 2026. В «Тесты Python» добавлен выбор minimal/python-data-v1,
диагностика отсутствующих импортов и привязка окружения к сохранённому запуску.
Решение: [ADR-026](docs/decisions/ADR-026-prepared-python-dependencies.md).

Проверено:

- Offline unit/contract/dashboard/launcher/F01: **724 passed, 3 skipped**,
  `.local/ui07-tests.xml`. Пропуски Windows symlink/POSIX mode.
  Целевая выборка runner/comparison/HTTP: **100 passed**,
  `.local/ui07-targeted.xml` (повторена после проверки сохранения результата).
- Ruff/format, mypy (**109 source-файлов**), **20 import-контрактов**.
  Wheel собран из sdist offline; новый diagnostics и UI включены.
- Оба доверенных образа собраны и закреплены локально. Pydantic 2.14.1,
  pydantic-core 2.50.1, PyYAML 6.0.3, annotated-types 0.8.0,
  typing-extensions 4.16.0, typing-inspection 0.4.4: версии и wheel hashes из uv.lock.
  pip --require-hashes --only-binary=:all:, ограниченный build context.
- Настоящий Docker suite: **8 passed**, `.local/ui07-docker.xml`.
  Сохранены проверки non-root/caps/seccomp/read-only, network deny, отсутствия
  секретов/socket/host mounts, неизменности источника, таймаута, переполнения вывода
  и удаления контейнера. Добавлены переход missing yaml → passed на одном снимке
  при выборе другого окружения и запуск существующего test_local_projects.py
  с выбранными исходными модулями AegisOps, без корневого conftest/service fixtures.
  Это не полный suite AegisOps внутри runner.
- API отклоняет произвольный environment, traversal и список вместо строки;
  прежний запрос без environment сохраняет minimal. Claim/result mismatch
  отклоняется до записи и при чтении. Изменение requirements инвалидирует pin;
  pin другого окружения не принимается. Смена environment исключает сравнение,
  даже при одинаковом image ID. Missing-module подсказки bounded/deduplicated,
  URL/shell fragments не принимаются как имена импортов.
- Браузер: отдельный «Демо · зависимости Python» в .local/dashboard/qa-dependencies.
  Базовый запуск `355b30e7-197e-431b-8ef2-8981ccff9875` показывает
  «Недоступны модули: yaml». Расширенный `5a97e6ac-e58e-4d91-919f-955be37c163b`
  проходит Ruff/pytest. Одинаковый snapshot SHA256
  `83f25a7470b78f6e6f888a12e636e74ade1d2d090338bdbd470b88baf7e97b43`;
  исходник между запусками не менялся. Оба контейнера удалены.
  Сравнение показывает «Выбраны разные окружения», не объявляя код исправленным.
  Screenshots: `.local/dashboard/screenshots/missing-dependency.jpg`, `dependency-run.jpg`.
- История старых запусков читается с default minimal. Новые поля не затрагивают
  incident schemas/DB migrations/F06/OPA/kill switch. Платные модели не вызывались.

Поддерживаются только два операторских preset, без автоматической установки
requirements.txt проекта, editable install, произвольного resolver или plugins.
При тестах сеть отключена; при подготовке собственного образа используется сеть.
Диагноз основан на недоверенном выводе: import name не равен distribution name,
а отсутствующий модуль может быть частью проекта. Контейнер/тесты не аттестованы.
