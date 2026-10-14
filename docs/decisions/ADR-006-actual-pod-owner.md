# ADR-006: сохранять непосредственного владельца Pod

14 октября 2026. Уточнение Owner DTO для S0-04.

Pod, созданный Deployment, содержит controller ownerReference на ReplicaSet.
Текущий read-only SA не может читать ReplicaSets. В Owner добавлен ReplicaSet;
имя ограничено контрактом, но больше не ограничивается 63 символами контейнера.
Сборщик сохраняет настоящий kind/name из единственного controller reference.
Если controller отсутствует, владельцем считается сам Pod. Неизвестный kind
или несколько controller references приводят к отказу.

Назначать Deployment по label или удалению суффикса ReplicaSet нельзя.
Дедупликация S0-05 будет стабильна при замене Pod внутри того же ReplicaSet.
Связать разные ReplicaSets после rollout с Deployment потребует отдельного
решения о чтении цепочки владельцев. RBAC и HTTP allowlist сейчас не расширены.
