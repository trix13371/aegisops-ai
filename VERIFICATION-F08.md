# F08 — взаимная аутентификация PEP и OPA

Проверено локально 15 октября 2026. Только L0 и описательный dry-run.

Обычный HTTP к локальному PDP заменён TLS 1.3 с отдельным CA, clientAuth/serverAuth,
IP SAN и закреплением точных сертификатов. OPA пропускает только identity PEP
на свой decision API. Ключи и сертификаты установки находятся вне Git.

Проверено:

- Полный pytest: **870 passed, 2 skipped, 2 deselected** с реальными PostgreSQL,
  kind и OPA. Пропущены недоступная Windows symlink-привилегия и проверка
  POSIX-режима файла; платные LLM smoke-тесты исключены.
- Реальные TLS handshakes: правильные сертификаты; отсутствие клиентского
  сертификата; клиент от другого CA; TLS 1.2; неподходящий hostname;
  просроченный серверный сертификат; другой серверный leaf того же CA;
  обычный HTTP. При отказах HTTP-проекция не передавалась тестовому серверу.
- Настоящий OPA с изолированным CA: клиент с subject `aegisops-pep` получает 200,
  клиент того же CA с subject `other-client` — 401, включая попытку подставить
  нужную identity в заголовках. Пути сертификатов тестировались с пробелами.
- Закреплённые файлы: отсутствие, подмена, oversized/corrupt данные, лишний CA,
  duplicate JSON keys, path escape, TTL и точная граница expiry, несовпадение
  pins. Ротация trust во время ответа отменяет результат. При недоступной TLS
  identity нет HTTP fallback, blocked receipt сохранён и прочитан из PostgreSQL.
- Оператор: новые pins без перезаписи старых ключей; общий срок ≤24 часов;
  CA signing key не сохраняется; lock не перехватывается; ошибка публикации
  оставляет прежний trust. Приложение не запускает signer или shell.
- Четыре обычных CLI review с настоящими файлами и OPA: allow → completed;
  обновление TLS identity при старом запущенном сервере → opa_unavailable/blocked;
  рестарт собственного OPA → completed; kill switch → blocked.
  Все четыре записи полностью совпали при чтении из БД.
- `run --source local-kind --authorization opa`: 10 наблюдений, 6 инцидентов,
  все 6 прошли mTLS, signed-policy v2 и durable readback; исполнений 0.
  Использован бесплатный fake/fallback, внешней передачи internal-данных нет.
  [Ручные проверки](docs/verification/f08-mtls/manual-checks.json).
- Новый API guard вошёл в bundle hash; выпущен policy release revision 3 тем же
  отдельным F07 signing key, старые отзывы сохранены. После проверки глобальная
  блокировка включена, control generation 11. TLS identity обновлена и OPA
  перезапущен; ротация не снимает kill switch.
- Ruff/format, mypy (77 source-файлов), **13 import-контрактов**, 51 schema/эталон,
  package assets и Alembic check прошли. Новая миграция не нужна.
- **31 Rego-тест** прошёл. `pip-audit`: известных уязвимостей нет; локальный
  неопубликованный пакет aegisops-ai сканером отдельно пропущен.
- Wheel построен из sdist и извлечён отдельно: импорт именно из wheel,
  TLS context/pins и bundle metadata проверены с текущими локальными
  зависимостями. PEM/DPAPI/key-файлы не упакованы.
  [Wheel smoke](docs/verification/f08-mtls/wheel-smoke.json).
- Ручной signed snapshot защищает **953 строки**, статус verified,
  missing/modified/unanchored — 0.
  [Audit verify](docs/verification/f08-mtls/audit-verification.json).

Gemini независимо просмотрел транспорт и выпуск identity. Предположение о
неработающем quoting Start-Process не подтвердилось живым запуском и readiness;
нужные кавычки сохранены. Два обрезанных предложения DeepSeek не применялись;
тесты подготовлены и проверены локально. Workflow CI обновлён для mTLS,
удалённо пока не запускался.

Ограничения: один доверенный host/user/operator, native PEM под ACL/0600,
ручной выпуск и рестарт, часы ОС. Нет SPIFFE/SPIRE, разграничения OS identity,
HA, gRPC Cluster Agent или реального исполнителя. Сертификаты действуют до
24 часов; истечение закрывает OPA-трек, не включает fallback на Python.
Исторические v2 receipts не являются доказательством наличия TLS задним числом.
Это локальный срез Foundation & Safety, не завершённый Этап 1 или допуск L2.
