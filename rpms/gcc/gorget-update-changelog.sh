#!/bin/sh
# Invoked by metadata/gcc.source-pipeline.yaml's post: step (cwd is this
# package directory). Replicates update-gcc.sh's changelog/PR-extraction
# logic against the real gcc.gnu.org history -- gorget's own fetch: step
# only has the git-archive'd tarball (no .git/), so this does its own
# throwaway clone purely for history access, the same idiom already used
# by jaeger's transform: run: step for its own auxiliary git lookups.
#
# Does NOT touch %global DATE or the fetched archive's name/shape -- both
# are already correct coming out of the fetch: step itself (DATE is baked
# into the pipeline's ${VERSION} at invocation time, computed from the new
# commit's own commit timestamp -- see the comment in
# metadata/gcc.source-pipeline.yaml). This script only updates %global
# gitrev and inserts the new %changelog entry.
#
# Usage: ./gorget-update-changelog.sh <spec-file> <new-gitrev>
set -eu
export LC_ALL=C

if [ "$#" -ne 2 ]; then
  echo "Usage: gorget-update-changelog.sh <spec-file> <new-gitrev>" >&2
  exit 1
fi

spec=$1
new=$2

if [ ! -f "${spec}" ]; then
  echo "gorget-update-changelog.sh: spec file not found: ${spec}" >&2
  exit 1
fi

v=$(sed -n 's/^%global gcc_version //p' "${spec}")
old=$(sed -n 's/^%global gitrev //p' "${spec}")

if [ -z "${v}" ]; then
  echo "gorget-update-changelog.sh: could not extract gcc_version from ${spec}" >&2
  exit 1
fi

if [ -z "${old}" ]; then
  echo "gorget-update-changelog.sh: could not extract gitrev from ${spec}" >&2
  exit 1
fi

if [ "${old}" = "${new}" ]; then
  echo "gorget-update-changelog.sh: gitrev unchanged (${old}), nothing to do"
  exit 0
fi

origdir=$(pwd)
workdir=$(mktemp -d)
trap 'rm -rf "${workdir}"' EXIT

git clone --quiet --filter=blob:none https://gcc.gnu.org/git/gcc.git "${workdir}/gcc-dir"
# gcc.gnu.org has uploadpack.allowReachableSHA1InWant enabled, so fetching
# a bare commit SHA works here. If this fails, ensure the server config
# still allows it (most git hosts, including GitHub, do not by default).
git --git-dir="${workdir}/gcc-dir/.git" fetch --quiet origin "${new}"

cd "${workdir}/gcc-dir"

# P1: the raw diff between old and new, used only as a source of "+PR
# component/number" lines below -- same as update-gcc.sh.
git diff "${old}..${new}" > P1

# P2: commit messages since the last gccadmin@gcc.gnu.org bump commit in
# the old..new range (gcc.gnu.org's own automated "daily bump" commits --
# scanning only after the most recent one avoids re-listing PRs from
# before the last snapshot was taken).
bump_commit=$(git log --format='%ae %H' "${old}..${new}" | awk '/^gccadmin@gcc\.gnu\.org/{print $2; exit 0}')
if [ -n "${bump_commit}" ]; then
  git log --format=%B "${bump_commit}..${new}" > P2
else
  git log --format=%B "${old}..${new}" > P2
fi
# diff exits 1 here whenever P2 is non-empty (the normal case) -- that's
# expected (it's only being used to add a "+" prefix to every line of P2,
# not to test equality), so don't let `set -e` treat it as a failure.
diff -up /dev/null P2 >> P1 || true

# P3: unique "component/number" PR references mentioned in either the diff
# or the commit messages, sorted.
sed -n 's,^+[[:blank:]]\+PR \([a-z0-9+-]\+/[0-9]\+\)$,\1,p' P1 \
  | sed 's/ - .*$//;s/[: ;.]//g' \
  | LC_ALL=C sort -u -t / -k 1,1 -k 2,2n > P3

# P4: only the PRs not already recorded somewhere in the current spec.
: > P4
while read -r pr; do
  [ -n "${pr}" ] || continue
  if grep -F "${pr}" "${origdir}/${spec}" > /dev/null; then
    echo "${pr} already recorded."
  else
    echo "${pr}" >> P4
  fi
done < P3

case "${v}" in
  *.0.*) echo "- update from trunk" > P5 ;;
  *) echo "- update from releases/gcc-$(echo "${v}" | sed 's/\..*$//') branch" > P5 ;;
esac
if [ -s P4 ]; then
  # shellcheck disable=SC2046 disable=SC2005
  echo $(cat P4) | sed 's/ /, /g' | fold -w 71 -s | sed '1s/^/  - PRs /;2,$s/^/\t/;s/, $/,/' >> P5
fi
echo >> P5

cd "${origdir}"

sed -Ei "s/^%global gitrev .*/%global gitrev ${new}/" "${spec}"
sed -i -e "/^%changelog\$/r ${workdir}/gcc-dir/P5" "${spec}"

echo "gorget-update-changelog.sh: updated ${old} -> ${new}"
