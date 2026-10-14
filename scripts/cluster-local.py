"""Developer CLI. Explicit fixed cluster only; has no application execution API."""

import argparse

from local_kind import LocalClusterError, cluster_down, cluster_up, refresh_reader

parser = argparse.ArgumentParser()
parser.add_argument("operation", choices=("up", "down", "refresh-reader"))
parser.add_argument("--assert-context", default="kind-aegisops-demo")
arguments = parser.parse_args()
if arguments.assert_context != "kind-aegisops-demo":
    raise SystemExit("Foreign context refused before any cluster command")
try:
    {"up": cluster_up, "down": cluster_down, "refresh-reader": refresh_reader}[
        arguments.operation
    ]()
except LocalClusterError as error:
    raise SystemExit(str(error)) from None
