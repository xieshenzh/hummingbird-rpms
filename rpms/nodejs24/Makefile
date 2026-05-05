# This is a packager-level Makefile, used for task related to the RPM updates.

# === Paths
# Directory of current Makefile; assumed to be the same as the .spec file.
MAKEDIR  := $(dir $(lastword ${MAKEFILE_LIST}))
SPECFILE ?= $(wildcard ${MAKEDIR}nodejs*.spec)

# === Tools
GIT      := git
RPM      := rpm -D '_sourcedir ${MAKEDIR}'
RPKG     ?= fedpkg
SPECTOOL := rpmdev-spectool --define '_sourcedir ${MAKEDIR}'
TAR      := tar

# === Variables potentially overwritten from environment
# Version we want to update to.
TARGET_VERSION ?= $(error No target version specified for update!)

# === File lists
# Source files that are not stored directly in git
SOURCES  := $(shell ${SPECTOOL} --list-files --source 0,1,2,100 ${SPECFILE}|sed -E 's/^Source[[:digit:]]*: //'|xargs basename -a)
# Packaging support files
PACKAGING := Makefile packaging/make-nodejs-tarball.sh packaging/fill-versions.sh

# === Control targets / actions ===
.PHONY: update clean

# Update the package to TARGET_VERSION
update: ${SPECFILE} packaging/fill-versions.sh
	awk -i inplace '/^Version:/ && !done {sub(/[[:digit:]]+\.[[:digit:]]+\.[[:digit:]]+/, "${TARGET_VERSION}"); done=1} {print}' '${SPECFILE}'
	${MAKE} node-v${TARGET_VERSION}-stripped.tar.gz
	packaging/fill-versions.sh '${SPECFILE}' node-v${TARGET_VERSION}-stripped.tar.gz
	${GIT} add '${SPECFILE}'
	${MAKE} sources

# Remove all downloaded and/or created files
clean:
	$(RM) -r node-v*-stripped.tar.gz node-v*/  # main archive and it's expanded contents
	$(RM) icu4c-*-data-bin-?.zip               # ICU database
	$(RM) packaging-scripts.tar.gz             # packaging tarball

# === Recipes for concrete files ===

# Upload new sources to lookaside cache
sources: ${SOURCES}
	${RPKG} new-sources $^

node-v%-stripped.tar.gz: packaging/make-nodejs-tarball.sh
	$< $*

icu4c-%-data-bin-b.zip icu4c-%-data-bin-l.zip &: ${SPECFILE}
	${SPECTOOL} --get-files --source 1,2 $<

packaging-scripts.tar.gz: ${PACKAGING}
	${TAR} -czf $@ $^
