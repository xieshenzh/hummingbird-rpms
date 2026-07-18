#!/bin/bash

set -e

SWIFT_VERSION="${1:-}"
# Only shorten the version if it contains two dots.
if [[ "${SWIFT_VERSION}" =~ \.\. ]]; then
    SHORT_SWIFT_VERSION="${SWIFT_VERSION%.*}"
else
    SHORT_SWIFT_VERSION="${SWIFT_VERSION}"
fi

if [[ -z "$SWIFT_VERSION" ]]; then
    echo "Usage: $0 <swift-version>"
    echo "Example: $0 6.1.3"
    exit 1
fi

SPEC_FILE="swift-lang.spec"
TAG="swift-${SWIFT_VERSION}-RELEASE"
CONFIG_URL="https://raw.githubusercontent.com/swiftlang/swift/${TAG}/utils/update_checkout/update-checkout-config.json"
TEMP_CONFIG=$(mktemp)

echo "Downloading Swift $SWIFT_VERSION configuration..."
if ! curl -sSL "$CONFIG_URL" -o "$TEMP_CONFIG"; then
    echo "Error: Failed to download configuration for Swift $SWIFT_VERSION"
    echo "URL: $CONFIG_URL"
    rm -f "$TEMP_CONFIG"
    exit 1
fi

if ! jq . "$TEMP_CONFIG" >/dev/null 2>&1; then
    echo "Error: Downloaded file is not valid JSON"
    rm -f "$TEMP_CONFIG"
    exit 1
fi

echo "Parsing repository versions..."

# Collect all the projects included in the desired branch scheme
PROJECTS=$(jq -r --arg scheme "release/${SHORT_SWIFT_VERSION}" '.["branch-schemes"][$scheme]["repos"]|keys[]' "${TEMP_CONFIG}")

# Associate each project with the ref used by the desired branch scheme
declare -A PROJECT_REFS
for PROJECT in ${PROJECTS}; do
    PROJECT_REF=$(jq -r --arg scheme "release/${SHORT_SWIFT_VERSION}" --arg project "$PROJECT" '.["branch-schemes"][$scheme]["repos"][$project]' "${TEMP_CONFIG}")
    PROJECT_REFS["${PROJECT}"]="${PROJECT_REF}"
done

# Remove all existing sources between the lines "Begin forge sources" and "End forge sources"
sed -i "/Begin forge sources/,/End forge sources/{//!d;}" "${SPEC_FILE}"

IDX=1
# Get each project's "ID" (Github repo org/proj URL fragment)
for PROJECT in "${!PROJECT_REFS[@]}"; do
    if [ "${PROJECT}" == "swift" ]; then
        continue
    fi

    PROJECT_REF=${PROJECT_REFS[${PROJECT}]}
    PROJECT_ID=$(jq -r --arg project "${PROJECT}" '.["repos"][$project].remote.id' "${TEMP_CONFIG}")
    FORGEURL="https://github.com/${PROJECT_ID}"

    PADDING="      "
    if [ "${IDX}" -lt 10 ]; then
        PADDING="      "
    fi
    if [ "${IDX}" -ge 100 ]; then
        PADDING="     "
    fi

    if [[ "${PROJECT_REF}" =~ ^(swift/)?release/.* ]]; then
        PROJECT_REF="swift-%{version0}-RELEASE"
    fi

    # Add a new source to the spec file between the lines "Begin forge sources" and "End forge sources"
    sed -i -e "/End forge sources/i %global forgeurl${IDX}  ${FORGEURL}\n%global tag${IDX} ${PADDING}${PROJECT_REF}\n%global subdir${IDX}    ${PROJECT}\n" "${SPEC_FILE}"
    IDX=$((IDX + 1))
done

# Update the version number in the spec file.
echo "Updating version number in the spec file."
sed -i "s|Version: .*|Version: ${SWIFT_VERSION}|" "${SPEC_FILE}"

rm -f "$TEMP_CONFIG"

echo "Done! Please review the changes to ensure everything is correct."
echo "Remember to review the patches and remove any that are no longer needed."
