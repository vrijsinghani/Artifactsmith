#!/usr/bin/env bash
# Scan tracked files for private identifiers that must never be copied out of the private reference.
# Patterns are assembled from fragments at run time so this file never contains a forbidden string itself.
# Exits 1 on any hit.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Fragments (individually harmless; only the runtime combination is a private identifier).
o1=192
o2=168
o3=172
o4=100
o5=10
dom_a=neur
dom_b=alami
nm_a=Vi
nm_b=kas
ts_a=ts

domain="${dom_a}${dom_b}"                 # the reference provider domain
name="${nm_a}${nm_b}"                    # an operator's personal name
tsnet=".${ts_a}.net"                     # tailnet host suffix
ip160="${o1}.${o2}.1.160"                # reference internal proxy host
ip101="${o1}.${o2}.30.101"               # reference internal host

# Extended regex: RFC1918 / CGNAT hosts, tailnet suffix, the provider domain, the personal name, specific hosts.
RE="(${o1}\.${o2}\.|(^|[^0-9])${o5}\.[0-9]|(^|[^0-9])${o3}\.(1[6-9]|2[0-9]|3[01])\.|(^|[^0-9])${o4}\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.|${tsnet}|${domain}|${name}|${ip160}|${ip101})"

hit=0
for f in $(git ls-files); do
    [ "$f" = "scripts/denylist.sh" ] && continue   # patterns live only here, assembled from fragments
    if matches=$(grep -nEi -- "$RE" "$f" 2>/dev/null); then
        echo "$matches"
        hit=1
    fi
done

if [ "$hit" -ne 0 ]; then
    echo "DENYLIST: forbidden private identifier(s) found in tracked files above." >&2
    exit 1
fi
echo "denylist: clean ($(git ls-files | wc -l | tr -d ' ') tracked files scanned)."
