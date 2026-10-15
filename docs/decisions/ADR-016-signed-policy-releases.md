# ADR-016 — подписанный локальный релиз OPA для PEP

Принято 15 октября 2026. F07, продолжение Foundation & Safety, только L0.

В режиме `--authorization opa` обязательны подпись Ed25519, актуальный релиз
политики, две проверки срока/отзыва и прежние Python/OPA/kill-switch проверки.
Fallback на неподписанный пакет, legacy receipt или Python при отказе нет.
Режим по умолчанию `--authorization python` остаётся прежним offline-треком.

Формат `aegisops-local-policy-v1` — detached signed manifest. Он связывает
release UUID, возрастающую revision, версию схемы policy `opa-l0-1`, SHA-256
Rego/API-authz/каталога, key ID и UTC issued_at/expires_at. Нормализованные LF
хеши файлов пересчитываются F05 load_config; каталог также входит в bundle hash.
Lifetime >0 и ≤24 часов, граница expires_at исключена. Domain separation
для подписываемых байтов и hash релиза отличается от F06 audit.

Это локальный формат PEP, не нативный OPA tar.gz/JWS `.signatures.json` и не
Bundle Service. [Нативные bundles OPA](https://www.openpolicyagent.org/docs/management-bundles)
имеют отдельный протокол. Native OPA продолжает загружать фиксированные три
файла; Windows launcher сначала проверяет signed release и соответствие assets.
PEP проверяет manifest независимо от ответов OPA. Loopback-процесс остаётся
доверенным как в F05: криптографической аттестации работающего процесса нет.

Отдельный policy signing key лежит в `work/policy-signing/`, вне репозитория/БД;
это другой ключ, чем у F06. Developer tool использует существующие операторские
DPAPI/0600 и exclusive-lock примитивы F06. Приложение только проверяет публичную
подпись. Нет KMS/HSM, four-eyes, разделения OS identity или online signer.

`.local/policy-releases/trust.json` — закреплённый публичный ключ.
`state.json` — доверенное локальное **неподписанное** состояние оператора:
generation, minimum_revision, active_release_hash, список отозванных revision
и changed_at. Приложение его никогда не пишет. `issue` автоматически выбирает
следующую revision, создаёт подписанный content-addressed файл и атомарно
продвигает state/floor. `revoke` сохраняет отзыв и повышает generation.
Продление истёкшего/отозванного пакета требует нового релиза с большей revision;
отзывы прежних revision сохраняются. Старый пакет под текущим неизменённым
state отвергается. Откат самого доверенного state/trust владельцем хоста не
покрывается; это не rollback-resistant hardware storage.

Публикация использует одного оператора, exclusive create, flush/fsync и
атомарную замену state.json. Ошибка до замены оставляет старый active state;
orphan release не активируется автоматически. Stale lock не захватывается.
Повреждённый активный файл препятствует следующим операторским операциям:
нужно восстановить проверенный исходный файл, а не сбрасывать trust/floor.

PEP читает release/trust/state перед OPA и снова перед dry-run. Неверная
подпись/хеш/тип/срок, отсутствие файла, отзыв, revision ниже floor, изменение
состояния или откат времени между проверками дают blocked без dry-run.
Приоритет причин: Python deny → control invalid/kill/change → corrupt packaged
bundle → release status/change → OPA error/deny. Ошибка чтения/clock не даёт allow.
Фиксированные причины пишутся в аудит. Неуспешная запись аудита по-прежнему не
даёт успешного результата CLI.

Новые receipt — `opa-dry-run-v2` с двумя PolicyReleaseCheck: время, signed
manifest, публичный trust и снимок state. При append/read подпись и вердикт
перепроверяются чисто, относительно сохранённого времени и проекции запроса.
Текущие файлы/часы при историческом чтении не используются. Старые v1 receipt
остаются читаемыми и явно не претендуют на F07. JSONB расширен без миграции.
Сам audit receipt не является удалённой аттестацией trust/state; его история
защищается отдельными ручными снимками F06.

Чтения bounded: trust ≤2048 байт, state и manifest ≤8192 байт, до 128 отзывов,
без duplicate keys, symlink или произвольных путей/URL. Версия схемы `opa-l0-1`
пока фиксирована; revision — номер выпуска того же policy-контракта. Ротация
ключей, распределение/подписанный revocation feed, trusted time, cache leases,
SPIFFE/mTLS, native bundle delivery и реальный исполнитель — следующие этапы.
Часы/оператор/ОС доверены: возврат системных часов внутрь старого valid window
между процессами без сохранённого нарушения этой моделью не обнаруживается.
