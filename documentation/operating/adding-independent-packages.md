---
title: Adding Independent Packages
description: How to add packages that originate in the Hummingbird repository
weight: 45
---

Independent packages are packages that originate in the Hummingbird repository rather than being imported
from Fedora dist-git.

## Steps to Add an Independent Package

### 1. Create Package Directory

```bash
mkdir rpms/<package-name>
cd rpms/<package-name>
```

### 2. Add Package Files

Create the following files in the package directory:

- **`<package-name>.spec`** - RPM spec file
- **`sources`** - SHA512 checksums of source tarballs
- Source tarballs (`.tar.gz`, `.tar.bz2`, `.tar.xz`)
- Any patches or additional configuration files

### 3. Generate the `sources` File

The `sources` file must contain SHA512 checksums in BSD-style format:

```bash
sha512sum --tag oras-1.3.0.tar.gz oras-1.3.0-vendor.tar.bz2 > sources
```

This produces the correct format:

```text
SHA512 (oras-1.3.0.tar.gz) = fb871c0577f621f7e1f56a54f249f96c659b29c771dad529f9a9e838379b2d56bca9cef03a90071915a568e28e266326989e67359e589a87908c4fc077b14689
SHA512 (oras-1.3.0-vendor.tar.bz2) = fa23324bf3910c3dc050eb3c0ebfef3224168489679cf1195d668c656c6c53beaac94b4786e45c2cf3782f9437b0d166da9c18e608a3b474b04957ff1dec5226
```

**Important**: Use `sha512sum --tag` to generate the BSD-style format. Do not use the default GNU
format.

### 4. Create Package Metadata

Create `metadata/<package-name>.json`:

```json
{
  "modification_status": "independent",
  "release": "1",
  "upstream_repo": "https://github.com/example/project",
  "version": "1.3.0"
}
```

Fields:

- **`modification_status`**: Must be `"independent"` for packages not imported from Fedora (see
  [Package Metadata Fields](package-metadata-fields.md))
- **`upstream_repo`**: Canonical upstream git repository URL (required — CI enforces this). If no
  upstream repo exists, use `https://src.fedoraproject.org/rpms/<name>` as a fallback.
- **`version`**: Package version (must match spec file)
- **`release`**: Base release number without dist tag (typically `1` or `0.1`; the `.hum1`
  suffix comes from `%{?dist}` in the spec `Release:` line — see
  [Package Metadata Fields](package-metadata-fields.md))

### 5. Generate Konflux Resources

Generate the Konflux Component and ImageRepository resources:

```bash
make generate-host
```

Or directly:

```bash
python3 ci/generate_resources.py all
```

This updates:

- `konflux-templates/rendered.yml`
- `.tekton/` pipeline files

### 6. Commit Changes

```bash
git add rpms/<package-name>/ metadata/<package-name>.json konflux-templates/ .tekton/
git commit -m "Add <package-name>-<version>-<release>

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

**Note**: Source tarballs (`.tar.gz`, `.tar.bz2`, `.tar.xz`) are automatically ignored by
`.gitignore` and should be uploaded to the lookaside cache instead.

## Example: Adding oras

```bash
# 1. Create directory
mkdir rpms/oras
cd rpms/oras

# 2. Add spec file and sources
# ... create oras.spec ...

# 3. Generate sources file
sha512sum --tag oras-1.3.0.tar.gz oras-1.3.0-vendor.tar.bz2 > sources

# 4. Create metadata
cat > ../../metadata/oras.json << 'EOF'
{
  "modification_status": "independent",
  "release": "1",
  "upstream_repo": "https://github.com/oras-project/oras",
  "version": "1.3.0"
}
EOF

# 5. Generate Konflux resources
make generate-host

# 6. Commit
git add rpms/oras/ metadata/oras.json konflux-templates/ .tekton/
git commit -m "Add oras-1.3.0-1.hum1"
```

## See Also

- [Package Metadata Fields](package-metadata-fields.md) - `modification_status` and `release`
  configuration
- [Package Modification Tracking](package-modification-tracking.md) - Marking packages as modified
  vs clean
- [Rebuilding Packages](rebuilding-packages.md) - Rebuilding existing packages
- [Updating Dist-git Packages](updating-dist-git-packages.md) - Importing/updating from Fedora
