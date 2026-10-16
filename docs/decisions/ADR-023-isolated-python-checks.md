# ADR-023: изолированный запуск проверок Python

16 октября 2026. После UI04 пользователь разрешил перейти от статических правил
к запуску Ruff и существующих pytest-тестов с изоляцией, лимитами и сохранением
результата. Исполнитель ограничен проверкой копии проекта; кластерные действия,
политики/kill switch, F01 и модели не расширяются.

`aegisops.project_runner` содержит только frozen DTO, bounded snapshot и локальный
репозиторий. 20-й import-контракт запрещает сеть/SDK/config/ключи/команды;
projects также не может импортировать runner. Код исполнения находится только
в operator scripts и доверенном container entry. Модель не имеет этого tool.

HTTP сохраняет сессию/точные Host+Origin/ограниченный JSON. Единственный новый
job: mode=project_runner, project_id, consent=python-checks-v1. Другие параметры,
команды, образы, volumes, endpoints, network и resource overrides отвергаются.
Общий job-lock исключает параллельные UI-запуски и удаление проекта во время
проверки. API истории/результата требует текущую регистрацию и сессию.

До child создаётся UUID claim и ссылка в истории последних 20 запусков.
Worker O_EXCL started запрещает повтор того же принятого запуска. Snapshot
проверяет root device/inode и ссылки, ограничивает entries/depth/bytes/time,
отбрасывает типичные secret filenames, VCS/dependencies и неизвестные расширения.
Файл сверяется по inode/size/mtime до/после чтения; ошибка/лимит запрещают весь
запуск. Детерминированный tar содержит только regular files с нормализованными
метаданными, SHA256 сохраняется в отчёте. Архив не сохраняется постоянно.
Guest повторно отвергает ссылки, traversal, duplicates, большие файлы/архивы.
Нет host mount: данные идут в stdin, распаковываются только в bounded tmpfs.

Docker CLI разрешён только operator script. Endpoint закреплён на локальный
Docker Desktop Linux named pipe (Windows) или local Unix socket; DOCKER_HOST/
CONTEXT и credentials окружения не наследуются. Управление remote daemon не
предусмотрено. Container image используется по image ID с pull=never и pin
контекста. Подготовка отдельно строит только наш runner context из трёх файлов,
с digest базового Python и require-hashes для закреплённых PyPI-зависимостей;
Dockerfile/package hooks проверяемого проекта не читаются как команды.

Обязательные параметры create и inspect: сеть none, read-only root, непривилегированный
UID/GID, drop ALL, no-new-privileges, CPU/memory/pids, no swap/ports/host PID/IPC/
devices/binds/volumes, restart=no. Work/tmp — ограниченные noexec/nosuid/nodev
tmpfs, /dev/shm 8 MiB, nofile/fsize ulimits. Docker log driver none исключает
накопление неограниченного docker log; stdout/stderr CLI ограничены host reader.
По умолчанию работает Docker seccomp; реальный тест проверяет Seccomp=2.
Семантика флагов: [Docker run](https://docs.docker.com/reference/cli/docker/container/run/).

Ruff использует фиксированный набор ошибок и isolated config; pytest получает
доверенный пустой config, отключённые cache/plugin autoload, import paths только
внутри guest. conftest/tests могут выполнять произвольный код **в контейнере**.
Фиксированные зависимости профиля не включают прикладные зависимости проекта:
ModuleNotFoundError/collection error отражаются как ошибка, без pip/network retry.
Семантика конфигурации: [pytest](https://docs.pytest.org/en/stable/reference/customize.html).

Guest supervises отдельные process groups, ограничивает Ruff 15s, pytest 45s
и вывод 24 KB на инструмент; timeout/overflow останавливают группы. Host ограничивает
attach 75s/вывод, CLI 15s; dashboard child — 220s с запасом на snapshot и очистку.
После выполнения/ошибки/таймаута host удаляет только полный container ID с точным
run label, profile label и ожидаемым именем. Результат сохраняется после очистки;
passed/issues требуют подтверждённой очистки, image ID, snapshot и оба инструмента.
Отсутствие тестов, зависимостей, ограничение вывода, OOM/invalid response не дают
успешного результата. Статусы/причины/таймстемпы и связь claim проверяются DTO.

Crash может оставить pending и контейнер, в том числе созданный, но не запущенный.
Нет автоматического resume или общего prune. Активный контейнер с runner label
блокирует новый запуск. Явный operator --cleanup UUID проверяет локальный claim и
точное владение перед удалением; неизвестный результат помечается error/uncertain,
не выполняется заново. История append-only по файлам, ссылки до 20, старые файлы
не удаляются; индекс атомарно replace после exclusive/fsync записи.

Условия: trusted OS user, Docker daemon, checkout и filesystem. Это не VM/microVM
или защита от kernel/container escape, злонамеренного владельца хоста, всех
TOCTOU/потери питания. Тесты могут подделать вывод/результат внутри контейнера:
inspect подтверждает параметры запуска, не аттестует честность тестов. Нет quality
gate/production допуска. Снимок — ограниченный набор поддержанных файлов; исключения
не являются DLP. Логи могут содержать исходники/значения и остаются локальными;
в UI только textContent, без HTML/shell, без передачи в OpenRouter. Старые L0
incident DTO по-прежнему execution_performed=false; новый RunResult явно имеет
execution_scope=container. Project runs вне F06 и не имеют криптографической подписи.
