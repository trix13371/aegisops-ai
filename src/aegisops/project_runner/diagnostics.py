"""Hints from bounded, untrusted tool output; never install or resolve packages."""

import re

from aegisops.project_runner.models import ToolCheck


def missing_modules(check: ToolCheck) -> tuple[str, ...]:
    if check.tool != "pytest" or check.status not in {"error", "failed"}:
        return ()
    # Import names do not necessarily match distribution names (e.g. yaml/PyYAML).
    matches = re.findall(
        r"(?m)^(?:E\s+)?ModuleNotFoundError: No module named ['\"]"
        r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)['\"]",
        check.output,
    )
    return tuple(dict.fromkeys(name for name in matches if len(name) <= 128))[:20]
