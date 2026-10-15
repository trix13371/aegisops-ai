"""One bounded checked-in catalog; no user-selected paths or remote retrieval."""

import re
from importlib import resources
from pathlib import Path

from aegisops.io import contained_file, read_yaml
from aegisops.runbooks.models import RunbookCatalog, RunbookDocument, RunbookError


class RunbookRepository:
    def __init__(self, root: Path | None = None) -> None:
        try:
            directory = (
                root
                if root is not None
                else Path(str(resources.files("aegisops"))) / "assets/runbooks"
            )
            self.catalog = RunbookCatalog.model_validate(
                read_yaml(contained_file(directory, "catalog.yaml"), max_bytes=8192)
            )
        except Exception:
            raise RunbookError() from None

    def list(self) -> tuple[RunbookDocument, ...]:
        return tuple(sorted(self.catalog.documents, key=lambda doc: doc.id))

    def get(self, identifier: str) -> RunbookDocument:
        try:
            if (
                type(identifier) is not str
                or re.fullmatch(r"[a-z][a-z0-9-]{0,31}", identifier) is None
            ):
                raise RunbookError()
            return next(doc for doc in self.catalog.documents if doc.id == identifier)
        except Exception:
            raise RunbookError() from None
