"""Read developer-owned local emergency controls; absence or corruption is a denial."""

import json
from pathlib import Path

from aegisops.authorization.bundle import AuthorizationError
from aegisops.context.sanitizer import sanitize_data
from aegisops.domain.authorization import AuthorizationControl
from aegisops.io import contained_file, read_text, unique_object


def load_control() -> AuthorizationControl:
    try:
        path = contained_file(Path.cwd(), ".local/authorization/control.json")
        control = AuthorizationControl.model_validate(
            json.loads(read_text(path, 4096), object_pairs_hook=unique_object)
        )
        if sanitize_data(control.model_dump(mode="json")) != control.model_dump(mode="json"):
            raise AuthorizationError()
        return control
    except Exception:
        raise AuthorizationError() from None
