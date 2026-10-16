# ADR-028: подготовленный Vitest и определение совместимости

Принято 16 октября 2026. Пользователь разрешил продолжить поддержку JS/TS
с зависимостями и Vitest, разделив исполнение, тестирование и ревью между агентами.

Новый javascript-vitest-v1 / node-vitest-v1 использует прежние snapshot/claim/image
pin/strict result/cleanup. Закрытая пара инструментов — typescript/vitest.
Никаких user npm commands/install, Dockerfile или project config в хост-процессе.
Модель не получает новых tools, исходников, logs или package metadata.

Чистый inspect_package читает только строку package.json: до 256 KiB, strict JSON
без дубликатов/NaN/overflow, четыре dependency-секции, до 100 безопасных имён.
Scripts не анализируются и не исполняются. DTO содержит framework, предложенный
environment, supported/limited/unsupported, закрытые reason codes и имена пакетов.
Прямые Vitest dependencies ограничены vitest/vite/typescript/@types/node;
Node — typescript/@types/node. Workspaces/Jest/mixed framework, external/local/git/
alias refs и дополнительные пакеты блокируют соответствующий запуск.

Разрешённый набор не означает совместимости версий: Vitest всегда limited с
dependency_versions_unverified. Пользователь явно видит pinned preset, без
воспроизведения constraints/lock проекта. Произвольный resolver пока вне scope.

Read-only UI endpoint читает только корневой package.json зарегистрированной папки,
проверяет root identity, plain regular path и ограничение размера. Подсказка advisory;
worker использует package bytes из полного bounded snapshot. Несовместимость
сохраняется как not_supported/project_incompatible до docker create/start.
Environment directives в исходниках консервативно блокируются без учёта регистра.
RunResult сохраняет совместимость снимка и закрытые issues; такие issues нельзя
объявить passed/исполненным результатом. Прежние отчёты имеют default None/empty.

Образ основан на прежнем pinned Node24.22 linux/amd64 digest. Операторский lock
фиксирует Vitest4.0.18/Vite7.3.1/TypeScript5.9.3/@types-node24.10.1 и транзитивные
пакеты (95 entries) с npm registry integrity. npm ci --ignore-scripts выполняется
при подготовке, без исходников проверяемой папки/секретов. Platform optional binary
packages esbuild/Rollup сохраняются. В guest создаётся symlink node_modules на
immutable /opt/aegisops/node_modules; это не host mount и не установка зависимостей.

Compiler API использует ESNext/Bundler/strict/noEmit, JS checkJs=false.
Программный startVitest с config:false/configFile:false исключает автозагрузку
vitest/vite config. Cache/tmp, envDir:false, publicDir:false, inline PostCSS и
esbuild settings исключают другие заявленные настройки. Внутренний Vite7 transform
всё ещё обнаруживает tsconfig независимо: реальные тесты это выявили. Поэтому
только после unpack гостевые tsconfig*/jsconfig*.json заменяются fixed settings,
включая nested, root tsconfig создаётся всегда. Host source не меняется.

Vitest Node environment/globalsfalse, один fork/maxWorkers1/fileParallelismfalse,
изолированные тесты, allowOnlyfalse, без setup/globalSetup/coverage/watch/retry и
обновления snapshots. Package installer заменён отказом. Python guest синтезирует
no_tests/5 только при отсутствии подходящих filenames до запуска Vitest; exit1
не маскируется. Compiler20s/Vitest40s, process-group kill, stdout24KB/tool,
memory512MiB/pids64/CPU1/tmpfs/capdrop/ROroot/networknone/UID65532 неизменны.
Node heaps дополнительно ограничены 192 MiB. Cleanup exact ID/name/run/profile.

UI предлагает профиль после inspection и удерживает несовместимость при polling,
смена окружения сбрасывает согласие. История показывает реальные tool names и
совместимость сохранённого snapshot. Разные environment/image не сравниваются
как исправление. Ограничения доверия Docker/kernel/user/filesystem и отсутствия
аттестации тестов/полезности assertions сохраняются. Кластер/OPA/F06 не расширяются.

Основания: [Vitest 4 API](https://github.com/vitest-dev/vitest/blob/v4.0.18/packages/vitest/src/node/create.ts),
[Vite config](https://vite.dev/config/),
[Vite shared options](https://vite.dev/config/shared-options.html),
[Vitest environment directives](https://vitest.dev/config/environment.html).
