# ADR-005: ограниченное чтение маркера namespace

14 октября 2026. Уточнение B.12 технического плана, необходимое для S0-03.

Namespace — cluster-scoped ресурс. Namespaced Role с pods/events/pods/log
не позволяет получить его label. Поэтому reader дополнительно получает
ClusterRole только с verb get, resource namespaces, resourceNames aegisops-demo.
ClusterRoleBinding связывает её только с SA aegisops-reader этого namespace.
Reader не может list/watch namespaces, читать другие namespaces или RBAC.

Это добавление к точной RBAC-матрице B.12, а не разрешение изменять кластер.
Доктор проверяет environment marker и уникальный instance-id, закреплённый
локальным bootstrap. Все изменяющие права, Secrets, ConfigMap contents,
exec/attach/portforward запрещены. Интеграционные тесты проверяют реальные 403.
