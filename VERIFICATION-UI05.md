# UI05 — изолированные проверки Python

Завершено 16 октября 2026. На карточке проекта добавлены «Тесты Python», отдельное
согласие на исполнение, история и результаты Ruff/pytest с локальным выводом.
Проект исполняется только как ограниченный снимок в disposable Docker container.

Проверено:

- Расширенный offline набор unit/contract/dashboard/launcher/F01: **687 passed,
  3 skipped**, `.local/ui05-tests.xml`. Windows symlink/POSIX-mode пропуски.
  После дополнительных проверок ownership/окружения/host-timeout и усиления DTO:
  финальная целевая выборка **69 passed**, `.local/ui05-targeted.xml`.
- Реальные Docker tests: **4 passed** (`.local/ui05-docker.xml`): безопасный
  disposable проект, ошибки Ruff и pytest с именами файлов/строками, отсутствие
  тестов, зависший тест с остановкой через 45s и удалением контейнера.
- Дополнительно на обновлённом entry: **2 passed, 4 deselected**
  (`.local/ui05-docker-extra.xml`): missing dependency даёт явный ModuleNotFoundError,
  большой вывод останавливается с error/truncated, без passed.
- Финальная проверка профиля после усиления cleanup: **1 passed, 5 deselected**
  (`.local/ui05-final-profile.xml`). Удаление проверяется `docker ps --all`, а не
  только отсутствием running контейнера. Подтверждены UID 65532, capabilities=0,
  no-new-privileges, Seccomp=2, cgroup memory=512 MiB/pids=64, отказ сети, отсутствие
  Docker socket/.env/ключа окружения, read-only image. Тест меняет только копию
  main.py; исходный файл хоста побайтно сохранён.
- Snapshot canary/secrets/dependencies исключаются до открытия; содержимое
  Python не исполняется на хосте. Oversize/depth/нет Python дают отказ, а не
  усечённый успешный запуск. Архив детерминирован и содержит только regular files.
- Persistent claim/history/readback, отсутствие повторного worker-start,
  invalid/corrupt result, no_tests и неполный passed проверены. Cleanup unit tests
  отвергают чужой run label, профиль и имя до rm. CLI окружение фильтрует cloud/
  provider/DB/Docker overrides; вывод ограничен. Host deadline вызывает cleanup
  и сохранение timeout, без повторного исполнения.
- HTTP: сессия, точные Origin/Host, consent, single-job lock, UUID и bounded payload.
  Commands/images/mount/network overrides отвергаются. История/результат требуют
  сессию. Все HTTP тесты используют отдельные временные реестры.
- Реальный UI: добавлен «Демо · тесты Python», checkbox до запуска не выбран,
  кнопка выключена. Запущены Ruff и 1 pytest-тест, оба passed; snapshot 2 файла,
  container_removed=true. Run ID `b99e3572-af86-4aaa-b0f1-0f4dec51feeb`, snapshot
  SHA256 `4014be803b57889f6c3d34a3ffcb57247267ba99ab0e6a86f179f39a455e3e4f`.
- После stop/start отчёт и вывод открыты из истории без повторного запуска.
  Ошибок JavaScript нет. Скриншот `.local/dashboard/screenshots/python-run-report.jpg`
  хранится только локально; демо оставлено в интерфейсе. Старые проекты/отчёты/
  нейросетевые ответы сохранены. Собственные runner-контейнеры после тестов не остались.
- Ruff/format, mypy (**106 source-файлов**) и **20 import-контрактов** прошли.
  Образ собран из digest base и pinned/require-hashes dependencies, context ограничен
  тремя файлами. Wheel включает pure runner modules/UI, sdist — Docker context;
  .local и ключи в пакеты не попали. Прикладные зависимости, миграции и incident
  schemas не менялись. F06 checkpoint не выпускался: runner runs вне audit_runs.

Это не запуск всего произвольного окружения проекта: Python 3.12 + Ruff/pytest,
без автоматической установки dependencies/плагинов и без project config hooks
инструментов. conftest.py исполняется в guest. Исходная папка не подключается;
результат относится к поддержанным файлам снимка. Вывод тестов недоверенный,
Docker не является гарантированной изоляцией для hostile code/host kernel escape.
При crash возможны pending/orphan, для точечного recovery есть --cleanup UUID;
автоматического resume/prune нет. Логи остаются локальными и не идут модели.
Решение: [ADR-023](docs/decisions/ADR-023-isolated-python-checks.md).
