#!/usr/bin/env bash

set -euo pipefail

: "${UPDATE_SPEC_FILE:?}"
: "${1:?Usage: $0 bundled-provides-file}"

generated=$1
if [[ ! -s "${generated}" ]]; then
  echo "Bundled Provides file is empty: ${generated}" >&2
  exit 1
fi

awk -v generated="${generated}" '
BEGIN {
    while ((getline line < generated) > 0)
        replacement = replacement line "\n"
    close(generated)
}
/^# BEGIN generated bundled Provides$/ {
    print
    printf "%s", replacement
    in_block = 1
    found_begin = 1
    next
}
/^# END generated bundled Provides$/ {
    in_block = 0
    found_end = 1
}
!in_block { print }
END {
    if (!found_begin || !found_end)
        exit 1
}
' "${UPDATE_SPEC_FILE}" > "${UPDATE_SPEC_FILE}.tmp"
mv "${UPDATE_SPEC_FILE}.tmp" "${UPDATE_SPEC_FILE}"
