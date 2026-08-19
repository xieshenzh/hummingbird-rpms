#!/bin/bash

set -euo pipefail
shopt -s inherit_errexit

cd "$(dirname "${BASH_SOURCE[0]}")/.."

TARGET_BRANCH=${CI_COMMIT_BRANCH:-main}

# Standing work item for autonomous MRs (HUM-6140 / HUM-6146).
STANDING_JIRA_NOTE='Jira: [HUM-6146](https://redhat.atlassian.net/browse/HUM-6146)'

# Parse arguments
AUTO_MERGE=
MR_DESCRIPTION=
MARK_AS_DRAFT=
MR_LABELS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --branch)
            BRANCH_NAME="$2"
            shift 2
            ;;
        --title)
            MR_TITLE="$2"
            shift 2
            ;;
        --description)
            MR_DESCRIPTION="$2"
            shift 2
            ;;
        --auto-merge)
            AUTO_MERGE=true
            shift
            ;;
        --draft)
            MARK_AS_DRAFT=true
            shift
            ;;
        --label)
            MR_LABELS+=("$2")
            shift 2
            ;;
        *)
            echo "ERROR: Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# Validate required arguments
if [[ -z "${BRANCH_NAME:-}" ]]; then
    echo "ERROR: --branch is required" >&2
    exit 1
fi

if [[ -z "${MR_TITLE:-}" ]]; then
    echo "ERROR: --title is required" >&2
    exit 1
fi

# Check if there are commits ahead of target branch
# (dist_git.py update already commits changes)
COMMITS_AHEAD=$(git rev-list --count "${TARGET_BRANCH}..HEAD")

if [[ ${COMMITS_AHEAD} -eq 0 ]]; then
    echo "No commits ahead of ${TARGET_BRANCH}"
    exit 0
fi

echo "${COMMITS_AHEAD} commit(s) detected, creating MR..."

# Check if branch already exists on remote
if git ls-remote --exit-code --heads "${REMOTE:-origin}" "${BRANCH_NAME}" >/dev/null 2>&1; then
    echo "Branch ${BRANCH_NAME} already exists on remote, skipping push (MR already exists)"
    echo "  Existing MR: https://gitlab.com/redhat/hummingbird/rpms/-/merge_requests?source_branch=${BRANCH_NAME}"
    exit 2
fi

# Switch to MR branch (create if needed)
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [[ "${CURRENT_BRANCH}" != "${BRANCH_NAME}" ]]; then
    git switch --quiet --create "${BRANCH_NAME}"
fi

if [[ -z "${MR_DESCRIPTION}" ]]; then
    MR_DESCRIPTION="${STANDING_JIRA_NOTE}"
elif [[ "${MR_DESCRIPTION}" != *HUM-6146* ]]; then
    MR_DESCRIPTION="${MR_DESCRIPTION}\\n\\n${STANDING_JIRA_NOTE}"
fi

push_options=(
    --push-option merge_request.create
    --push-option "merge_request.title=${MR_TITLE}"
    --push-option "merge_request.description=${MR_DESCRIPTION}"
    --push-option merge_request.remove_source_branch
)

if [[ -n ${MARK_AS_DRAFT} ]]; then
    push_options+=(--push-option merge_request.draft)
fi

if [[ -n ${AUTO_MERGE} ]]; then
    push_options+=(--push-option merge_request.merge_when_pipeline_succeeds)
fi

for label in "${MR_LABELS[@]}"; do
    push_options+=(--push-option "merge_request.label=${label}")
done

# Capture push output to check if MR was created
echo "Pushing branch with MR creation options..."
set -x
PUSH_EXIT=0
PUSH_OUTPUT=$(git push "${push_options[@]}" "${REMOTE:-origin}" "${BRANCH_NAME}" 2>&1) || PUSH_EXIT=$?
set +x

echo ""
echo "Git push output:"
echo "----------------------------------------"
echo "${PUSH_OUTPUT}"
echo "----------------------------------------"
echo ""

if [[ ${PUSH_EXIT} -ne 0 ]]; then
    echo "ERROR: git push failed" >&2
    exit 1
fi

# Check if the output indicates MR was created (look for actual MR URL with number)
if echo "${PUSH_OUTPUT}" | grep -q "merge_requests/[0-9]"; then
    MR_URL=$(echo "${PUSH_OUTPUT}" | grep -o "https://[^[:space:]]*merge_requests/[0-9]*" | head -1)
    echo "SUCCESS: Created MR at ${MR_URL}"
elif echo "${PUSH_OUTPUT}" | grep -qi "merge.request"; then
    echo "WARNING: Push options sent but MR may already exist or push options not working" >&2
    echo "Check manually at:" >&2
    echo "  https://gitlab.com/redhat/hummingbird/rpms/-/merge_requests?source_branch=${BRANCH_NAME}" >&2
    exit 1
else
    echo "WARNING: Branch pushed but MR was not created" >&2
    echo "You may need to create the MR manually at:" >&2
    echo "  https://gitlab.com/redhat/hummingbird/rpms/-/merge_requests/new?merge_request[source_branch]=${BRANCH_NAME}" >&2
    exit 1
fi
