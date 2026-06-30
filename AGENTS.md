# RPMs Repository Guidelines

## `ci/dist_git.py` — The Package Management Tool

**All package operations must go through `ci/dist_git.py`.** Do not manually edit spec
files, metadata JSON, or Konflux/Tekton resources to perform operations that the script
handles — it coordinates all of those changes atomically and commits them.

Run the script inside `limactl shell fedora` (or a container) because it needs Python
dependencies (`specfile`, `packaging`, `jinja2`) that are installed there.

| Command | When to use |
| ------- | ----------- |
| `./ci/dist_git.py import fedora/<pkg>` | Import a new package from Fedora rawhide (default) or another branch (`--branch f42`) |
| `./ci/dist_git.py update [<pkg>]` | Pull latest Fedora changes into one or all packages (merges local modifications) |
| `./ci/dist_git.py sync <pkg>` | Force-sync a package to upstream, discarding local changes |
| `./ci/dist_git.py rebuild <pkg> --reason "..."` | Bump Release to trigger a no-change rebuild |
| `./ci/dist_git.py rebuild-rev-deps <pkg> --reason "..."` | Rebuild all reverse-dependencies of a package |
| `./ci/dist_git.py rename <pkg>` | Rename a package (reads new name from spec) |
| `./ci/dist_git.py mark-modified <pkg> --modified --reason "..."` | Mark a package as locally modified so it isn't auto-updated |
| `./ci/dist_git.py mark-modified <pkg> --clean` | Clear the modified flag to allow auto-updates again |
| `./ci/dist_git.py set-upstream <pkg> --track-version <prefix>` | Pin a versioned package to a version line (e.g., `1.26`) |
| `./ci/dist_git.py list [--clean\|--modified\|--native\|--prerelease]` | List packages by status |
| `./ci/dist_git.py diff [<pkg>…\|--all]` | Show what changed locally vs Fedora upstream |
| `./ci/dist_git.py ls-sources <pkg>` | Inspect source archives for a package |
| `./ci/dist_git.py update-releases` | Refresh `upstream-releases.json` from Bodhi |

The `--dry-run` flag is available on most commands to preview without committing.

**Keeping this file in sync:** Whenever a new `dist_git.py` subcommand is added, or a new
operational task is introduced (new script, new workflow, new hook type), update both the
command table above and the Operations Index below in the same commit. This file is the
authoritative quick-reference for agents and should always reflect the current state of the
tooling.

## Operations Index

Common operational tasks that users or AI agents may need to perform:

| Operation                    | `dist_git.py` command                           | Documentation                                                                             | When to use                                                  |
| ---------------------------- | ----------------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Import dist-git package      | `import fedora/<pkg>`                           | [Updating Dist-git Packages](documentation/operating/updating-dist-git-packages.md)       | Add a new package from Fedora dist-git                       |
| Add native package           | _(manual — not from Fedora)_                    | [Adding Native Packages](documentation/operating/adding-native-packages.md)               | Add new package not imported from Fedora                     |
| Rebuild package (no-change)  | `rebuild <pkg> --reason "..."`                  | [Rebuilding Packages](documentation/operating/rebuilding-packages.md)                     | Faulty RPM published; need to bump Release for rebuild       |
| Backport a patch             | `mark-modified` then edit spec                  | [Rebuilding Packages](documentation/operating/rebuilding-packages.md)                     | Fast-track an upstream fix not yet in Fedora                 |
| Debug build failures         | _(see docs)_                                    | [Debugging Build Failures](documentation/operating/debugging-build-failures.md)           | Investigate and fix failed package builds                    |
| Mark package modified        | `mark-modified <pkg> --modified --reason "..."` | [Package Modification Tracking](documentation/operating/package-modification-tracking.md) | Track local changes; prevent automatic Fedora updates        |
| Set package basename         | `set-upstream <pkg> --track-version <prefix>`   | [Package Modification Tracking](documentation/operating/package-modification-tracking.md) | Set basename/track_version for versioned packages            |
| Track upstream versions      | `set-upstream <pkg>`                            | [Package Modification Tracking](documentation/operating/package-modification-tracking.md) | Enable/disable upstream version checking for a package       |
| List upstream version status | `list`                                          | [Package Modification Tracking](documentation/operating/package-modification-tracking.md) | See all packages with upstream version and tracking info     |
| View package differences     | `diff <pkg>`                                    | [Package Modification Tracking](documentation/operating/package-modification-tracking.md) | See what changed in modified packages vs Fedora              |
| Add per-package update hooks | _(edit metadata hooks YAML)_                    | [Package Modification Tracking](documentation/operating/package-modification-tracking.md) | Customize spec update, source download, or post-update steps |
| Update dist-git packages     | `update [<pkg>]`                                | [Updating Dist-git Packages](documentation/operating/updating-dist-git-packages.md)       | Test or trigger automated package updates from Fedora        |
| Lookaside cache access       | _(AWS credentials setup)_                       | [Lookaside Cache Access](documentation/operating/lookaside-cache-access.md)               | Set up AWS credentials to upload source tarballs             |
| Analyze upstream diffs       | `diff --all --stat`                             | [Upstream Diff Analysis](documentation/operating/upstream-diff-analysis.md)               | Classify modified packages for upstreaming                   |
| Report CVE data issues       | _(see docs)_                                    | [Reporting CVE Data Issues](documentation/operating/reporting-cve-data-issues.md)         | Get incorrect CVE data on cve.org or NIST corrected          |
| Konflux resource deployment  | _(background info)_                             | [Konflux Resource Deployment](documentation/background/konflux-resource-deployment.md)    | Understand how Konflux resources are deployed                |

## Post-Import / Post-Add Requirements

After importing a new SRPM (`dist_git.py import`) or adding a native package, the `upstream_repo`
field **must** be populated in `metadata/<package>.json` before CI will pass. The `dist_git.py
import` command does not set this field automatically.

To populate it:

1. Use the `/generate-package-map` Cursor skill (`.cursor/skills/generate-package-map/SKILL.md`),
   which searches for the canonical upstream git repository and handles mirror canonicalization,
   known overrides, and versioned-package branches.
2. Or manually add the field to `metadata/<package>.json`:
   `"upstream_repo": "https://github.com/example/project"`
3. If no upstream git repository exists, use the Fedora DistGit URL as a fallback:
   `"upstream_repo": "https://src.fedoraproject.org/rpms/<package>"`

CI enforces this via `test/test_package_map.py::test_upstream_repo_exists`.

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
