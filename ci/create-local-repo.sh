#!/bin/bash
# Create a local RPM repository from a set of RPM files
# Usage: create-local-repo.sh --engine ENGINE --image IMAGE RPM_FILES...

set -euo pipefail

# Parse arguments
ENGINE=""
IMAGE="quay.io/hummingbird-ci/hummingbird-builder:latest"
RPM_FILES=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --engine)
            ENGINE="$2"
            shift 2
            ;;
        --image)
            IMAGE="$2"
            shift 2
            ;;
        *)
            RPM_FILES+=("$1")
            shift
            ;;
    esac
done

# Validate required arguments
if [[ -z ${ENGINE} ]]; then
    echo "Error: --engine is required" >&2
    exit 1
fi

if [[ ${#RPM_FILES[@]} -eq 0 ]]; then
    echo "Error: At least one RPM file is required" >&2
    exit 1
fi

# Create temporary directory for the repository
repo_dir=$(mktemp -d)

# Copy RPM files to the repository directory
for rpm_file in "${RPM_FILES[@]}"; do
    if [[ ! -f ${rpm_file} ]]; then
        echo "Error: RPM file not found: ${rpm_file}" >&2
        rm -rf "${repo_dir}"
        exit 1
    fi
    cp "${rpm_file}" "${repo_dir}/"
done

# Run createrepo_c in a container to generate repository metadata
"${ENGINE}" run --rm --user 0 -e HOME=/root -v "${repo_dir}:/repo:z" "${IMAGE}" \
    createrepo_c /repo >&2

# Output the repository directory path (this is captured by the caller)
echo "${repo_dir}"
