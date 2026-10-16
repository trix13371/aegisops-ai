# ADR-027: изолированные проверки JavaScript/TypeScript

Принято 16 октября 2026. Продолжаем локальную функциональность, размещение в
интернете остаётся отдельным будущим треком.

Новый javascript-checks-v1 / node-js-v1 использует существующий runner: durable
claim, ограниченный снимок, immutable image ID, strict result, cleanup и историю.
Ни одной команды проекта на хосте; API принимает только закрытую пару environment
и consent=profile. RunClaim/RunResult сохраняют profile и environment; связи
проверяются до записи и при чтении. Старые записи остаются Python/minimal.
Пара инструментов строго зависит от профиля, результаты нельзя смешать.

Node 24.22.0 взят из официального node:24-bookworm по linux/amd64 digest
7f7f17f773027e7f0475fa829fc0d793d75918fda85b9e517c9cc5aa46200406.
В образе есть Python 3.11 для существующего доверенного bounded capture/unpack;
он не используется для исполнения Python-файлов проверяемого проекта.
package-lock фиксирует TypeScript 5.9.3, @types/node 24.10.1, undici-types 7.16.0
с registry integrity. npm ci --ignore-scripts --no-audit --no-fund выполняется
только в доверенном build context, до передачи каких-либо файлов проекта.
Контекст закрыт runner/.dockerignore; image pin включает этот файл, все JS tools
и общий python-v1/entry.py. Изменение любого из них требует новой подготовки.

Compiler API получает явные options и root file list, не читает tsconfig и не
загружает плагины: noEmit/strict/NodeNext/ES2022, allowJs, checkJs=false,
allowImportingTsExtensions, фиксированные Node typings. Проверяет синтаксис JS
и типы TS, не запускает исходники. Это профиль проверки, не сборка проекта.
package.json остаётся источником обычной семантики ESM/CJS и module resolution.

Node --test получает явный список *.test.*, *.spec.*, test.*, spec.* с
.js/.mjs/.cjs/.ts/.mts/.cts, concurrency=1, TAP. Никаких npm scripts/install,
Jest/Vitest hooks или внешних runner. TS выполняется type stripping средствами
Node, не через transpilation/tsconfig; enum/JSX/TSX не поддержаны.
Ноль найденных файлов → no_tests/5, общий статус issues. Как и у прежнего runner,
успех инструмента не доказывает наличие полезных assertions или честность тестов.

Снимок содержит только код выбранного языка и прежние ограниченные data-файлы;
не включает зависимости, типичные secret filenames и ссылки. Прежние границы
размера/глубины/времени сохраняются. SnapshotInfo допускает Python=0 для JS;
RunResult требует ненулевое число файлов своего языка. JS declarations учитываются
как файлы профиля, а не как найденные тесты.
Регистр расширений у Node/TypeScript важен: snapshot отвергает .JS/.TS/.MtS
и другие неканонические варианты, вместо молчаливого пропуска в госте.

Изоляция прежняя: network none, UID65532, RO root, no capabilities/new privileges,
1 CPU/512MiB/64pids, bounded tmpfs, без host mounts/socket/credentials.
Compiler timeout20, Node timeout40, attach75, stdout24KB/tool. Процессные группы
убиваются при лимите; cleanup проверяет точный ID/name/run и выбранный profile
label. Никакого broad prune. Логи недоверенные и локальные, модели не вызываются.
Смена environment/image блокирует сравнение. Сохраняются ограничения Docker и
доверие локальному пользователю, daemon/kernel/filesystem, без аттестации тестов.

Справочные основания:
[Node TypeScript support](https://nodejs.org/docs/latest-v24.x/api/typescript.html),
[Node test runner](https://nodejs.org/docs/latest-v24.x/api/test.html),
[TypeScript compiler options](https://www.typescriptlang.org/tsconfig/),
[официальный Dockerfile Node](https://github.com/nodejs/docker-node/blob/main/24/bookworm/Dockerfile).
