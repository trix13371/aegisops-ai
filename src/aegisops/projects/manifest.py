"""Selected configuration warnings; no interpolation, external schemas or execution."""

from collections.abc import Callable
from itertools import islice

from aegisops.projects.models import Finding, FindingCode

RECOMMENDATIONS: dict[FindingCode, str] = {
    "syntax": "Исправьте синтаксис и повторяющиеся ключи, затем повторите проверку.",
    "encoding": "Сохраните конфигурацию в UTF-8 и повторите проверку.",
    "read_failed": "Проверьте доступ к файлу и повторите проверку после исправления.",
    "yaml_alias": "Подготовьте YAML без ссылок. Исходник не меняется автоматически.",
    "privileged": "Уберите privileged: true. Необходимые привилегии обоснуйте и ограничьте.",
    "host_namespace": "Отключите доступ к пространствам хоста, если он не требуется приложению.",
    "mutable_image": (
        "Укажите версию вместо latest или закрепите образ digest sha256. Тег может изменяться."
    ),
    "missing_limits": "Задайте ненулевые limits.cpu и limits.memory с учётом нагрузки приложения.",
    "capabilities": "Уберите ALL/SYS_ADMIN; оставьте только необходимые Linux capabilities.",
    "privilege_escalation": (
        "Задайте allowPrivilegeEscalation: false, если повышение привилегий не требуется."
    ),
    "host_path": (
        "Замените hostPath подходящим томом. Необходимый доступ к хосту проверьте вручную."
    ),
    "docker_socket": (
        "Уберите доступ к Docker socket; при необходимости используйте "
        "отдельный ограниченный сервис."
    ),
    "limit": "Посмотрите ограничения профиля и проверьте пропущенные файлы отдельно.",
}


def mutable_image(image: object) -> bool:
    return (
        isinstance(image, str)
        and "@sha256:" not in image
        and (image.endswith(":latest") or ":" not in image.rsplit("/", 1)[-1])
    )


def dangerous_caps(value: object) -> bool:
    return isinstance(value, list) and any(
        isinstance(item, str) and item.upper().removeprefix("CAP_") in {"ALL", "SYS_ADMIN"}
        for item in value
    )


def docker_socket(value: object) -> bool:
    if not isinstance(value, str):
        return False
    path = value.replace("\\", "/").lower()
    return path in {"/var/run/docker.sock", "/run/docker.sock", "//./pipe/docker_engine"}


