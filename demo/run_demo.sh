#!/bin/bash
# Builds the toy demo page using fabricated data (fakebin/) for a fictional lab and cluster.
# Doesn't touch Slurm, doesn't touch your real config.env or index.html: it runs the exact same
# bin/build_usage_page.py, pointed at this folder instead (HPCUSAGE_ROOT), with the fake
# sacctmgr/sshare/sacct/scontrol in fakebin/ standing in on PATH.
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
export PATH="$HERE/fakebin:$PATH"
export HPCUSAGE_ROOT="$HERE"
python3 "$ROOT/bin/build_usage_page.py"
echo "-> open $HERE/index.html in a browser"
