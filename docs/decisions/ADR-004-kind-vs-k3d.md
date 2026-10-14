# ADR-004: kind для локальных сценариев

Статус: принято пользователем; оформлено 14 октября 2026.

В техническом плане от 10 октября выбран kind; k3d не используется.
Целевой кластер — aegisops-demo, контекст kind-aegisops-demo,
namespace aegisops-demo с меткой aegisops.io/environment=local-demo.
Выделенный kubeconfig хранится внутри проекта и игнорируется Git.
Приложение не использует KUBECONFIG и ~/.kube/config.

Создание кластера, ServiceAccount, Role и локальные сценарии относятся к S0-03.
В S0-01 doctor проверяет только синтетический режим и локальную БД:
ему не нужны Kubernetes credentials или LLM-ключ. Read-only-доступ должен
подтверждаться RBAC и тестами S0-03, а не одной проверкой HTTP-метода.
