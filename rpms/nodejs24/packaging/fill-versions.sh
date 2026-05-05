#!/bin/bash
set -e

usage() {
    echo "$(basename "$0")" '<SPEC>' '<TARBALL>'
    echo Fill versions between BEGIN/END automatic-version-macros in the spec file.
}

# Format version information in unified style
# usage: format_version <component> <source file> <version> [macro options…]
format_version() {
    local -r component="${1?No component named!}" && shift
    local -r srcpath="${1?No version source specified!}" && shift
    local -r version="${1?No version specified}" && shift

    printf '# Version from %s\n' "${srcpath}"
    printf '%%nodejs_define_version %s %s%s%s\n' "${component}" "${version}" "${*:+ }" "${*}"
}

# Find concrete path to the requested file within unpacked source tree.
# usage: find_source_path <path_tail>
find_source_path() {
    local -r path_tail="${1?No source path specified!}"

    # currently just verify that the expected path exists
    if test -r node-v*/"${path_tail}"
    then
        printf '%s\n' "${_}"
    else
        printf >&2 'Path does not exist: %s\n' "${_}"
        return 1
    fi
}

# "Meta"-extraction function.
# Finds the actual source file, extracts the version as specified,
# and outputs properly formatted macro on stdout.
# usage: find_version <component> <source_path> <macro_opts> <extractor_fn> [extractor_fn_opts]…
# arguments:
#   component: name of the vendored component
#   source_path: path to the version information source, relative to the root of the extracted tree
#   macro_opts: options for %nodejs_define_version macro; use empty string ('') if none shall be used
#   extractor_fn: name of the function responsible for extraction of the version string.
#       expected signature: extractor_fn <source_path> [options]…
#   extractor_fn_opts: additional options to pass to extractor_fn after source_path
find_version() {
    local -r component="${1?No component specified!}" && shift
    local -r srcpath="${1?No version source path specified!}" && shift
    local -r macro_opts="${1?No %nodejs_define_version macro options specified!}" && shift
    local -r extract_fn="${1?No extraction function specified!}" && shift

    local full_source_path version_string
    full_source_path="$(find_source_path "${srcpath}")"
    version_string="$("${extract_fn}" "${full_source_path}" "$@")"
    format_version "${component}" "${full_source_path}" "${version_string}" "${macro_opts}"
}

# The extraction functions (extractor_fn above) follows. Add to them as necessary.

# Extract version string from a C #define directive.
# If provided more than one define name, it is assumed these define the version components,
# and they will all be concatenated with a dot.
# usage: version_from_c_define <header> <define_name>…
version_from_c_define() {
    local -r path="${1?No C header provided!}" && shift

    while test "$#" -gt 0; do
        local name="${1}"; shift
        # grab either a single numeric component (\d+)
        # or a single quoted string (".*?")
        # after a #define <name> statement
        local regexp="(?<=#define ${name})\s+(\d+|\".*?\")"

        grep --only-matching --perl-regexp "${regexp}" "${path}" \
        | sed -E 's|^[[:space:]"]*||;s|"$||' \
        | if test "$#" -gt 0  # not the last part
            then tr '\n' '.'  # turn line end into separator dot
            else cat          # leave as is
        fi
    done
}

# Extract version from package.json or similar file
# usage: version_from_json <path> [jq_filter]
# jq_filter defaults to .version
version_from_json() {
    local -r path="${1?No JSON path provided!}" && shift
    local -r filter="${1-.version}" && shift

    jq --raw-output "${filter}" "${path}"
}

