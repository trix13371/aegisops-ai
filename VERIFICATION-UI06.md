# UI06 — сравнение сохранённых проверок

Завершено 16 октября 2026. Статическая история и история Python-runner получили
выбор исходного/нового результата и кнопку «Сравнить отчёты». Сравнение читает
существующие файлы и не создаёт новую проверку, контейнер или запрос модели.

Проверено:

- Offline unit/contract/dashboard/launcher/F01: **715 passed, 3 skipped**,
  `.local/ui06-tests.xml`. Пропуски относятся к Windows symlink/POSIX mode.
  Целевая выборка сравнения/HTTP: **76 passed**, `.local/ui06-targeted.xml`.
- 17 новых pure unit cases: multiset/дубликаты, появившиеся/исчезнувшие/совпавшие,
  rename/location/line/code/severity, изменение формулировки, empty reports,
  partial/версии правил, одинаковый UUID/разные проекты. Runner: переходы статусов,
  snapshot identity, другой image, pending, truncated/unconfirmed и неверная связь
  результата. Логи с canary не входят в сравнительный ответ.
- HTTP: обязательная сессия, malformed UUID, одинаковые отчёты, разные проекты,
  удалённая регистрация и pending. Comparison API не вызывает child/model и не
  создаёт job. Тесты используют временные реестры, не личную историю пользователя.
- В браузере создан отдельный «Демо · сравнение проверок». До изменения учебных
  файлов — 1 static finding и failed Ruff/pytest; после изменения — 0 findings,
  оба инструмента passed. Два реальных запуска Docker завершились с подтверждённым
  удалением контейнеров. Файлы изменены только в отдельной учебной папке.
- Static comparison: появились 0, больше не обнаружены 1 (privileged), остались 0.
  Пара `870e6717-dcaa-4275-8180-3ab26b3a58ef` →
  `dbb79614-ba03-47ce-8293-fac99aae7bc8`.
- Runner comparison: Ruff и pytest «Найдены ошибки → Пройдено», снимок изменился,
  image ID одинаковый. Пара `488c7c1b-d9c4-4cd9-ae72-a3d55bdd6193` →
  `a5cf423f-8d1d-40c5-aae9-7d68444fe6d5`.
- Один элемент с двух сторон отключает кнопку. Escape закрывает экран; select
  входит в focus trap. После перезапуска UI история и сравнение открываются без
  повторного исполнения. Ошибок JS нет. Локальные screenshots:
  `.local/dashboard/screenshots/static-comparison.jpg`, `run-comparison.jpg`.
- Ruff/format, mypy (**108 source-файлов**), **20 import-контрактов** прошли.
  Wheel собран из sdist; comparison-модули/UI включены, .local/ключи исключены.
  Зависимости, Docker-профиль/образ, DB migrations и incident schemas не менялись.
  Полный прежний runner_live/PostgreSQL/kind/OPA suite не повторялся; изменения
  не затрагивают запуск. Платные модели не вызывались, F06 checkpoint не выпускался.

Ограничения: static identity привязана к координатам; rename/line shift/изменение
области могут выглядеть как исчезновение/появление. Это не доказательство исправления.
У runner нет структурированных case IDs — сравниваются только статусы инструментов,
не отдельные тесты/число ошибок/сырой вывод. Подлинность тестов внутри Docker не
аттестована. При partial/rules/image/scope mismatch оценка отключается.
Сохраняются trusted single-user/filesystem и прежние ограничения локальных отчётов.
Решение: [ADR-025](docs/decisions/ADR-025-saved-check-comparisons.md).
