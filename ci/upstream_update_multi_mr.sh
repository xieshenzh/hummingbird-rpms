#!/bin/bash
# Creates individual MRs for each package that needs an upstream version update
# Uses check_upstream_versions.py to detect newer upstream releases and update
# spec files, then creates one MR per package for independent review.
#
# Usage:
#   ./ci/upstream_update_multi_mr.sh                              # Normal mode: check all tracked packages, create MRs
#   ./ci/upstream_update_multi_mr.sh --clone                      # Clone to /tmp, check all tracked packages, dry-run (no MRs)
#   ./ci/upstream_update_multi_mr.sh --clone --create-mrs         # Clone to /tmp, create MRs
#   ./ci/upstream_update_multi_mr.sh --clone --max-updates=3 --create-mrs  # Clone, create up to 3 MRs
#   ./ci/upstream_update_multi_mr.sh --max-updates=5              # Check all, create up to 5 MRs (in current repo)
#   ./ci/upstream_update_multi_mr.sh --only-package=curl          # Check only curl
#
# Environment variables:
#   CHORE_MR_GITLAB_TOKEN    - GitLab API token with write_repository scope (required for --create-mrs)
#   GITLAB_REMOTE_URL        - GitLab repo URL (default: https://gitlab.com/redhat/hummingbird/rpms.git)

set -euo pipefail

# Parse arguments
CLONE_MODE=false
CREATE_MRS=false
MAX_UPDATES=0   # 0 means unlimited
ONLY_PACKAGE=""  # Empty means check all tracked packages
TEMP_DIR=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --clone)
            CLONE_MODE=true
            shift
            ;;
        --max-updates=*)
            MAX_UPDATES="${1#*=}"
            if ! [[ "${MAX_UPDATES}" =~ ^[0-9]+$ ]]; then
                echo "Error: --max-updates requires a number (e.g., --max-updates=10)"
                exit 1
            fi
            shift
            ;;
        --create-mrs)
            CREATE_MRS=true
            shift
            ;;
        --only-package=*)
            ONLY_PACKAGE="${1#*=}"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--clone] [--max-updates=N] [--create-mrs] [--only-package=NAME]"
            exit 1
            ;;
    esac
done

# Configuration
TARGET_BRANCH=${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME:-${CI_COMMIT_BRANCH:-${CI_DEFAULT_BRANCH:-main}}}

# Cleanup function for clone mode
# shellcheck disable=SC2329  # Function is invoked via EXIT trap
cleanup() {
    if [[ -n "${TEMP_DIR}" && -d "${TEMP_DIR}" ]]; then
        echo ""
        echo "Temporary directory preserved at: ${TEMP_DIR}"
        echo "You can inspect it or delete it manually when done."
    fi
}

# ── Phase 1: Setup ──────────────────────────────────────────────────

