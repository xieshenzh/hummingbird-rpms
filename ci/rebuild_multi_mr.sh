#!/bin/bash
# Creates individual MRs for each rebuild commit ahead of a base branch.
# Intended to be run after 'dist_git.py rebuild' has created commits locally.
#
# Usage:
#   ./ci/rebuild_multi_mr.sh                              # Create MRs for all commits ahead of origin/main
#   ./ci/rebuild_multi_mr.sh --base main                  # Use local main as base instead of origin/main
#   ./ci/rebuild_multi_mr.sh --base abc1234               # Use a specific commit SHA as base
#   ./ci/rebuild_multi_mr.sh --max-updates=5              # Create at most 5 MRs
#   ./ci/rebuild_multi_mr.sh --dry-run                    # Show what would be done, don't push
#
# Environment variables:
#   CHORE_MR_GITLAB_TOKEN    - GitLab API token with write_repository scope (for HTTPS remotes)

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Parse arguments
BASE="origin/main"
MAX_UPDATES=0  # 0 means unlimited
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --base=*)
            BASE="${1#*=}"
            shift
            ;;
        --base)
            BASE="$2"
            shift 2
            ;;
        --max-updates=*)
            MAX_UPDATES="${1#*=}"
            if ! [[ "${MAX_UPDATES}" =~ ^[0-9]+$ ]]; then
                echo "ERROR: --max-updates requires a number (e.g., --max-updates=5)" >&2
                exit 1
            fi
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: $0 [--base REF] [--max-updates=N] [--dry-run]" >&2
            exit 1
            ;;
    esac
done

# Configuration
TARGET_BRANCH=${CI_DEFAULT_BRANCH:-main}

echo "========================================"
echo "Rebuild Multi-MR"
echo "========================================"
echo "Base: ${BASE}"
if [[ "${DRY_RUN}" == true ]]; then
    echo "Mode: DRY-RUN (no MRs will be created)"
else
    echo "Mode: PRODUCTION (MRs will be created)"
fi
if [[ ${MAX_UPDATES} -gt 0 ]]; then
    echo "Max MRs: ${MAX_UPDATES}"
fi
echo ""

# Resolve base to a commit SHA
if ! START_COMMIT=$(git rev-parse "${BASE}" 2>/dev/null); then
    echo "ERROR: Cannot resolve base ref '${BASE}'" >&2
    exit 1
fi
echo "Base commit: ${START_COMMIT:0:8} (${BASE})"

# Collect commits ahead of base (oldest first)
# shellcheck disable=SC2312
mapfile -t COMMIT_SHAS < <(git rev-list --reverse "${START_COMMIT}..HEAD")

