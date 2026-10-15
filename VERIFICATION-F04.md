# F04 — версионируемые диагностические runbook

15 октября 2026. Локальный срез п. 4.5 Foundation & Safety. Добавлены
канонический каталог из трёх public инструкций, строгие DTO, bounded loader,
чистый индекс по pod_status и CLI `runbook list/show` без сети/БД/ключа.

Runbook включаются как недоверенные справочные данные в analysis-v2. Они не
становятся evidence или разрешениями. OOM выбирается только по OOMKilled,
не по коду 137/логам; общий runbook обязателен, специальный — необязателен.
До модели доходят максимум два документа и 2048 байт. Объём одного документа
ограничен 1024 байтами; общий предел контекста/RunBudget не увеличен.

Хеши текста и версии связаны с выборкой и prompt hash. В LLMMetadata добавлены
три поля ID@version/catalog version/selection hash; вместе с returned_model
и двумя fallback-полями record укладывается в прежний лимит 16 parameters.
Схема БД не менялась, исторический аудит v1 сохранён. Offline fixtures
перевыпущены как fixture-v2; старые eval-отчёты fixture-v1 читаются.

Проверено:

- Полный pytest с БД и `AEGISOPS_KIND_LIVE=1`: **624 passed, 1 skipped,
  2 deselected**, включая 20 живых kind-тестов. Пропущен Windows symlink;
  два платных API-теста исключены по умолчанию.
- 37 новых safety/integration-кейсов: статус против логов/имён/exit 137,
  bool exit code, отсутствие текущего CrashLoop, границы документов/выборки,
  версии/ID/классы данных, дубликаты, secret-shaped текст, unsafe YAML,
  символические root/file, ID traversal, изменение хешей, подмена неактивной
  инструкции даже с пересчитанными хешами, инструкция только как JSON-данные,
  отказ цитирования runbook вместо evidence, CLI и полный metadata roundtrip в БД.
- Три проверки настоящего loopback HTTP дополнены проверкой фактического
  справочного JSON, selection hash и разделения runbook/evidence ID.
  Ответ даёт контролируемый тестовый сервер, не настоящая локальная модель.
- Повторный offline eval сохраняет все 10 результатов, 6 fake-вызовов;
  причины и допустимые действия 6/6, leaks/execution — 0. Это механика fixtures,
  не измерение качества LLM. [Сводка](docs/verification/f04-runbooks/summary.json),
  [eval](docs/verification/f04-runbooks/eval-report.json).
  Резерв шести разборов — 57840 при прежнем лимите 65536.
- Ruff/format, mypy (62 файла), 9 import-контрактов, 36 JSON Schema с эталонами,
  package assets, Alembic check и doctor local-kind прошли.
- Wheel собран; обязательные assets присутствуют. Отдельный импорт из
  распакованного wheel и бесплатный analyze OOM прошли без editable-копии
  пакета. [Проверка wheel](docs/verification/f04-runbooks/wheel-smoke.json).
- Pre-commit и gitleaks staged прошли без находок секретов.

DeepSeek подготовил чистый selector; типы DTO и фиксированные ошибки исправлены
перед применением. Gemini независимо просмотрел границы. Замечания о production
namespace, symlink root и обязательности всех специальных документов не приняты:
это расходится с явными границами L0 или уже покрыто contained_file/DTO.
Соответствующие отказы и допустимые случаи проверены тестами.
Телеметрия и отзывы внешним моделям в этой приёмке не передавались; платного
анализа инцидентов нет. Удалённый CI не запускался.

Результат не подтверждает улучшение качества настоящей модели или её устойчивость
к prompt injection. Подпись/четыре глаза не реализованы программно. Каталог
поддерживает только public учебные документы; internal runbook требуют отдельного
объединения классов данных с маршрутизацией. Уровень остаётся L0.
Решение: [ADR-013](docs/decisions/ADR-013-versioned-runbooks.md).
