# Восстановление Docker/kind и живая приёмка

15 октября 2026. Блокер живого локального конвейера устранён.

Причина подтверждена на компьютере: API-порт 52369 попал в excluded range
Windows 52367–52466. Прямой bind и отдельный stop/start Docker вернули ошибку
10013. Новый контейнер в сети kind публиковал порт нормально; API и сервисы
внутри исходной ноды работали. Общий сброс Docker, изменение системных исключений
или firewall не понадобились. Значение ошибки:
[Microsoft Winsock codes](https://learn.microsoft.com/en-us/windows/win32/winsock/windows-sockets-error-codes-2).

API перенесён на свободный `https://127.0.0.1:16443`, ниже текущего динамического
диапазона 49152–65535. Порт задан через
[kind apiServerPort](https://kind.sigs.k8s.io/docs/user/configuration/#api-server).
Перед созданием проверяется bind; TLS и guards не отключались.

Контейнер ноды заменён контейнером из того же закреплённого образа, с тем же
`/var` volume и IP-адресами. Восстановлены runtime-конфиги, сертификаты,
machine/product identity и mount wrappers. CA hash, marker и instance ID
сверены с исходными; etcd, Deployment и данные диска сохранены. Выделенные
endpoint/регистрация обновлены, reader-токен выпущен заново. Старый остановленный
контейнер удалён без `-v`; приватные архивы остались в
`.local/kind/port-repair-backup`, вне Git. PostgreSQL не пересоздавался.

Шесть учебных аварийных Pod без постоянных томов перезапущены для чистого
воспроизведения. Deployment и namespace сохранены. Десять реальных снапшотов
захвачены заново и синхронизированы с package assets: 6 положительных и
4 отрицательных. Сценарии проходят фазы независимо; live-тест детектора
собирает реальные CrashLoop-наблюдения разных workload в ограниченном окне.
Правило `waiting_reason == CrashLoopBackOff` не ослаблялось.

Проверено:

- **20 live-тестов прошли**: RBAC, read-only wrapper, collector, marker fail-closed,
  детектор/контекст, kind → анализ/fallback → policy → dry-run → PostgreSQL.
- Весь набор с `AEGISOPS_KIND_LIVE=1`: **449 passed, 1 skipped, 2 deselected**.
  Пропущен Windows symlink; платные API-тесты исключены по умолчанию.
- Нода Ready, `doctor --source local-kind` зелёный, API опубликован на loopback.
  Аудит completed-запуска прочитан из БД.
  [Безопасная сводка](docs/verification/kind-recovery/live-summary.json).
- Ruff/format, mypy, 5 границ import-linter, 29 схем, assets и Alembic check
  прошли. Добавлены проверки фиксированного порта и отказа до создания при
  невозможном bind. Зависимости не менялись.
- Pre-commit и gitleaks staged прошли без находок секретов.

В подробном выводе первого неуспешного теста оказался локальный app-пароль БД.
Он заменён, `.env` обновлён, данные и права сохранены. Pytest переведён на
`--tb=short`; отдельный тест с фейковым credential подтвердил, что значение
аргумента фикстуры не выводится. Реальных значений в отчёте нет.

Платный LLM на живых данных в этой приёмке не вызывался: проверены fake/fallback
и отсутствие исполнения. Уровень остаётся L0, без Shadow/MVP-A и подтверждения
качества модели. Приватные архивы содержат credentials и не публикуются.
