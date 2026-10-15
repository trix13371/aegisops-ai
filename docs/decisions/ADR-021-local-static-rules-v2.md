# ADR-021: расширение локальных правил и рекомендации

16 октября 2026. Следующий шаг после UI02 — улучшить выбранные контейнерные
проверки без добавления runner, сетевого доступа или модели.

Разбор конфигурации вынесен в `projects.manifest`. Kubernetes: стандартные Pod,
Deployment/StatefulSet/DaemonSet/ReplicaSet/ReplicationController/Job/CronJob,
List; containers/initContainers/ephemeralContainers. Проверяются явные privileged,
allowPrivilegeEscalation, ALL/SYS_ADMIN, пространства hostNetwork/hostPID/hostIPC,
объявления hostPath, latest/отсутствие тега, наличие CPU+memory limits. Для
ephemeralContainers limits не требуются. Произвольные CRD и ConfigMap не
интерпретируются как Pod. Это выбранные эвристики, не Pod Security admission.

Compose: privileged, network_mode/pid/ipc: host, cap_add ALL/SYS_ADMIN, latest/
отсутствие тега, прямое подключение /var/run/docker.sock, /run/docker.sock или
Windows docker_engine pipe. Поддержаны short syntax и long bind/npipe; read-only
не подавляет предупреждение. Именованные тома, переменные, include/extends,
наследование и внешние файлы не разрешаются; произвольные socket aliases и
прокси не распознаются. Отсутствие предупреждения не означает изоляцию Docker.

Семантика сверена с первичными источниками:
[Kubernetes Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/)
и [Docker Compose services](https://docs.docker.com/reference/compose-file/services/).
Это не реализация всех требований этих спецификаций.

Каждое новое замечание содержит фиксированную рекомендацию; контейнерные —
ограниченный структурный location с известными ключами и индексами. Значения
конфигурации и имена сервисов/контейнеров не переносятся. document нумеруется
с 1, индексы массивов с 0, services[#N] — сервисы в порядке записи с 1.
Python location остаётся номером строки парсера; текст ошибки не копируется.

Сохранён формат local-static-v1 с обратно совместимыми полями: rules_version
по умолчанию local-static-v1, location/recommendation — None. Новые scanner-запуски
явно записывают local-static-v2. Старые отчёты не переписываются и не получают
задним числом новые проверки. UI показывает версию и рекомендации при наличии.

Обход манифестов ограничен 100 элементами каждого массива/набора сервисов,
8 уровнями List и 1000 объектами на запуск. Усечение даёт partial. Сохраняются
лимиты исходного сканера и 200 замечаний; последнее оставлено для partial summary.
Неподдержанные структуры не являются schema errors: проверка форматов не
гарантирует валидность Kubernetes/Compose. Исправления выполняет пользователь
после оценки назначения приложения; предупреждение не запускает действий.

Проверки остаются локальными, без запуска кода, тестов, команд, модели или
автоматического исправления. Project reports по-прежнему не входят в F06 audit.
