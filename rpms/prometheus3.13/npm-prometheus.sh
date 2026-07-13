#!/usr/bin/env bash
# Generate Fedora-style bundled Node.js provides from package.json files.

normalize_version() {
    sed 's/^[^0-9]*//' <<<"$1"
}

normalize_name() {
    sed 's|/|-|g' <<<"${1#@}"
}

for package_json in "$@"; do
    name=$(jq -r '.name // empty' "$package_json")
    version=$(jq -r '.version // empty' "$package_json")
    if [[ -n "$name" && -n "$version" ]]; then
        echo "Provides: npm($(normalize_name "$name")) = $version"
    fi

    for section in dependencies optionalDependencies; do
        jq -r ".$section // {} | to_entries[] | \"\(.key) \(.value)\"" "$package_json" |
            while read -r dependency version; do
                echo "Provides: bundled(nodejs-$(normalize_name "$dependency")) = $(normalize_version "$version")"
            done
    done
done | sort -u
