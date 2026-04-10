# RPMs Repository Guidelines

## Operations Index

Common operational tasks that users or AI agents may need to perform:

| Operation                    | Documentation                                                                             | When to use                                                  |
| ---------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Add native package           | [Adding Native Packages](documentation/operating/adding-native-packages.md)               | Add new package not imported from Fedora                     |
| Rebuild package (no-change)  | [Rebuilding Packages](documentation/operating/rebuilding-packages.md)                     | Faulty RPM published; need to bump Release for rebuild       |
| Backport a patch             | [Rebuilding Packages](documentation/operating/rebuilding-packages.md)                     | Fast-track an upstream fix not yet in Fedora                 |
| Debug build failures         | [Debugging Build Failures](documentation/operating/debugging-build-failures.md)           | Investigate and fix failed package builds                    |
| Mark package modified        | [Package Modification Tracking](documentation/operating/package-modification-tracking.md) | Track local changes; prevent automatic Fedora updates        |
| Set package basename         | [Package Modification Tracking](documentation/operating/package-modification-tracking.md) | Set basename/track_version for versioned packages            |
| Track upstream versions      | [Package Modification Tracking](documentation/operating/package-modification-tracking.md) | Enable/disable upstream version checking for a package       |
| List upstream version status | [Package Modification Tracking](documentation/operating/package-modification-tracking.md) | See all packages with upstream version and tracking info     |
| View package differences     | [Package Modification Tracking](documentation/operating/package-modification-tracking.md) | See what changed in modified packages vs Fedora              |
| Add per-package update hooks | [Package Modification Tracking](documentation/operating/package-modification-tracking.md) | Customize spec update, source download, or post-update steps |
| Update dist-git packages     | [Updating Dist-git Packages](documentation/operating/updating-dist-git-packages.md)       | Test or trigger automated package updates from Fedora        |
| Lookaside cache access       | [Lookaside Cache Access](documentation/operating/lookaside-cache-access.md)               | Set up AWS credentials to upload source tarballs             |
| Konflux resource deployment  | [Konflux Resource Deployment](documentation/background/konflux-resource-deployment.md)    | Understand how Konflux resources are deployed                |

## Development Guidelines

### Testing Requirements

When modifying `ci/dist_git.py`:

- **Add tests** for new commands or functionality in `test/test_dist_git.py`
- Run `make check` to verify all tests pass before committing
- Test coverage is tracked - aim to test all user-facing behavior
- Examples: See existing tests like `test_import`, `test_update`, `test_mark_modified_with_reason`

When modifying validation or CI scripts:

- Ensure `make check` passes (includes linting, type checking, and validation)
- Add tests if the change affects user-facing behavior

### Code Style

**Exception Handling:**

- **Never use bare `except Exception:` handlers** - they hide bugs like `AttributeError`,
  `TypeError`, and other programming errors
- Only catch specific exception types that you expect and know how to handle (e.g.,
  `json.JSONDecodeError`, `OSError`, `subprocess.CalledProcessError`)
- Let unexpected errors crash with a traceback - this helps identify bugs during development and
  prevents silent failures in production
- If you need to catch an exception for cleanup or logging, re-raise it afterward so the error isn't
  silently ignored

Bad:

```python
try:
    process_package(pkg)
except Exception:
    logger.warning(f"Error processing {pkg}")
    # Silently continues, hiding the real error!
```

Good:

```python
# Let it crash - we want to see AttributeError, TypeError, etc.
process_package(pkg)

# Or catch only specific expected errors:
try:
    with open(metadata_file) as f:
        data = json.load(f)
except (json.JSONDecodeError, OSError) as e:
    return False, f"Failed to load metadata: {e}"
```