class ManifestChecks:
    def __init__(self, emit: Callable[[Finding], None], stacks: set[str]) -> None:
        self.emit = emit
        self.stacks = stacks
        self.partial = False
        self.remaining = 1000

    def warn(self, file: str, location: str, code: FindingCode, message: str) -> None:
        self.emit(
            Finding(
                severity="warning",
                code=code,
                file=file,
                location=location,
                message=message,
                recommendation=RECOMMENDATIONS[code],
            )
        )

    def bounded(self, items: list[object]) -> list[object]:
        if len(items) > 100:
            self.partial = True
        return items[:100]

    def check(self, document: object, file: str, location: str, depth: int = 0) -> None:
        if not isinstance(document, dict):
            return
        if self.remaining <= 0 or depth >= 8:
            self.partial = True
            return
        self.remaining -= 1
        if document.get("apiVersion") and document.get("kind"):
            self.stacks.add("Kubernetes")
            kind = document.get("kind")
            if kind == "List":
                items = document.get("items")
                if isinstance(items, list):
                    for index, item in enumerate(self.bounded(items)):
                        self.check(item, file, f"{location}.items[{index}]", depth + 1)
                return
            paths = {
                "Pod": ("spec",),
                "Deployment": ("spec", "template", "spec"),
                "StatefulSet": ("spec", "template", "spec"),
                "DaemonSet": ("spec", "template", "spec"),
                "ReplicaSet": ("spec", "template", "spec"),
                "ReplicationController": ("spec", "template", "spec"),
                "Job": ("spec", "template", "spec"),
                "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
            }
            spec: object = document
            # Do not traverse arbitrary CRDs or treat ConfigMap data as Pod specs.
            for key in paths.get(kind, ()) if isinstance(kind, str) else ():
                spec = spec.get(key) if isinstance(spec, dict) else None
                location += f".{key}"
            if isinstance(kind, str) and kind in paths and isinstance(spec, dict):
                self.pod(spec, file, location)
            return
        services = document.get("services")
        if isinstance(services, dict):
            self.stacks.add("Docker Compose")
            if len(services) > 100:
                self.partial = True
            for index, service in enumerate(islice(services.values(), 100)):
                if isinstance(service, dict):
                    # Ordinals locate services without copying untrusted names into reports.
                    self.compose(service, file, f"{location}.services[#{index + 1}]")

    def pod(self, spec: dict[str, object], file: str, location: str) -> None:
        for key in ("hostNetwork", "hostPID", "hostIPC"):
            if spec.get(key) is True:
                self.warn(
                    file, f"{location}.{key}", "host_namespace", "Pod использует пространство хоста"
                )
        volumes = spec.get("volumes")
        if isinstance(volumes, list):
            for index, volume in enumerate(self.bounded(volumes)):
                if isinstance(volume, dict) and isinstance(volume.get("hostPath"), dict):
                    self.warn(
                        file,
                        f"{location}.volumes[{index}].hostPath",
                        "host_path",
                        "Pod объявляет том с доступом к файловой системе хоста",
                    )
        for group in ("containers", "initContainers", "ephemeralContainers"):
            containers = spec.get(group)
            if not isinstance(containers, list):
                continue
            for index, container in enumerate(self.bounded(containers)):
                if not isinstance(container, dict):
                    continue
                target = f"{location}.{group}[{index}]"
                security = container.get("securityContext")
                if isinstance(security, dict):
                    if security.get("privileged") is True:
                        self.warn(
                            file,
                            f"{target}.securityContext.privileged",
                            "privileged",
                            "Контейнер запускается с privileged: true",
                        )
                    if security.get("allowPrivilegeEscalation") is True:
                        self.warn(
                            file,
                            f"{target}.securityContext.allowPrivilegeEscalation",
                            "privilege_escalation",
                            "Явно разрешено повышение привилегий",
                        )
                    caps = security.get("capabilities")
                    if isinstance(caps, dict) and dangerous_caps(caps.get("add")):
                        self.warn(
                            file,
                            f"{target}.securityContext.capabilities.add",
                            "capabilities",
                            "Контейнер запрашивает ALL или SYS_ADMIN",
                        )
                resources = container.get("resources")
                limits = resources.get("limits") if isinstance(resources, dict) else None
                if group != "ephemeralContainers" and (
                    not isinstance(limits, dict)
                    or not limits.get("cpu")
                    or not limits.get("memory")
                ):
                    self.warn(
                        file,
                        f"{target}.resources.limits",
                        "missing_limits",
                        "Для контейнера не заданы оба лимита CPU и памяти",
                    )
                if mutable_image(container.get("image")):
                    self.warn(
                        file,
                        f"{target}.image",
                        "mutable_image",
                        "Образ использует latest или не содержит явного тега/digest",
                    )

    def compose(self, service: dict[str, object], file: str, location: str) -> None:
        if service.get("privileged") is True:
            self.warn(
                file,
                f"{location}.privileged",
                "privileged",
                "Сервис Docker Compose использует privileged: true",
            )
        for key in ("network_mode", "pid", "ipc"):
            if service.get(key) == "host":
                self.warn(
                    file,
                    f"{location}.{key}",
                    "host_namespace",
                    "Сервис Compose использует пространство хоста",
                )
        if dangerous_caps(service.get("cap_add")):
            self.warn(
                file, f"{location}.cap_add", "capabilities", "Сервис запрашивает ALL или SYS_ADMIN"
            )
        if mutable_image(service.get("image")):
            self.warn(
                file,
                f"{location}.image",
                "mutable_image",
                "Образ использует latest или не содержит явного тега/digest",
            )
        volumes = service.get("volumes")
        if isinstance(volumes, list):
            for index, volume in enumerate(self.bounded(volumes)):
                source: object = None
                if isinstance(volume, str):
                    source = volume.split(":", 1)[0]
                elif isinstance(volume, dict) and volume.get("type") in ("bind", "npipe"):
                    source = volume.get("source")
                if docker_socket(source):
                    self.warn(
                        file,
                        f"{location}.volumes[{index}]",
                        "docker_socket",
                        "Сервис получает доступ к Docker socket, даже при read-only монтировании",
                    )