if [[ ${#COMMIT_SHAS[@]} -eq 0 ]]; then
    echo "No commits ahead of ${BASE}. Nothing to do."
    exit 0
fi

echo "Found ${#COMMIT_SHAS[@]} commit(s) to process"
echo ""

# Remember where we are so we can restore it at the end
ORIGINAL_BRANCH=$(git rev-parse --abbrev-ref HEAD)

# Statistics
MRS_CREATED=0
MRS_SKIPPED=0
MR_FAILURES=0
COMMITS_SKIPPED=0
FAILED_PACKAGES=()
CREATED_MR_URLS=()

# Process each commit
for COMMIT_SHA in "${COMMIT_SHAS[@]}"; do
    COMMIT_MSG=$(git log -1 --format=%s "${COMMIT_SHA}")

    # Parse package name from "Rebuild {package}: {reason}"
    if [[ ${COMMIT_MSG} =~ ^Rebuild\ ([^\ :]+):\ .* ]]; then
        PACKAGE="${BASH_REMATCH[1]}"
    else
        echo "----------------------------------------"
        echo "⚠ Skipping commit ${COMMIT_SHA:0:8}: cannot parse package name from: ${COMMIT_MSG}"
        COMMITS_SKIPPED=$((COMMITS_SKIPPED + 1))
        continue
    fi

    echo "----------------------------------------"
    echo "Package: ${PACKAGE}"
    echo "  Commit:  ${COMMIT_SHA:0:8}"
    echo "  Message: ${COMMIT_MSG}"

    BRANCH_NAME="chore/rebuild-${PACKAGE}"
    MR_TITLE="chore(rpms): ${COMMIT_MSG}"

    if [[ "${DRY_RUN}" == true ]]; then
        echo "  → [DRY-RUN] Would create MR:"
        echo "      Branch: ${BRANCH_NAME}"
        echo "      Title:  ${MR_TITLE}"
        MRS_CREATED=$((MRS_CREATED + 1))

        # Check max-updates limit
        if [[ ${MAX_UPDATES} -gt 0 && ${MRS_CREATED} -ge ${MAX_UPDATES} ]]; then
            echo ""
            echo "Reached --max-updates=${MAX_UPDATES}. Stopping."
            break
        fi
        continue
    fi

    # Create branch at base and cherry-pick the rebuild commit.
    # Use detached HEAD so we never modify the original branch.
    git branch -D "${BRANCH_NAME}" 2>/dev/null || true

    if ! git checkout --detach --quiet "${START_COMMIT}" 2>/dev/null; then
        echo "  ✗ Failed to checkout base commit"
        MR_FAILURES=$((MR_FAILURES + 1))
        FAILED_PACKAGES+=("${PACKAGE} (checkout failed)")
        continue
    fi

    if ! git checkout -b "${BRANCH_NAME}" 2>/dev/null; then
        echo "  ✗ Failed to create branch ${BRANCH_NAME}"
        MR_FAILURES=$((MR_FAILURES + 1))
        FAILED_PACKAGES+=("${PACKAGE} (branch creation failed)")
        continue
    fi

    if ! git cherry-pick "${COMMIT_SHA}" >/dev/null 2>&1; then
        echo "  ✗ Failed to cherry-pick ${COMMIT_SHA:0:8}"
        MR_FAILURES=$((MR_FAILURES + 1))
        FAILED_PACKAGES+=("${PACKAGE} (cherry-pick failed)")
        git cherry-pick --abort 2>/dev/null || true
        continue
    fi

    # Create MR via create_mr.sh
    echo "  → Creating MR: ${MR_TITLE}"
    MR_EXIT_CODE=0
    ./ci/create_mr.sh --branch "${BRANCH_NAME}" --title "${MR_TITLE}" --auto-merge || MR_EXIT_CODE=$?

    if [[ ${MR_EXIT_CODE} -eq 0 ]]; then
        echo "  ✓ Created MR for ${PACKAGE}"
        MRS_CREATED=$((MRS_CREATED + 1))
        CREATED_MR_URLS+=("https://gitlab.com/redhat/hummingbird/rpms/-/merge_requests?source_branch=${BRANCH_NAME}")
    elif [[ ${MR_EXIT_CODE} -eq 2 ]]; then
        echo "  ⚠ MR already exists for ${PACKAGE} (branch ${BRANCH_NAME} already on remote)"
        MRS_SKIPPED=$((MRS_SKIPPED + 1))
    else
        echo "  ✗ Failed to create MR for ${PACKAGE}"
        MR_FAILURES=$((MR_FAILURES + 1))
        FAILED_PACKAGES+=("${PACKAGE} (MR creation failed)")
    fi

    # Check max-updates limit
    if [[ ${MAX_UPDATES} -gt 0 && $((MRS_CREATED + MRS_SKIPPED)) -ge ${MAX_UPDATES} ]]; then
        echo ""
        echo "Reached --max-updates=${MAX_UPDATES}. Stopping."
        break
    fi
done

# Restore original branch
git checkout --quiet "${ORIGINAL_BRANCH}" 2>/dev/null || true

# Print summary
echo ""
echo "========================================"
echo "Summary"
echo "========================================"
if [[ "${DRY_RUN}" == true ]]; then
    echo "  Mode:        DRY-RUN"
    echo "  Would create: ${MRS_CREATED} MR(s)"
else
    echo "  Mode:        PRODUCTION"
    echo "  MRs created: ${MRS_CREATED}"
    echo "  MRs skipped: ${MRS_SKIPPED} (already exist)"
    echo "  MR failures: ${MR_FAILURES}"
fi
if [[ ${COMMITS_SKIPPED} -gt 0 ]]; then
    echo "  Commits skipped (unparseable): ${COMMITS_SKIPPED}"
fi

if [[ ${#FAILED_PACKAGES[@]} -gt 0 ]]; then
    echo ""
    echo "Failed packages:"
    for failed_pkg in "${FAILED_PACKAGES[@]}"; do
        echo "  - ${failed_pkg}"
    done
fi

if [[ ${#CREATED_MR_URLS[@]} -gt 0 ]]; then
    echo ""
    echo "Created MR URLs:"
    for mr_url in "${CREATED_MR_URLS[@]}"; do
        echo "  - ${mr_url}"
    done
fi
echo "========================================"

if [[ ${MR_FAILURES} -gt 0 ]]; then
    echo ""
    echo "Exiting with error due to ${MR_FAILURES} failure(s)"
    exit 1
fi

echo ""
if [[ "${DRY_RUN}" == true ]]; then
    echo "Dry-run complete."
else
    echo "Done."
fi
exit 0
