# Приёмка S0-03: локальный kind, reader RBAC и живые сценарии

14 октября 2026. S0-03 выполнена локально на Windows/Docker Desktop.
Кластер оставлен работающим для S0-04, credentials не включены в Git.

## Что реализовано

- kind 0.33.0, Kubernetes 1.36.4; node image закреплён по SHA256.
- Изолированный admin kubeconfig; отдельный reader SA и kubeconfig, CA pin,
  instance marker и запрос токена на 10 минут. Приложение не обновляет токен.
- Role pods/events get/list/watch, pods/log get. Дополнительно get единственного
  namespace aegisops-demo для проверки маркера (обоснование ADR-005).
- Никаких write/exec/attach/portforward, Secrets/ConfigMap/RBAC-read прав reader.
- Environment guard отвергает чужую среду, расширенный kubeconfig, auth plugins,
  TLS/proxy overrides, неправильные CA, SA subject, audience или срок токена.
- SDK создаётся вручную, не через default kubeconfig. GET-only transport
  проверяет URI/path/origin и expiry, запрещает redirects. Маркер проверяется
  перед каждым чтением. Сообщения не содержат SDK body/headers или токен.
- Разработческие скрипты up/down/refresh-reader/apply/capture работают только
  с зарегистрированным aegisops-demo, проверяют context, CA и Docker label.
- 10 digest-pinned Deployment-сценариев с restricted Pod Security и лимитами.
- Реальные локальные PodSnapshot с origin=local_kind сохранены отдельно от
  ручных synthetic_fixture. Capture не является runtime collector S0-04.
- doctor --source local-kind проверяет БД и reader/namespace marker.

## Проверки

**Полный прогон: 158 passed, 1 skipped.** Единственный пропуск — настоящий
Windows symlink без соответствующего права; остальные path/guard проверки
выполнены. 17 живых Kubernetes-тестов входят в этот результат.

Получены реальные HTTP403 на create/patch/delete, GET и POST exec,
attach/portforward, Secrets, ConfigMap, RBAC и чужой namespace; чтение наших
pods/events/logs проходит. SelfSubjectRulesReview и kubectl auth can-i --list
подтвердили эталон core-resource правил. Kubernetes также предоставляет
встроенные selfsubjectreviews и discovery GET, не дающие доступа к изменению
workload/секретам; application transport всё равно запрещает POST.

Удаление namespace marker блокирует следующее чтение; тест восстанавливает label.
Просроченный token, другой origin, небезопасный путь и non-GET блокируются
до обращения к underlying HTTP pool. Эксплуатационное поведение после TTL:
fail closed, затем developer refresh-reader.

Очистка реально выполнена: удалён только созданный kind, PostgreSQL сохранён.
После этого kind пересоздан, сценарии повторно применены и захвачены.
SHA256 стандартного ~/.kube/config не изменился за create/delete/recreate.
Чужой context и administrative auth plugin отвергаются до запуска команды.
Cleanup не требует живого namespace marker, но context/CA/Docker ownership
обязательны — не реализован небезопасный обход при произвольном отказе guards.

Ruff, format, mypy strict (19 source files), import-linter, schema export и
assets parity прошли. pip-audit: 88 зависимостей, 0 известных уязвимостей.
Gitleaks проверяет staged files и Git history. Отдельный pinned kind CI job
подготовлен; удалённо на GitHub не запускался.

## Наблюдения сценариев

- crashloop-exit-1, missing-env, bad-command и обе injection/canary фикстуры:
  реально наблюдался CrashLoopBackOff.
- OOM: наблюдались exit137, OOMKilled и CrashLoopBackOff.
- neg-healthy: Ready, без CrashLoopBackOff.
- neg-imagepull: ErrImagePull → ImagePullBackOff. Digest из 64 нулей намеренно
  отсутствует в registry; это отрицательный тест, не доступный образ.
- neg-missing-configmap-ref: CreateContainerConfigError.
- neg-old-restarts: процесс один раз завершился, затем стабильно Ready;
  restart_count=1 и отсутствие текущего CrashLoopBackOff.

При повторном capture опрос раз в 2 секунды/180 секунд пропустил краткое
состояние OOM. Интервал уменьшен до 0,25 секунды, timeout до 300 секунд:
все 10 статусов реально пойманы. Capture report сохраняет увиденную
последовательность, а не приписывает один статус всему времени наблюдения.
Данные созданы нашими workload; production-данные и реальные секреты не читались.

В Docker Desktop прямой kind load docker-image столкнулся с неполным
многоплатформенным index. Нужная платформа загружена в собственный node через
containerd по тому же pinned digest; registry/kubelet тоже поддерживают
обычную загрузку этого digest. Защитные настройки не отключались.

## Работа моделей и замечания

DeepSeek написал env_guard и safe_client. Первый объединённый ответ исчерпал
7500 токенов: он сохранён и не применялся. Два следующих ограниченных задания
дали полные файлы. Оркестратор исправил реальные интеграционные ошибки:
cluster name вместо context name, отсутствующую обязательность audience,
nested SA claims, bounded CA read, SDK template path rendering, expiry на
каждом уровне транспорта, marker timeout и проверку container name.

Gemini написал отрицательные guard unit-тесты, которые прошли после интеграции,
и выполнил финальное ревью. Оркестратор подготовил локальную инфраструктуру,
fixture capture, тесты transport/live RBAC/cleanup, CLI, CI и документацию.
Ревью предложило унифицировать origin extraction — исправлено. Замечание про
cleanup обработано с сохранением ownership proof; безусловное удаление при
любой ошибке verify_cluster не допускается.

JWT claims проверяются структурно, подпись проверяет apiserver. Это не
доказательство безопасности при компрометации хоста или arbitrary Python.
SDK типы upstream не имеют stubs: исключение mypy ограничено этим пакетом
и единственным наследованием ApiClient, остальные исходники проверяются strict.

## Расход OpenRouter

| Вызов | Вход | Выход | USD |
|---|---:|---:|---:|
| DeepSeek: незавершённый общий ответ | 1176 | 7500 | 0.0045263424 |
| DeepSeek: environment guard | 1229 | 4077 | 0.0024626704 |
| DeepSeek: safe client | 1440 | 2704 | 0.0016435968 |
| Gemini: guard tests | 4589 | 2758 | 0.0137842500 |
| Gemini: финальное ревью | 10124 | 276 | 0.0086280000 |
| **Итого S0-03** | | | **0.0310448596** |

Все попытки учтены. Расход работы Codex/оркестратора сюда не входит.
Общий расход подключённого ключа ≈ $0.126839261; остаток общего лимита
≈ $4.873160739 из $5 на момент проверки. Это не общий баланс аккаунта.
Usage, JUnit, capture report и isolation proof: docs/verification/s0-03/.

## Версии и источники

- [kind release v0.33.0](https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0).
- [kind explicit kubeconfig](https://kind.sigs.k8s.io/docs/user/quick-start/).
- [Kubernetes RBAC](https://kubernetes.io/docs/reference/access-authn-authz/rbac/).
- [Короткоживущий token request](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_create/kubectl_create_token/).

Python 3.12.14, Kubernetes Python Client 36.0.3, kubectl 1.36.1.
kind Windows SHA256: 4b22adaa135368c5a465d56bbd8e520cbea87272a06ca00b6078e7b81515c9fc.
kind node digest: 099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed.
Python workload digest: a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1.

Следующая задача S0-04 — bounded read-only collector/normalization через
safe_client. Detector, sanitizer, LLM, PolicyChecker/DryRunEngine и исполнение
не реализованы в S0-03; production-готовность не заявляется.
