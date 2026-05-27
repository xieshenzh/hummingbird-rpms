#!/usr/bin/bash -eu
# Download all Fedora rawhide spec files and verify that every spec using
# %%pyproject macros parses successfully with rpm --specfile.

workdir=$(mktemp -d)
trap 'rm -rf $workdir' EXIT

curl -L https://pkgs.fedoraproject.org/repo/rpm-specs-latest.tar.xz | tar -xJf - -C "$workdir"

specs=$(grep -rl '%pyproject' "$workdir"/rpm-specs/ --include='*.spec' || true)

if [ -z "$specs" ]; then
  echo "ERROR: No specs with %%pyproject found" >&2
  exit 1
fi

nspecs=$(echo "$specs" | wc -l)
echo "Found $nspecs specfiles with %pyproject macros"

# Known-broken spec filenames that should not cause test failure
allowlist=(
  # "broken-package.spec"
)

is_allowed() {
  local base
  base=$(basename "$1")
  for a in "${allowlist[@]}"; do
    if [ "$base" = "$a" ]; then
      return 0
    fi
  done
  return 1
}

total=0
failed=0
failed_specs=()
last_pct=-1

for spec in $specs; do
  total=$((total + 1))
  pct=$((total * 100 / nspecs))
  if [ $pct -ne $last_pct ]; then
    printf '\r%d%% done (%d/%d)' "$pct" "$total" "$nspecs" >&2
    last_pct=$pct
  fi
  if ! output=$(LANG=C.utf-8 rpm --specfile "$spec" 2>&1); then
    if is_allowed "$spec"; then
      printf '\r\033[K' >&2
      echo "SKIP (allowlisted): $spec"
    else
      printf '\r\033[K' >&2
      echo "FAIL: $spec"
      echo "$output" | head -20
      echo "---"
      failed=$((failed + 1))
      failed_specs+=("$spec")
    fi
  fi
done

printf '\r\033[K' >&2
echo "Tested $total specs, $failed failures"

if [ $failed -gt 0 ]; then
  echo ""
  echo "Failed specs:"
  printf '  %s\n' "${failed_specs[@]}"
  exit 1
fi
