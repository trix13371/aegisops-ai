# F07 — подпись, TTL и отзыв policy-релизов

Проверено локально 15 октября 2026. Только L0, без реального исполнения.

Новые OPA-mode запуски требуют detached Ed25519 manifest точных хешей Rego,
API guard и каталога, актуальную revision и срок ≤24 часов. Отдельный ключ
политики защищён DPAPI и находится вне репозитория/БД; ключ аудита не переиспользован.
PEP проверяет пакет до OPA и перед dry-run. Launcher тоже проверяет релиз.
Прежний offline Python mode остаётся режимом по умолчанию.

Проверено:

- Полный pytest: **800 passed, 1 skipped, 2 deselected**, с настоящими
  PostgreSQL, kind и OPA. Включены 20 kind- и 18 OPA-тестов. Пропущен
  недоступный Windows symlink; два платных LLM-теста исключены.
- 62 новых F07-кейса: правильная/неверная подпись и другой ключ, точная граница
  expires_at, ещё не действующий пакет, отзыв, revision ниже floor, подмена
  active/content/catalog hash, строгие типы и TTL, отсутствие/повреждение
  metadata, ошибка reader, отказ публикации, сохранение отзывов при продлении.
  Истечение, отзыв, renewal или откат времени **после** ответа PDP отменяют allow.
- Неполное/поддельное доказательство не может заявить valid; v2 receipt требует
  оба check. Ошибки чтения дают корректный blocked receipt, без исходного текста
  исключения и без вызова PDP. Исторические v1 записи остаются читаемыми.
- Шесть вариантов receipt с настоящими файлами и PostgreSQL сохраняются и
  совпадают при чтении, даже после удаления текущего state. Valid-path дополнительно
  проверен с настоящим OPA. CLI без signed release сохраняет policy_missing/blocked.
- Обычные команды CLI с реальным локальным trust/key/state и OPA:
  revision 1 → completed; revoke 1 → policy_revoked/blocked без dry-run;
  новый revision 2 → completed. Все три записи совпали при чтении из БД.
- `run --source local-kind --authorization opa`: 10 наблюдений, 6 инцидентов,
  6 valid signed-policy v2 receipt revision 2, completed, durable readback равен.
  Использован fixture-v2; платного анализа инцидентов нет.
- Ruff/format, mypy (76 файлов), 12 import-контрактов, 51 JSON Schema/эталонов,
  metadata/assets, Alembic check и doctor local-kind прошли. Миграция не нужна.
  `pip-audit`: известных уязвимостей не найдено; локальный неопубликованный
  aegisops-ai отдельно пропущен сканером.
- 25 Rego-тестов прошли; сами правила действий и полномочия не расширялись.
- Wheel из sdist извлечён отдельно: импорт именно из wheel, проверка публичного
  signed-policy эталона на сохранённый момент времени и metadata прошли.
  Ключ и операторский signer не упакованы.
  [Wheel smoke](docs/verification/f07-policy/wheel-smoke.json).
- История F06 обновлена отдельной ручной точкой: **777 строк**, verified,
  modified/missing/unanchored — 0.
  [Audit verify](docs/verification/f07-policy/audit-verification.json).
- Pre-commit и gitleaks staged прошли. CI дополнен отдельным ephemeral policy
  key/release для тестов; удалённый workflow не запускался.

DeepSeek подготовил unit-тесты; исправлены путь импорта/тип signature и слишком
широкие assertions, удалены проверки вида f(x)==f(x). Gemini независимо просмотрел
границы и предположил, что policy_invalid без доказательства нельзя сохранить.
Это не подтвердилось: контракт допускает такую безопасную диагностику, а
реальный исключающий reader и испорченные файлы дают валидный blocked receipt.
Моделям передавался только код, без приватных ключей, телеметрии и state-файлов.

Итоговое состояние: активна revision **2**, revision **1 отозвана**, глобальный
kill switch включён. Срок текущего релиза и UUID приёмки — в
[машинной сводке](docs/verification/f07-policy/summary.json). Через 24 часа
нужен явный операторский `issue`; автоматического продления нет.

Это формат manifest PEP, не нативный OPA JWS bundle. Локальные trust/state
(state не подписан), ОС и часы доверены; совместный rollback этих файлов,
KMS/HSM, remote revocation feed, ротация ключей, identity и аттестация процесса
не реализованы. Старый аудит проверяется по сохранённым доказательствам и не
подтверждает внешний trust сам по себе; F06 защищает его только с момента
ручного снимка. Это не production-допуск и не завершение MVP-A.
Решение: [ADR-016](docs/decisions/ADR-016-signed-policy-releases.md).
