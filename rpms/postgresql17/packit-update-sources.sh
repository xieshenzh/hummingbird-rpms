 git clone --depth 1 --branch master https://github.com/postgres/postgres.git
 pushd postgres
 git fetch --tags
 NEW_MINOR=$(git tag -l "REL_16_*" | sed -E "s/REL_16_([0-9]+)/\1/" | sort -n | tail -n 1)
 popd
 pushd $PACKIT_DOWNSTREAM_REPO
 sed -i "s/\(%global prevversion %{prevmajorversion}\)\.[0-9]\+/\1.$NEW_MINOR/" $PACKIT_DOWNSTREAM_PACKAGE_NAME.spec
 popd
