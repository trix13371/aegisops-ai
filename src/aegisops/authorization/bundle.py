"""Validate packaged Rego/catalog content against the local bundle metadata."""

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Literal

from aegisops.actions.catalog import load_catalog
from aegisops.domain.common import DTO, Sha256
from aegisops.io import contained_file, read_json, read_text


class AuthorizationError(ValueError):
    def __init__(self) -> None:
        super().__init__("authorization operation failed")


class BundleConfig(DTO):
    policy_version: Literal["opa-l0-1"] = "opa-l0-1"
    bundle_hash: Sha256
    catalog_hash: Sha256


def digest(data: object) -> str:
    canonical = json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_config(root: Path) -> BundleConfig:
    try:
        catalog_hash = digest(load_catalog().model_dump(mode="json"))
        payload = {"policy_version": "opa-l0-1", "catalog_hash": catalog_hash}
        for filename in ("decision.rego", "api_authz.rego"):
            payload[filename] = read_text(contained_file(root, filename), 32768).replace(
                "\r\n", "\n"
            )
        return BundleConfig(catalog_hash=catalog_hash, bundle_hash=digest(payload))
    except Exception:
        raise AuthorizationError() from None


def load_config() -> BundleConfig:
    try:
        root = Path(str(resources.files("aegisops"))) / "assets/policies/opa"
        data = read_json(contained_file(root, "data.json"))
        if not isinstance(data, dict) or set(data) != {"aegisops_config"}:
            raise AuthorizationError()
        declared = BundleConfig.model_validate(data["aegisops_config"])
        if declared != compute_config(root):
            raise AuthorizationError()
        return declared
    except Exception:
        raise AuthorizationError() from None
