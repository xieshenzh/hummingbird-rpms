---
title: Debugging Build Failures
weight: 55
---

## Overview

When `./ci/build_rpms.sh` fails to build a package, you can use the `--shell-after` flag to drop
into an interactive debugging environment. This preserves the build state and allows you to
investigate failures, modify files, and re-run the build without starting over.

## Quick Start

```bash
# Run build and drop into shell after completion (even on failure)
./ci/build_rpms.sh --shell-after <package>
```

After the build completes (success or failure), you'll be dropped into a mock shell inside the build
environment.

## Understanding the Build Environment

The build happens in two layers:

1. **Outer container**: The rpm-build-pipeline podman container (Red Hat build environment)
2. **Mock shell**: An isolated RPM build environment inside the container (similar to a chroot)

When you use `--shell-after`, you're placed directly in the **mock shell**.

### Key directories in the outer container

- `/results/build.log` - Full build output with all commands that were run
- `/sources/` - Package source files (spec, patches, etc.)
- `/config/` - Mock configuration
- `/repo/` - Your local rpms repository (read-only mount)

### Key directories in the mock shell

- `/builddir/build/BUILD/<package>-*/` - Unpacked source tree (modify and re-run build/test commands
  here)
- `/builddir/build/originals/<package>.spec` - The spec file (modify to test spec changes)
- `/builddir/build/SOURCES/` - Source tarballs and patches
- `/builddir/build/BUILDROOT/` - Install root (where %install places files)

## Debugging Workflow

### 1. Connect to the outer container

To get a second shell without killing your existing mock shell session, connect to the outer
container:

```bash
# In another terminal, find the running container
podman ps | grep rpm-build-pipeline

# Get a shell in the outer container
podman exec -it <container-id> bash
```

### 2. Examine the build failure

From the outer container, check the build log to understand what failed:

```bash
# View the end of the build log
tail -100 /results/build.log

# Search for specific errors
grep -i error /results/build.log
```

### 3. Enter the mock shell

To investigate or modify the build environment, you can get an additional shell in the mock chroot,
particularly for agents which cannot use the initial interactive `--shell-after` from above.

```bash
# From the outer container, chroot into the mock build environment
chroot /var/lib/mock/local-x86_64/root /bin/bash
```

### 4. Iterate on the fix

The recommended approach for debugging is to extract and re-run specific commands directly:

#### Fast iteration: Extract and re-run specific commands (recommended)

For quick iteration while developing a fix, extract the exact build or test command from the build
log and run it directly:

```bash
# From the outer container, find what command the failing phase runs
grep -A 30 'Executing(%check)' /results/build.log

# Look for lines starting with "+ " that show the actual commands
# Example output:
#   + cd /builddir/build/BUILD/dnf5-5.4.0.0-build/dnf5-5.4.0.0
#   + /usr/bin/ctest --test-dir redhat-linux-build --output-on-failure ...

# Run that command in the mock chroot
podman exec -u root <container-id> chroot /var/lib/mock/local-x86_64/root \
  su mockbuild -c "cd /builddir/build/BUILD/<package>-*/... && <actual-command>"
```

**For build failures during %build:** Look for `Executing(%build)` in the log, find commands like
`make` or `ninja`, then re-run them:

```bash
# Example: extract the build command
grep -A 30 'Executing(%build)' /results/build.log | grep -E '^\+ (make|ninja|cmake)'

# Re-run in the build directory
podman exec -u root <container-id> chroot /var/lib/mock/local-x86_64/root \
  su mockbuild -c "cd /builddir/build/BUILD/<package>-*/<build-subdir> && make -j14"
```

**For test failures during %check:** Look for `Executing(%check)` and extract the test command:

```bash
# Example: extract the test command
grep -A 30 'Executing(%check)' /results/build.log | grep -E '^\+ (ctest|make check|pytest)'

# Re-run tests
podman exec -u root <container-id> chroot /var/lib/mock/local-x86_64/root \
  su mockbuild -c "cd /builddir/build/BUILD/<package>-*/... && ctest --output-on-failure"
```

This approach is fast because it skips rpmbuild overhead and goes straight to the failing command.
It works with the preserved BUILD directory from `--shell-after` and is ideal when you're modifying
source files and want quick feedback.

**Modifying source files:** Edit files directly in `/builddir/build/BUILD/<package>-*/` and re-run
the command to test your changes quickly.

**Modifying the spec file:** You can also test spec file changes by editing
`/builddir/build/originals/<package>.spec` in the mock chroot:

```bash
# From the host, edit the spec file in the mock chroot
podman exec -it -u root <container-id> chroot /var/lib/mock/local-x86_64/root \
  vim /builddir/build/originals/<package>.spec

# Run rpmbuild to see the change
podman exec -u root <container-id> chroot /var/lib/mock/local-x86_64/root \
  env HOME=/builddir LANG=C.UTF-8 su mockbuild -c \
  'rpmbuild -bb --target x86_64 --nodeps /builddir/build/originals/<package>.spec'
```

#### Full validation: Apply fixes and rebuild with build_rpms.sh

Manual `rpmbuild` in the preserved environment may not work reliably for all packages due to complex
build dependencies and environment requirements.

Once you've identified a fix using the fast iteration approach:

1. **Exit the debug environment** and apply your fix to the actual source files or spec file in
   `rpms/<package>/`
2. **Test the complete build** using the build script:

   ```bash
   ./ci/build_rpms.sh <package>
   ```

This ensures your fix works through the entire build process in a clean environment.

## Related Operations

- [Rebuilding Packages](rebuilding-packages.md) - Bump release or backport patches
- [Package Modification Tracking](package-modification-tracking.md) - Mark packages as modified
