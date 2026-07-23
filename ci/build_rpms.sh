#! /bin/bash -e

# See usage in help text below.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
RPM_DIR=${SCRIPT_DIR}/../rpms
OUT_DIR=${SCRIPT_DIR}/../builds
REPO_ROOT=${SCRIPT_DIR}/..

show_help() {
    cat << 'EOF'

Build RPMs for a given package using mock in a containerized environment.

This script:
- Calculates build dependencies using mock
- Materializes the hermetic buildroot repository
- Builds the RPM without network access, matching the upstream RPM pipeline
- Outputs binary RPMs to builds/PACKAGE_NAME/RPMS/
- Outputs source RPMs to builds/PACKAGE_NAME/SRPMS/

Usage: ./ci/build_rpms.sh [OPTIONS] PACKAGE_NAME
  PACKAGE_NAME       - Name of the package directory in rpms/
  --arch ARCH        - Target architecture (default: $(uname -m))
  --build-dir DIR    - Custom build directory (default: builds/PACKAGE_NAME)
  --local-rpms-dir DIR - Directory containing local RPMs to use as an additional
                       high-priority repo (useful for testing build compatibility)
  --hermetic         - Match CI: materialize dependencies, then build offline
  --nocheck          - Skip running %check tests during the build
  --shell-before     - Drop into interactive shell instead of running mock
                       (displays the mock command that would be executed)
  --shell-after      - Drop into interactive shell after running mock
  --help, -h         - Show this help message

The built RPMs can be found in: builds/PACKAGE_NAME/RPMS/ and builds/PACKAGE_NAME/SRPMS/

The default is the faster networked Mock build. Use --hermetic to reproduce
Konflux's dependency-calculation and network-disabled build phases exactly.
Interactive shell modes cannot be used with --hermetic.
EOF
}

image=quay.io/redhat-user-workloads/rpm-build-pipeline-tenant/environment:latest@sha256:56bde7a1040650bc14ee927534426a52d88de30d588048c6a08be7a8758372cb
arch=$(uname -m)
build_dir=""
local_rpms_dir=""
hermetic=""
nocheck=""
shell_before=""
shell_after=""
package_name=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --help|-h)
            show_help
            exit 0
            ;;
        --arch)
            arch="$2"
            shift 2
            ;;
        --build-dir)
            build_dir="$2"
            shift 2
            ;;
        --local-rpms-dir)
            local_rpms_dir="$(realpath "$2")"
            shift 2
            ;;
        --hermetic)
            hermetic="1"
            shift
            ;;
        --nocheck)
            nocheck="--nocheck"
            shift
            ;;
        --shell-before)
            shell_before="1"
            shift
            ;;
        --shell-after)
            shell_after="1"
            shift
            ;;
        *)
            if [[ -n "${package_name}" ]]; then
                echo "Error: Multiple package names provided. Only one package can be built at a time." >&2
                echo "" >&2
                show_help
                exit 1
            fi
            package_name="$1"
            shift
            ;;
    esac
done

if [[ -n "${hermetic}" && ( -n "${shell_before}" || -n "${shell_after}" ) ]]; then
    echo "Error: Interactive shell modes cannot be used with --hermetic" >&2
    exit 1
fi

mock_check_args=()
if [[ -n "${nocheck}" ]]; then
    mock_check_args+=(--nocheck)
fi

# Validate exactly one package name was provided
if [[ -z "${package_name}" ]]; then
    echo "Error: No package name provided" >&2
    echo "" >&2
    show_help
    exit 1
fi

# Use builds directory as workdir for easier debugging
if [[ -n "${build_dir}" ]]; then
    workdir="$(realpath -m "${build_dir}")"
else
    workdir="${OUT_DIR}/${package_name}"
fi
mkdir -p "${workdir}"

cd "${workdir}"
mkdir -p results config sources RPMS SRPMS
# CI gives each build fresh source, result, and Mock storage.  Do the same here:
# retaining Mock's root cache without its nested Podman image store makes a
# subsequent dependency-lock calculation fail while inspecting the bootstrap
# image that the cached root caused Mock not to pull.
find results config sources -mindepth 1 -delete
find RPMS SRPMS -mindepth 1 -type f -delete

# Create directory for mock buildroot on host filesystem (avoids overlayfs xattr issues)
mkdir -p var_lib_mock
podman unshare find var_lib_mock -mindepth 1 -delete

# Allow mockbuilder (gid 135/mock) to read config and write results
if command -v setfacl &> /dev/null; then
    podman unshare setfacl -m g:135:rwx -m default:g:135:rwx "results"
    podman unshare setfacl -m g:135:rwx -m default:g:135:rwx "config"
    podman unshare setfacl -m g:135:rwx -m default:g:135:rwx "sources"
    podman unshare setfacl -m g:135:rwx "var_lib_mock"
else
    echo "Error: This script requires setfacl in \$PATH.  Install setfacl via 'dnf -y install acl'"
    exit 1