if [[ "${CLONE_MODE}" == true ]]; then
    trap cleanup EXIT

    TEMP_DIR=$(mktemp -d /tmp/upstream-update-clone.XXXXXX)
    echo "========================================"
    echo "CLONE MODE - Working in temporary directory"
    echo "========================================"
    echo "Temp dir: ${TEMP_DIR}"
    echo ""

    # Use configurable remote URL, defaulting to the upstream canonical repo
    GITLAB_REMOTE_URL="${GITLAB_REMOTE_URL:-https://gitlab.com/redhat/hummingbird/rpms.git}"

    # Extract host and project path for constructing MR URLs later
    if [[ "${GITLAB_REMOTE_URL}" =~ ^https://([^/]+)/(.+)\.git$ ]]; then
        GITLAB_HOST="${BASH_REMATCH[1]}"
        GITLAB_PROJECT="${BASH_REMATCH[2]}"
    elif [[ "${GITLAB_REMOTE_URL}" =~ ^https://([^/]+)/(.+)$ ]]; then
        GITLAB_HOST="${BASH_REMATCH[1]}"
        GITLAB_PROJECT="${BASH_REMATCH[2]%.git}"
    fi

    echo "Cloning repository from GitLab..."
    git clone --quiet --depth 1 --single-branch --branch "${TARGET_BRANCH}" "${GITLAB_REMOTE_URL}" "${TEMP_DIR}"
    cd "${TEMP_DIR}"

    # Configure git for the cloned repo
    git config user.name "Upstream Update (Clone Mode)"
    git config user.email "noreply@example.com"

    # If --create-mrs is set, configure credentials for pushing
    if [[ "${CREATE_MRS}" == true ]]; then
        if [[ -z "${CHORE_MR_GITLAB_TOKEN:-}" ]]; then
            echo "ERROR: CHORE_MR_GITLAB_TOKEN must be set when using --create-mrs"
            echo "       Token must have write_repository scope"
            exit 1
        fi

        git config credential.username ignored
        git config credential.helper "!echo \"password=\$CHORE_MR_GITLAB_TOKEN\"; :"

        echo "Configured GitLab authentication for pushing"
    fi

    echo "Repository cloned from: ${GITLAB_REMOTE_URL}"
    echo "Working directory: ${TEMP_DIR}"
    echo ""
else
    cd "$(dirname "${BASH_SOURCE[0]}")/.."

    # In production mode, extract GitLab info for MR URL tracking
    GITLAB_REMOTE_URL="${GITLAB_REMOTE_URL:-https://gitlab.com/redhat/hummingbird/rpms.git}"
    if [[ "${GITLAB_REMOTE_URL}" =~ ^https://([^/]+)/(.+)\.git$ ]]; then
        GITLAB_HOST="${BASH_REMATCH[1]}"
        GITLAB_PROJECT="${BASH_REMATCH[2]}"
    elif [[ "${GITLAB_REMOTE_URL}" =~ ^https://([^/]+)/(.+)$ ]]; then
        GITLAB_HOST="${BASH_REMATCH[1]}"
        GITLAB_PROJECT="${BASH_REMATCH[2]%.git}"
    fi
fi

# Ensure git is configured (for both clone and production mode)
if [[ -z "$(git config user.name 2>/dev/null || true)" ]]; then
    if [[ -n "${GITLAB_USER_NAME:-}" ]]; then
        git config user.name "${GITLAB_USER_NAME}"
    elif [[ "${CLONE_MODE}" == false ]]; then
        echo "Warning: git user.name not configured. Commits may fail."
    fi
fi

if [[ -z "$(git config user.email 2>/dev/null || true)" ]]; then
    if [[ -n "${GITLAB_USER_EMAIL:-}" ]]; then
        git config user.email "${GITLAB_USER_EMAIL}"
    elif [[ "${CLONE_MODE}" == false ]]; then
        echo "Warning: git user.email not configured. Commits may fail."
    fi
fi

# Statistics tracking
PACKAGES_UPDATED=0
MR_FAILURES=0
EXISTING_UPDATES=0
FAILED_PACKAGES=()
CREATED_MR_URLS=()

echo "========================================"
echo "Upstream Version Multi-MR Update"
echo "========================================"
if [[ "${CLONE_MODE}" == true ]]; then
    echo "Mode: CLONE"
    if [[ "${CREATE_MRS}" == true ]]; then
        echo "MRs: Will be created and pushed to GitLab"
    else
        echo "MRs: Dry-run (no MRs will be created)"
    fi
else
    echo "Mode: PRODUCTION (MRs will be created)"
fi
echo "Target branch: ${TARGET_BRANCH}"
if [[ -n "${ONLY_PACKAGE}" ]]; then
    echo "Package filter: ${ONLY_PACKAGE}"
fi
if [[ ${MAX_UPDATES} -gt 0 ]]; then
    echo "Max updates: ${MAX_UPDATES}"
fi
echo ""

# Merge request pipelines already have the source commit checked out. Keep that
# checkout so manual jobs exercise the code under review rather than switching
# back to the default branch.
if [[ -z "${CI_MERGE_REQUEST_IID:-}" ]]; then
    git checkout --quiet "${TARGET_BRANCH}" 2>/dev/null || true
fi

# Save the current commit before running updates
START_COMMIT=$(git rev-parse HEAD)

# ── Phase 2: Run upstream version check ─────────────────────────────

echo "Running check_upstream_versions.py check --update --sign-off ..."
echo ""

CHECK_ARGS=(./ci/check_upstream_versions.py check --update --sign-off)

if [[ -n "${ONLY_PACKAGE}" ]]; then
    CHECK_ARGS+=("${ONLY_PACKAGE}")
fi

CHECK_EXIT_CODE=0
"${CHECK_ARGS[@]}" 2>&1 || CHECK_EXIT_CODE=$?

echo ""

# Exit codes from check_upstream_versions.py:
#   0 = no updates available
#   1 = updates found and applied
#   2 = errors occurred
if [[ ${CHECK_EXIT_CODE} -eq 0 ]]; then
    echo "No upstream version updates available. All tracked packages are up-to-date."
    exit 0
elif [[ ${CHECK_EXIT_CODE} -eq 2 ]]; then
    echo "Warning: check_upstream_versions.py reported errors (exit code 2)"
    echo "Continuing to process any commits that were created..."
    echo ""
fi

# ── Phase 3: Collect commits, create MRs ────────────────────────────

# Check how many commits were created since we started
COMMITS_CREATED=$(git rev-list --count "${START_COMMIT}..HEAD" 2>/dev/null || echo "0")

if [[ ${COMMITS_CREATED} -eq 0 ]]; then
    echo "No commits were created despite updates being reported."
    echo "This may indicate all updates failed."
    exit 1
fi

echo "Found ${COMMITS_CREATED} upstream update commit(s)"
echo ""

# Get list of commit SHAs (oldest first, so we process in chronological order)
# shellcheck disable=SC2312  # Command substitution in mapfile is intentional
mapfile -t COMMIT_SHAS < <(git rev-list --reverse "${START_COMMIT}..HEAD")

# Apply --max-updates limit by truncating the commit array
if [[ ${MAX_UPDATES} -gt 0 && ${#COMMIT_SHAS[@]} -gt ${MAX_UPDATES} ]]; then
    echo "Limiting to ${MAX_UPDATES} updates (--max-updates=${MAX_UPDATES})"
    COMMIT_SHAS=("${COMMIT_SHAS[@]:0:${MAX_UPDATES}}")
    echo ""
fi

# Reset main back to START_COMMIT so MR branches will have commits ahead
# (The commits are preserved in COMMIT_SHAS array)
git reset --hard "${START_COMMIT}" --quiet

# Process each commit to create individual MRs
for COMMIT_SHA in "${COMMIT_SHAS[@]}"; do
    # Get commit message first line and extract package name + version
    COMMIT_MSG=$(git log -1 --format=%s "${COMMIT_SHA}")

    # Extract package name and version from commit message
    # Format: "Update <package> to <version>"
    if [[ ${COMMIT_MSG} =~ ^Update\ ([^\ ]+)\ to\ (.+)$ ]]; then
        PACKAGE="${BASH_REMATCH[1]}"
        VERSION="${BASH_REMATCH[2]}"
    else
        echo "----------------------------------------"
        echo "Skipping commit ${COMMIT_SHA:0:8}: cannot parse from: ${COMMIT_MSG}"
        continue
    fi

    echo "----------------------------------------"
    echo "Processing: ${PACKAGE}"
    echo "  Commit: ${COMMIT_SHA:0:8}"
    echo "  Message: ${COMMIT_MSG}"

    # Create package-specific branch
    BRANCH_NAME="chore/upstream-update-${PACKAGE}"
    MR_TITLE="chore(rpms): Update ${PACKAGE} to ${VERSION}"

    # Create a new branch from START_COMMIT and cherry-pick this commit
    if ! git checkout --quiet "${START_COMMIT}"; then
        echo "  Failed to checkout starting commit"
        MR_FAILURES=$((MR_FAILURES + 1))
        FAILED_PACKAGES+=("${PACKAGE} (checkout failed)")
        continue
    fi

    # Delete branch if it exists, then create fresh
    git branch -D "${BRANCH_NAME}" 2>/dev/null || true

    if ! git checkout -b "${BRANCH_NAME}" 2>/dev/null; then
        echo "  Failed to create branch ${BRANCH_NAME}"
        MR_FAILURES=$((MR_FAILURES + 1))
        FAILED_PACKAGES+=("${PACKAGE} (branch creation failed)")
        continue
    fi

    # Cherry-pick the specific commit
    if ! git cherry-pick "${COMMIT_SHA}" >/dev/null 2>&1; then
        echo "  Failed to cherry-pick commit for ${PACKAGE}"
        MR_FAILURES=$((MR_FAILURES + 1))
        FAILED_PACKAGES+=("${PACKAGE} (cherry-pick failed)")
        git cherry-pick --abort 2>/dev/null || true
        continue
    fi

    if [[ "${CLONE_MODE}" == true && "${CREATE_MRS}" == false ]]; then
        # Clone mode without --create-mrs: dry-run
        echo "  Would create MR:"
        echo "      Branch: ${BRANCH_NAME}"
        echo "      Title: ${MR_TITLE}"
        echo "  [DRY-RUN] MR would be created for ${PACKAGE}"
        PACKAGES_UPDATED=$((PACKAGES_UPDATED + 1))
    else
        # Production mode or clone mode with --create-mrs: actually create the MR
        echo "  Creating MR: ${MR_TITLE}"

        MR_ARGS=(--branch "${BRANCH_NAME}" --title "${MR_TITLE}" --auto-merge)

        # Create MR using existing create_mr.sh
        # Exit codes: 0 = created, 2 = already exists, 1 = failure
        MR_EXIT_CODE=0
        ./ci/create_mr.sh "${MR_ARGS[@]}" || MR_EXIT_CODE=$?

        if [[ ${MR_EXIT_CODE} -eq 0 ]]; then
            echo "  Created MR for ${PACKAGE}"
            PACKAGES_UPDATED=$((PACKAGES_UPDATED + 1))

            # Track MR URL if we have GitLab info
            if [[ -n "${GITLAB_HOST:-}" && -n "${GITLAB_PROJECT:-}" ]]; then
                MR_URL="https://${GITLAB_HOST}/${GITLAB_PROJECT}/-/merge_requests?source_branch=${BRANCH_NAME}"
                CREATED_MR_URLS+=("${MR_URL}")
            fi
        elif [[ ${MR_EXIT_CODE} -eq 2 ]]; then
            # MR already exists
            EXISTING_UPDATES=$((EXISTING_UPDATES + 1))
        else
            echo "  Failed to create MR for ${PACKAGE}"
            MR_FAILURES=$((MR_FAILURES + 1))
            FAILED_PACKAGES+=("${PACKAGE} (MR creation failed)")
        fi
    fi
done

# Return to TARGET_BRANCH
git checkout --quiet "${TARGET_BRANCH}" 2>/dev/null || true

# ── Phase 4: Summary ───────────────────────────────────────────────

echo ""
echo "========================================"
echo "Summary"
echo "========================================"
if [[ "${CLONE_MODE}" == true ]]; then
    echo "  Mode: CLONE"
    if [[ "${CREATE_MRS}" == true ]]; then
        echo "  MRs: ${PACKAGES_UPDATED} created"
    else
        echo "  MRs: Dry-run (no MRs created)"
    fi
else
    echo "  Mode: PRODUCTION"
    echo "  MRs: ${PACKAGES_UPDATED} created"
fi
echo "  Commits found:           ${COMMITS_CREATED}"
echo "  Commits processed:       ${#COMMIT_SHAS[@]}"
echo "  Existing updates:        ${EXISTING_UPDATES}"
echo "  MR failures:             ${MR_FAILURES}"

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

# Exit with error if any failures occurred
if [[ ${MR_FAILURES} -gt 0 ]]; then
    echo ""
    echo "Exiting with error due to ${MR_FAILURES} failure(s)"
    exit 1
fi

echo ""
if [[ "${CLONE_MODE}" == true ]]; then
    if [[ "${CREATE_MRS}" == true ]]; then
        echo "Clone mode with MR creation completed successfully!"
    else
        echo "Clone mode (dry-run) completed successfully!"
    fi
else
    echo "All upstream updates processed successfully!"
fi
exit 0
