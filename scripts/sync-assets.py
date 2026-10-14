"""Copy canonical checked-in inputs into wheel package assets; no app writes."""

import argparse
import shutil
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
arguments = parser.parse_args()
root = Path(__file__).resolve().parents[1]
assets = root / "src" / "aegisops" / "assets"
for name in ("actions", "scenarios", "prompts", "fixtures"):
    source = root / name
    expected = {str(path.relative_to(source)): path for path in source.rglob("*") if path.is_file()}
    destination = assets / name
    if arguments.check:
        actual = {
            str(path.relative_to(destination)) for path in destination.rglob("*") if path.is_file()
        }
        if actual != set(expected) or any(
            (destination / relative).read_bytes() != path.read_bytes()
            for relative, path in expected.items()
        ):
            raise SystemExit("Packaged assets differ from canonical inputs")
    else:
        shutil.copytree(source, destination, dirs_exist_ok=True)
print("Package assets verified." if arguments.check else "Package assets synchronized.")