fi

# Copy local source files to sources directory
cp -f "${RPM_DIR}/${package_name}"/* "${workdir}/sources/" 2>/dev/null || true

# prepare mock config
sed "s|@ARCH@|${arch}|" "${SCRIPT_DIR}/../mock/mock.cfg" > "${workdir}/config/mock.cfg"

# If local RPMs are specified, add a high-priority local repo to the mock config
if [[ -n "${local_rpms_dir}" ]]; then
    # Verify the directory exists and contains RPMs
    if [[ ! -d "${local_rpms_dir}" ]]; then
        echo "Error: Local RPMs directory does not exist: ${local_rpms_dir}" >&2
        exit 1
    fi
    if ! ls "${local_rpms_dir}"/*.rpm &>/dev/null; then
        echo "Warning: No RPM files found in ${local_rpms_dir}" >&2
    fi

    # Inject local repo config into mock.cfg at the anchor point
    # Priority 1 ensures it takes precedence over all other repos
    # Uses /sources/local-rpms so every build phase sees the same repository.
    local_repo_config=$(cat <<'EOF'
[local-rpms]
name=local-rpms
baseurl=file:///sources/local-rpms/
enabled=1
gpgcheck=0
priority=1
EOF
)
    content=$(<"${workdir}/config/mock.cfg")
    printf '%s\n' "${content//# @LOCAL_RPMS_REPO@/${local_repo_config}}" > "${workdir}/config/mock.cfg"
    echo "Local RPMs repo configured from: ${local_rpms_dir}"
fi

# Detect spec file name (there should be exactly one .spec file)
spec_file=$(find "${RPM_DIR}/${package_name}" -maxdepth 1 -name "*.spec" -type f | head -n1 || true)
if [[ -z "${spec_file}" ]]; then
    echo "Error: No .spec file found in rpms/${package_name}/" >&2
    exit 1
fi
spec_file_name=$(basename "${spec_file}")

# Extract upstream package name from spec file name (strip .spec extension)
# This allows the directory name to differ from the actual package name
upstream_package_name="${spec_file_name%.spec}"
if [[ "${upstream_package_name}" != "${package_name}" ]]; then
    echo "Using upstream package name: ${upstream_package_name} (local directory: ${package_name})"
fi

# Check for forked_from override in package-overrides.yaml
forked_from=""
overrides_file="${SCRIPT_DIR}/package-overrides.yaml"
if [[ -f "${overrides_file}" ]]; then
    # Extract forked_from value for this package using grep/sed (avoids yq dependency)
    # Look for package_name: followed by forked_from: on subsequent indented lines
    forked_from=$(awk -v pkg="${package_name}:" '
        $0 ~ "^"pkg"$" { in_pkg=1; next }
        in_pkg && /^[^ ]/ { in_pkg=0 }
        in_pkg && /forked_from:/ { gsub(/.*forked_from:[[:space:]]*["'\'']?/, ""); gsub(/["'\'']?[[:space:]]*$/, ""); print; exit }
    ' "${overrides_file}")
fi

# Determine the upstream repo URL for dist-git-client
if [[ -n "${forked_from}" ]]; then
    upstream_repo_url="${forked_from}/${package_name}.git"
    echo "Using custom lookaside cache: ${forked_from}"
else
    upstream_repo_url="https://src.fedoraproject.org/rpms/${upstream_package_name}.git"
fi

# Detect git directory location (handle worktrees)
if [[ -f "${REPO_ROOT}/.git" ]]; then
    # Git worktree - read the gitdir location (may be relative)
    gitdir=$(grep 'gitdir:' "${REPO_ROOT}/.git" | cut -d' ' -f2) || true
    gitdir="${gitdir:-}"
    # Strip /worktrees/* to get the bare repo root
    bare_repo_relative="${gitdir%/worktrees/*}"
    # Convert to absolute path (resolve relative to REPO_ROOT)
    bare_repo=$(cd "${REPO_ROOT}" && realpath "${bare_repo_relative}")
else
    # Regular git repo
    bare_repo="${REPO_ROOT}/.git"
fi

# Podman arguments shared by dependency calculation and package builds.
podman_args=(
    --rm -ti --privileged --init
    --pids-limit=16384
    -u mockbuilder
    -v "${workdir}/results:/results:z"
    -v "${workdir}/config:/config:z"
    -v "${workdir}/sources:/sources:z"
    -v "${workdir}/var_lib_mock:/var/lib/mock:z"
    -v "${REPO_ROOT}:/repo:z"
    -v "${bare_repo}:/bare:z"
    -e "CURL_HOME=/tmp"
)

# Add local RPMs mount if specified (read-only source, will be copied inside container)
if [[ -n "${local_rpms_dir}" ]]; then
    podman_args+=(-v "${local_rpms_dir}:/local-rpms-src:ro,z")
fi

# Mock command used by the non-hermetic debugging path.
mock_cmd="mock -r /config/mock.cfg \\
     --spec '/repo/rpms/${package_name}/${spec_file_name}' \\
     --sources /sources \\
     --resultdir /results \\
     --no-clean --no-cleanup-after \\
     ${nocheck}"

podman run "${podman_args[@]}" "${image}" bash -euo pipefail -c "
# Configure curl's header until https://github.com/release-engineering/dist-git/issues/88 is fixed
echo 'header = \"Accept-Encoding: identity\"' > /tmp/.curlrc

# Set up dist-git-client config directory with custom lookaside cache configuration
mkdir -p /tmp/dist-git-client-config
cp /repo/mock/dist-git-client.ini /tmp/dist-git-client-config/
export CONFIG_DIR=/tmp/dist-git-client-config

# Create repo from local RPMs if mounted
if [[ -d /local-rpms-src ]]; then
    echo 'Creating repository from local RPMs...'
    mkdir -p /sources/local-rpms
    cp /local-rpms-src/*.rpm /sources/local-rpms/ 2>/dev/null || true
    createrepo_c /sources/local-rpms
fi

# Download sources using dist-git-client
# Copy package to writable location and set up .git for dist-git-client
cp -r /repo/rpms/${package_name} /tmp/package
pushd /tmp/package

# dist-git-client needs a .git directory even with --forked-from (it runs git commands)
cp -r /bare .git

echo 'Downloading sources via dist-git-client...'
# Use --forked-from to tell dist-git-client which lookaside cache to use
dist-git-client --configdir \${CONFIG_DIR} --forked-from ${upstream_repo_url} sources

# Copy all downloaded sources to /sources directory
echo 'Copying sources to /sources directory...'
cp -v * /sources/ 2>/dev/null || true

# The remaining non-hermetic actions intentionally stay in this container.
if [[ -z '${hermetic}' && -n '${shell_before}' ]]; then
    echo 'Installing dnf5 into the mock buildroot...'
    mock -r /config/mock.cfg --resultdir /results --no-clean --install dnf5
    echo ''
    echo '=========================================='
    echo 'Ready to build. The mock command would be:'
    echo ''
    echo '${mock_cmd}'
    echo ''
    echo 'Dropping into mock shell instead...'
    echo '=========================================='
    echo ''
    mock -r /config/mock.cfg --resultdir /results --no-clean --enable-network --shell
elif [[ -z '${hermetic}' && -n '${shell_after}' ]]; then
    ${mock_cmd} || true

    popd
    echo ''
    echo '=========================================='
    echo 'Build complete. Dropping into mock shell.'
    echo '=========================================='
    echo ''
    mock -r /config/mock.cfg --resultdir /results --no-clean --enable-network --shell
elif [[ -z '${hermetic}' ]]; then
    ${mock_cmd}

    popd
fi
"

if [[ -n "${hermetic}" ]]; then
    echo "Calculating build dependencies..."
    podman run "${podman_args[@]}" "${image}" \
        mock -r /config/mock.cfg \
            --spec "/sources/${spec_file_name}" \
            --sources /sources \
            --resultdir /results \
            --calculate-build-dependencies \
            "${mock_check_args[@]}"

    echo "Materializing hermetic buildroot repository..."
    root_podman_args=("${podman_args[@]}")
    for i in "${!root_podman_args[@]}"; do
        if [[ "${root_podman_args[${i}]}" == "-u" ]]; then
            unset 'root_podman_args[i]' 'root_podman_args[i+1]'
            break
        fi
    done
    podman run "${root_podman_args[@]}" "${image}" \
        mock-hermetic-repo \
            --lockfile /results/buildroot_lock.json \
            --output-repo /results/buildroot_repo

    echo "Building without network access..."
    podman run --network=none "${podman_args[@]}" "${image}" \
        mock --hermetic-build \
            /results/buildroot_lock.json \
            /results/buildroot_repo \
            --spec "/sources/${spec_file_name}" \
            --sources /sources \
            --resultdir /results \
            "${mock_check_args[@]}"
fi

# Organize RPMs into separate directories
mkdir -p "${workdir}/RPMS" "${workdir}/SRPMS"
mv -f "${workdir}"/results/*.src.rpm "${workdir}/SRPMS/" 2>/dev/null || true
mv -f "${workdir}"/results/*.rpm "${workdir}/RPMS/" 2>/dev/null || true

# Fix ownership of build outputs (container creates files as mapped uid)
# Inside podman unshare, uid 0 maps to the host user
podman unshare chown -R 0:0 "${workdir}"

echo ""
echo "Binary RPMs:"
ls "${workdir}/RPMS"/*.rpm 2>/dev/null || echo "No binary RPMs found"

echo ""
echo "Source RPMs:"
ls "${workdir}/SRPMS"/*.src.rpm 2>/dev/null || echo "No source RPMs found"

echo ""
echo "Build outputs saved to: ${workdir}/"