# There always is a special case. Don't be afraid to use one-off extractor functions.
parse_isu_version() {
    local -r path="${1?No ICU metadata JSON path provided!}"

    version_from_json "${path}" '.[0].url' \
    | sed -E 's/.*release-([[:digit:]]+).([[:digit:]]+).*/\1.\2/g'
}
parse_punycode_version() {
    local -r path="${1?No punycode.js path provided!}"

    # Fragile, but I could come up with nothing better yet.
    grep --only-matching --perl-regexp \
        "(?<='version': )'\d+\.\d+\.\d+'" "${path}" \
    | tr -d "'"
}
parse_npm_version() {
    # NPM was originally a separate package using epoch; we need to keep it
    local -r NPM_EPOCH=1
    # NPM is a subpackage with it's own version. Use always-increasing release macro
    local -r NPM_RELEASE='%{nodejs_subpackage_release}'

    local -r path="${1?No path to npm package.json provided!}"

    printf '%d:%s-%s\n' "${NPM_EPOCH}" "$(version_from_json "${path}")" "${NPM_RELEASE}"
}
parse_v8_version() {
    # v8 was originally a separate package using epoch; we need to keep it
    local -r V8_EPOCH=3
    # v8 is a subpackage with it's own version. Use always-increasing release macro
    local -r V8_RELEASE='%{nodejs_subpackage_release}'

    local -r path="${1?No path to v8 version header provided!}"

    printf '%d:%s-%s\n' \
        "${V8_EPOCH}" \
        "$(version_from_c_define "${path}" V8_{MAJOR,MINOR}_VERSION V8_BUILD_NUMBER V8_PATCH_LEVEL)" \
        "${V8_RELEASE}"
}

# Main script
# ===========

readonly SPEC="$1" TARBALL="$2"
if test -z "${SPEC}"; then echo 'Missing SPEC path!' >&2; usage >&2; exit 1; fi
if test -z "${TARBALL}"; then echo 'Missing TARBALL path!' >&2; usage >&2; exit 1; fi
readonly NEW_SPEC="${SPEC}.new"

# Start with a clean source tree
rm -rf node-v*/ && tar -xzf "${TARBALL}"

# Redirect standard output to working copy of the current spec file
exec 3>&1 1>"${NEW_SPEC}"

# Copy SPEC file up to the BEGIN marker
sed -E '/^#\s*BEGIN automatic-version-macros/q' "${SPEC}"

# Output libnode.so soname version
soversion_source="$(find_source_path src/node_version.h)"
printf '# Version from %s\n' "${soversion_source}"
printf '%%global node_soversion %s\n' "$(version_from_c_define "${soversion_source}" NODE_MODULE_VERSION)"

echo

# Output all the dependency versions. Try to keep them in alphabetical order

find_version ada deps/ada/ada.h '' version_from_c_define ADA_VERSION
find_version brotli deps/brotli/c/common/version.h '' version_from_c_define BROTLI_VERSION_{MAJOR,MINOR,PATCH}
find_version c_ares deps/cares/include/ares_version.h '' version_from_c_define ARES_VERSION_STR
find_version histogram deps/histogram/include/hdr/hdr_histogram_version.h '' version_from_c_define HDR_HISTOGRAM_VERSION
find_version icu tools/icu/current_ver.dep -p parse_isu_version
find_version libuv deps/uv/include/uv/version.h '' version_from_c_define UV_VERSION_{MAJOR,MINOR,PATCH}
find_version llhttp deps/llhttp/include/llhttp.h '' version_from_c_define LLHTTP_VERSION_{MAJOR,MINOR,PATCH}
find_version merve deps/merve/merve.h '' version_from_c_define MERVE_VERSION
find_version nghttp2 deps/nghttp2/lib/includes/nghttp2/nghttp2ver.h '' version_from_c_define NGHTTP2_VERSION
find_version nghttp3 deps/ngtcp2/nghttp3/lib/includes/nghttp3/version.h '' version_from_c_define NGHTTP3_VERSION
find_version ngtcp2 deps/ngtcp2/ngtcp2/lib/includes/ngtcp2/version.h '' version_from_c_define NGTCP2_VERSION
find_version nodejs-minimatch deps/minimatch/package.json '' version_from_json
find_version nodejs-punycode lib/punycode.js '' parse_punycode_version
find_version nodejs-undici deps/undici/src/package.json '' version_from_json
find_version npm deps/npm/package.json '' parse_npm_version
find_version sqlite deps/sqlite/sqlite3.h '' version_from_c_define SQLITE_VERSION
find_version uvwasi deps/uvwasi/include/uvwasi.h '' version_from_c_define UVWASI_VERSION_{MAJOR,MINOR,PATCH}
find_version v8 deps/v8/include/v8-version.h -p parse_v8_version
find_version zlib deps/zlib/zlib.h '' version_from_c_define ZLIB_VERSION

# Copy rest of the spec file from the END marker till the end
sed -nE '/^#\s*END automatic-version-macros/,$p' "${SPEC}"

# Restore standard output
exec 1>&3 3>&-

# Replace the old spec with the updated one
mv "${NEW_SPEC}" "${SPEC}"
