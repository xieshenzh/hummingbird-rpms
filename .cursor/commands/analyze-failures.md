---
name: analyze-failures
description: Investigate and analyze failing Konflux tests in a GitLab merge request
arguments:
  - name: mr_url
    description: Full GitLab merge request URL
    required: true
---

# GitLab MR Failure Analyzer

Investigate failing Konflux tests in a GitLab merge request by fetching PipelineRun details, TaskRun
logs, and error analysis. Generate a detailed forensic report documenting all investigation steps.

**Scope:** This command focuses exclusively on analyzing FAILING Konflux tests. It does not report
on:

- GitLab-native CI pipeline jobs
- Overall merge readiness

**Note on successful tests:** Data.5 fetches ALL PipelineRuns and TaskRuns for the commit (both
passing and failing). While the primary focus is analyzing failures, successful tests are available
for **comparison purposes** when it helps explain why something failed (e.g., "Package A built
successfully but Package B failed - here's what's different").

**Approach:**

- **Efficient bulk fetching**: Uses Kubernetes label selectors to retrieve all PipelineRuns and
  TaskRuns for a commit in just 2 API calls per cluster (one for PipelineRuns, one for TaskRuns)
- **Comprehensive data**: Fetches both passing and failing tests, enabling comparison during
  analysis
- **On-demand logs**: Only fetches logs when needed to explain failures
- **Partial reports**: If some clusters are unavailable, the analysis continues with accessible
  clusters

Use this for debugging and root cause analysis of failures, not for general CI status overview.

## Design Philosophy

**This command uses pseudo-code for AI interpretation, not literal bash scripting.**

The workflow steps describe WHAT needs to happen at a conceptual level, not HOW to implement it with
specific code. This design:

- Allows flexibility in choosing implementation details (jq queries, data structures, error handling
  patterns)
- Maintains focus on logic and requirements rather than syntax
- Enables adaptation to different execution environments
- Facilitates iteration without rewriting concrete code blocks

When modifying this command:

- Keep steps at a high conceptual level
- Describe operations, don't write scripts
- Specify constraints and requirements clearly
- Let implementation details be determined within the constraints

## Strict Constraints

These constraints must be followed:

- **File Operations**: All file I/O limited to temporary directory only
- **API Calls**: Use `glab api` for GitLab, `../containers/ci/internal/k8s_helper.py` for
  Kubernetes/Kubearchive
- **Recommended Commands**: `glab api` for GitLab API, `../containers/ci/internal/k8s_helper.py` for
  K8s/Kubearchive access, `jq` for JSON parsing, `sed` for URL parsing, `curl` for Testing Farm,
  `python3` for complex data processing
- **SSL Verification**: Always enabled - no `--insecure` flags
- **Work Log**: Build incrementally - append to report.md after each step with timestamps
- **Error Handling**: Fetch on-demand and provide partial reports - individual test failures should
  not prevent analysis of other tests
- **Shell Persistence**: Each tool invocation may use a new shell - persist state via files in temp
  directory, not shell variables
- **Progress Logging**: Echo user-visible progress messages at the start of each major step (e.g.,
  "=== Data.3: Fetching commit statuses ===") so users can see what's happening
- **Relative Paths in Reports**: Use relative paths in markdown links (e.g., `logs/file.xml`) not
  absolute file:// URLs

## Allowed API Endpoints

### GitLab API (via glab)

Use `glab api` command for all GitLab API access. Output is JSON to stdout (pipe to `jq` for
parsing).

**Commands:**

- `glab api /projects/{encoded_project}/merge_requests/{iid}` - Get MR details
- `glab api --paginate /projects/{encoded_project}/repository/commits/{sha}/statuses` - Get commit
  statuses

### Kubernetes/Kubearchive API (via k8s_helper.py)

Use `../containers/ci/internal/k8s_helper.py` for all Kubernetes and Kubearchive API access.

**Note:** The k8s_helper.py script lives in the containers repository. Run it relative to this repo:
`python3 ../containers/ci/internal/k8s_helper.py`

**Commands:**

```bash
# Get all PipelineRuns for a commit (both BUILD and TEST)
python3 ../containers/ci/internal/k8s_helper.py --cluster-url <url-with-namespace> \
  pipelinerun get {commit-sha}

# Get all TaskRuns for a commit (both BUILD and TEST)
python3 ../containers/ci/internal/k8s_helper.py --cluster-url <url-with-namespace> \
  taskrun get {commit-sha}

# Get logs for a pod
python3 ../containers/ci/internal/k8s_helper.py --cluster-url <url-with-namespace> \
  pod log {pod-name}
```

**Cluster URL format:**

The `--cluster-url` parameter must contain both cluster domain and namespace:

- Konflux UI:
  `https://konflux-ui.apps.kflux-prd-rh03.nnv1.p1.openshiftapps.com/ns/hummingbird-tenant/pipelinerun/...`
- Direct: `https://anything.apps.kflux-prd-rh03.nnv1.p1.openshiftapps.com/ns/hummingbird-tenant/`

The helper extracts:

- Cluster domain from the URL (matches `apps.{cluster}` or `api.{cluster}` patterns)
- Namespace from `/ns/{namespace}/` or `/namespaces/{namespace}/` patterns in URL

**Output format:**

- `pipelinerun get` and `taskrun get`: JSON array of items
- `pod log`: Plain text log output

**Data sources:**

The helper automatically fetches from both Kubearchive (historical data) and K8s API (live data),
then combines and deduplicates results. This ensures complete data coverage even if resources have
been deleted from the live cluster.

**Authentication:**

Ensure you are logged in to the Konflux cluster before running:

```bash
oc login --server=https://api.kflux-prd-rh03.nnv1.p1.openshiftapps.com:6443
```

### Testing Farm API

Base URL: `https://artifacts.osci.redhat.com/testing-farm/{request-id}`

Testing Farm is an external testing service integrated with Konflux. The artifacts URL is found in
GitLab commit status `target_url` for TEST PipelineRuns.

**Key endpoints:**

- `GET /{request-id}/results.xml` - Structured test results in JUnit XML format
  - Contains test suite metadata, test case results, and log URLs
  - Use this as the primary source for test failure investigation
- `GET /{request-id}/pipeline.log` - Overall pipeline execution log
- `GET /{log-path}` - Individual test logs (URLs found in `results.xml`)
  - Example: `{request-id}/work-.../execute/data/guest/default-0/{test-name}/output.txt`

**Test result structure** (`results.xml`):

- `//testsuites/@overall-result` - Overall test result (passed/failed)
- `//testsuite` - Test suite with metadata (@name, @result, @tests, @stage)
- `//testcase` - Individual test cases with:
  - `@name` - Test name
  - `@result` - Test result (passed/failed)
  - `@time` - Execution time in seconds
  - `<failure/>` - Present if test failed
  - `<logs>` - Child elements with log URLs:
    - `<log name="testout.log" href="..."/>` - Main test output
    - `<log name="failures.yaml" href="..."/>` - Structured failure data
    - `<log name="data" href="..."/>` - Test data directory

**Usage pattern:**

1. Extract Testing Farm request ID from GitLab status `target_url`
2. Fetch `results.xml` to identify failed tests
3. For each failed test, extract log URLs from `<logs>` elements
4. Fetch `testout.log` for detailed failure information

## RPM Build Pipeline Structure

The rpms repository builds RPM packages using Konflux pipelines. Understanding the pipeline
structure helps diagnose failures:

### Pipeline Types

**BUILD Pipelines** (`{package}-main-on-pull-request-*`):

- Build individual RPM packages
- One pipeline per package changed in the MR
- Key steps: `clone-repository`, `process-sources`, `prepare-mock-config`, `calculate-deps-{arch}`,
  `rpmbuild-{arch}`

**TEST Pipelines** (`rpms-main-testing-farm-{arch}-*`):

- Run integration tests via Testing Farm
- One pipeline per architecture (x86-64, aarch64)
- Tests RPMs after successful builds

### Common Failure Patterns

**calculate-deps failures:**

- Missing dependencies in the repo
- Dependency version conflicts
- **Parallel build timing**: Package A depends on Package B, but both started building
  simultaneously
- Architecture-specific dependency issues (fails on x86-64/aarch64 but succeeds on ppc64le/s390x)

**rpmbuild failures:**

- Compilation errors
- Missing build dependencies
- Spec file issues
- Patch application failures

**Testing Farm failures:**

- RPM installation failures
- Test script errors
- Timeout issues

### Hummingbird-Specific Resolution Patterns

When analyzing dependency resolution failures (`calculate-deps`), check for these common patterns
specific to the hummingbird build environment. These should be noted in the analysis report with
concrete recommendations. **Do not apply fixes — only suggest them to the user.**

#### Pattern 1: Previous version already had a fix — re-apply local modifications

Automated `dist_git.py update` pulls the raw Fedora spec, overwriting local hummingbird
modifications. If the previous version of the package had spec changes (e.g., conditional
`BuildRequires` exclusions), the update discards them.

- **How to detect**: Check `metadata/<package>.json` on the `main` branch. If
  `modification_status` is `"modified"`, read `modification_reason` — it describes what was changed
  and why. Then compare the main branch spec against the update branch spec to see what local
  changes were lost.
- **Suggested fix**: Re-apply the same conditional guards from the previous version to the updated spec.
- **Example**: meson excludes `gcc-objc`, `qt5-qtbase-devel`, `gnustep-base-devel`, and `wxGTK-devel`
  via `%if %{undefined rhel} && !%{defined hummingbird}` because hummingbird's gcc does not build
  objc support and some dependencies conflict with hummingbird's libicu version.

#### Pattern 2: Missing packages — need to import into hummingbird

A Fedora package requires a library that conflicts with a hummingbird-overlay version (e.g.,
`libicu 77` vs `libicu 78`). The Fedora package must be imported into hummingbird and rebuilt
against the hummingbird version of the library.

- **How to detect**: DNF error shows `cannot install both <pkg>-X.hum1 from hummingbird and
  <pkg>-Y.fc43 from fedora`. Trace the dependency chain to find which Fedora package needs the
  older library version.
- **Suggested fix**: Import the Fedora package into hummingbird (`dist_git.py import`) so it gets
  rebuilt against the hummingbird library version. This may cascade — importing one package can
  reveal further dependency conflicts that require additional imports.
- **Example**: poppler required importing tinysparql because `libtinysparql` (Fedora) needed
  `libicu 77` while hummingbird ships `libicu 78`.

#### Pattern 3: Honor `%{rhel}` or `%{hummingbird}` conditionals

Many Fedora specs use `%if %{undefined rhel}` to gate optional BuildRequires that are unavailable
or unnecessary on RHEL. Hummingbird is Fedora-based (does NOT define `%{rhel}`) but DOES define
`%{hummingbird}`. When a BuildRequires is unavailable in hummingbird, adding
`&& !%{defined hummingbird}` to an existing `%if %{undefined rhel}` guard is the standard fix.

- **How to detect**: The failing BuildRequires is inside a `%if %{undefined rhel}` block in the
  spec. The package exists in Fedora but cannot be installed due to conflicts with hummingbird
  overlay packages (or simply is not available).
- **Suggested fix**: Change `%if %{undefined rhel}` to
  `%if %{undefined rhel} && !%{defined hummingbird}`. Update `metadata/<package>.json` with
  `modification_status: "modified"` and a clear `modification_reason`.
- **Example**: meson gates `wxGTK-devel` behind `!%{defined hummingbird}` because wxGTK depends on
  webkit2gtk4.1 which requires `libicu 77`, conflicting with hummingbird's `libicu 78`.

## Workflow

### Setup Phase

#### Setup.1: Validate Prerequisites

**Progress:** Echo "=== Setup.1: Validating prerequisites ==="

**Validate required commands:**

1. Check that `glab` command is available
2. Check that `jq` command is available
3. Check that Python 3 is available
4. Check that `../containers/ci/internal/k8s_helper.py` exists

If any command is missing, fail with error message indicating which command needs to be installed.

#### Setup.2: Parse MR URL Basics

**Progress:** Echo "=== Setup.2: Parsing MR URL ==="

Parse the provided MR URL to extract project and MR IID (needed for workspace setup):

- Expected format: `https://{host}/{project}/-/merge_requests/{iid}`
- Extract project path: `sed -n 's#https://[^/]*/\(.*\)/-/merge_requests/.*#\1#p'`
- Extract MR IID: `sed -n 's#.*/merge_requests/\([0-9]*\).*#\1#p'`

If URL doesn't match expected format, fail with error.

Log: Extracted project and MR IID

#### Setup.3: Create Temporary Workspace

**Progress:** Echo "=== Setup.3: Creating temporary workspace ==="

Create a deterministic temporary directory based on project name and MR IID to avoid conflicts with
concurrent runs:

- Sanitize project path for use in directory name: replace `/` with `-` (e.g.,
  `redhat/hummingbird/rpms` -> `redhat-hummingbird-rpms`)
- Path: `/tmp/analyze-failures-${SANITIZED_PROJECT}-mr${MR_IID}`
- If this directory already exists, purge it completely first:
  `rm -rf /tmp/analyze-failures-${SANITIZED_PROJECT}-mr${MR_IID}`
- Create fresh directory with subdirectories:
  - `api-responses/` - for JSON responses from APIs
  - `logs/` - for pod logs
  - `analysis/` - for intermediate analysis files

Initialize `report.md` with work log header.

**Note:** Using a deterministic path based on project + MR IID means:

- No need to save/retrieve tmpdir path across shell invocations
- Different projects and MRs can be analyzed concurrently without conflicts
- Re-running analysis for the same MR always starts fresh (purges old data)
- Example: `/tmp/analyze-failures-redhat-hummingbird-rpms-mr263`

#### Setup.4: Initialize Work Log

**Progress:** Echo "=== Setup.4: Initializing work log ==="

Set up a mechanism to append work log entries to `report.md` throughout the workflow as a bulleted
list. Each major step should add an entry.

Log:

- "Started failure analysis"
- "Parsed MR IID from URL"

### Data Collection Phase

#### Data.1: Parse Remaining MR URL Details

**Progress:** Echo "=== Data.1: Parsing remaining MR URL details ==="

Parse the MR URL to extract remaining details (project path and MR IID already extracted in
Setup.2):

- GitLab host: `sed -n 's#^\(https://[^/]*\).*#\1#p'`
- URL-encode project path using `printf '%s' "$PROJECT_PATH" | jq -sRr @uri` (NOT `echo` - it adds
  trailing newline)

Log: Parsed full MR URL details

**Known Issue:** Using `echo` for piping to jq will add a newline character to the encoded output.
Always use `printf '%s'` instead.

#### Data.2: Fetch MR Details

**Progress:** Echo "=== Data.2: Fetching MR details from GitLab ==="

Call GitLab API using glab: `glab api /projects/{encoded_project}/merge_requests/{iid}`

Save response to `api-responses/mr-details.json`.

If API call fails, fail immediately with error.

Extract from response using jq:

- `sha` - head commit SHA (this is what we'll analyze)
- `title` - MR title
- `author.username` - author
- `state` - MR state
- `web_url` - MR URL

Store these values for later use in a file that can be sourced by bash. **IMPORTANT**: When writing
variables to a file for sourcing, properly quote all values to handle special characters (colons,
spaces, etc.). Use format: `VAR_NAME="value"` not `VAR_NAME=value`.

Log: Fetched MR details, will analyze commit {sha}

#### Data.3: Fetch Commit Statuses and Identify Clusters

**Progress:** Echo "=== Data.3: Fetching commit statuses and identifying failing tests ==="

Call GitLab API with automatic pagination:
`glab api --paginate /projects/{encoded_project}/repository/commits/{sha}/statuses`

The `--paginate` flag automatically handles pagination and returns all results. Use `jq -s 'add'` to
combine the paginated JSON arrays into a single array.

Save combined response to `api-responses/commit-statuses.json`.

If API call fails, fail immediately.

Filter for failing Konflux tests - statuses where:

- `name` field contains "Konflux"
- `status` field is "failed"

**Note:** This command only analyzes tests that have FAILED. Tests with other statuses are ignored:

- `pending` - test hasn't completed yet (cannot analyze)
- `running` - test is in progress (cannot analyze)
- `success` - test passed (no failure to analyze)
- `canceled` - test was aborted (not a failure)

Passing Konflux tests and GitLab-native CI jobs are also ignored.

Save any failing test's `target_url` to use in Data.4 (all tests use the same cluster/namespace).

Count the number of failing tests. If zero, skip remaining steps.

**Note:** There is only one Konflux cluster per MR. All tests run on the same cluster and namespace.
The `k8s_helper.py` script will extract cluster domain and namespace from the URL.

Log: Number of failing Konflux tests identified, saved example URL for cluster access

#### Data.4: Fetch All PipelineRuns and TaskRuns for Commit

**Progress:** Echo "=== Data.4: Fetching all PipelineRuns and TaskRuns from Kubernetes ==="

**Goal:** Efficiently retrieve all Tekton resources associated with the commit SHA from Data.2.

If no failing Konflux tests were found in Data.3, skip this step entirely.

**Note:** There is only one Konflux cluster per MR. Use the `target_url` from any failing test saved
in Data.3.

**Fetch PipelineRuns:**

Use `../containers/ci/internal/k8s_helper.py` to fetch all PipelineRuns (both BUILD and TEST):

```bash
# Use the saved URL from any failing test
python3 ../containers/ci/internal/k8s_helper.py --cluster-url {saved-url-from-data3} \
  pipelinerun get {commit-sha} > api-responses/pipelineruns.json
```

The helper automatically extracts cluster domain and namespace from the URL, then:

- Finds matching kubeconfig context
- Fetches from both Kubearchive and K8s API
- Applies both BUILD and TEST label selectors
- Handles pagination
- Combines and deduplicates results
- Returns JSON array of items

**Fetch TaskRuns:**

Use `../containers/ci/internal/k8s_helper.py` to fetch all TaskRuns (both BUILD and TEST):

```bash
# Use same URL as above
python3 ../containers/ci/internal/k8s_helper.py --cluster-url {url-from-failing-test} \
  taskrun get {commit-sha} > api-responses/taskruns.json
```

The helper returns a JSON array of items.

**Error handling:**

If fetch fails (e.g., no kubeconfig credentials, cluster inaccessible):

- Log error and exit - cannot proceed without Kubernetes access
- This is a fatal error
- **Hint:** Run `oc login --server=https://api.kflux-prd-rh03.nnv1.p1.openshiftapps.com:6443` to
  refresh credentials

**Note:** The helper fetches from both Kubearchive (historical data) and K8s API (live data),
ensuring complete coverage even if resources have been deleted from the live cluster.

**Bulk Fetch Testing Farm Data (for Failed TEST PipelineRuns only):**

After fetching all PipelineRuns and TaskRuns, immediately fetch Testing Farm results for failed TEST
PipelineRuns:

1. **Identify failed TEST PipelineRuns**:
   - Read `pipelineruns.json` (JSON array)
   - For each PipelineRun in the array:
     - Check if it has label `pac.test.appstudio.openshift.io/sha` (TEST PipelineRun)
     - Extract name, status from `.status.conditions[0].reason`, component label
     - **Only include if status is NOT "Succeeded" or "Completed"**
       - Failed statuses include: "Failed", "PipelineRunTimeout", "Cancelled", etc.
   - Save mapping: `{pr-name}|{component}` to temp list

2. **Extract Testing Farm request IDs for failed TEST PipelineRuns**:
   - For each failed TEST PipelineRun (from temp list):
     - Find scheduler TaskRun in `taskruns.json` array by matching name pattern:
       `{pr-name}-scheduler-*`
     - Extract TF request URL from TaskRun's
       `.status.results[] | select(.name == "tf-request") | .value`
     - Extract request ID from URL (last path component)
     - Save to `analysis/tf-mapping.txt`: `{pr-name}|{component}|{tf-request-id}`
   - Log: "Extracted N Testing Farm request IDs for failed tests"

3. **Batch fetch all results.xml for failed tests**:
   - For each TF request ID from `analysis/tf-mapping.txt`:
     - Fetch `https://artifacts.osci.redhat.com/testing-farm/{request-id}/results.xml`
     - Save as `logs/tf-results-{request-id}.xml`
     - These are small files (~50KB), quick to fetch
   - If any fetch fails (404, timeout), note in log but continue with others
   - Log: "Fetched M Testing Farm results.xml files for failed tests (N successful, P
     failed/unavailable)"

4. **Parse results.xml to structured summaries for failed tests**:
   - For each results.xml that was successfully fetched:
     - Use **Python with xml.etree.ElementTree** for reliable parsing (avoid awk/sed for XML)
     - Extract:
       - Overall result: `//testsuites/@overall-result` (via xmllint)
       - Total tests: `count(//testcase)` (via xmllint)
       - Failed test details: Parse with Python to get name, time, log URLs from
         `<log name="testout.log">` elements
     - Export environment variables for Python script to access (TMPDIR, TF_REQUEST_ID, PR_NAME,
       COMPONENT, etc.)
     - Save structured data as `analysis/tf-summary-{request-id}.json` with format:

**Known Issue:** Parsing XML with awk/sed is complex and error-prone. Use Python's
xml.etree.ElementTree for reliable extraction of nested elements and attributes. Remember to
`export` environment variables before running Python heredoc scripts.

```json
{
  "request_id": "...",
  "pipelinerun": "...",
  "component": "...",
  "overall_result": "failed",
  "total_tests": 4,
  "failed_tests": [
    {
      "name": "/Podman",
      "time": "130",
      "result": "failed",
      "log_url": "https://artifacts.osci.redhat.com/.../output.txt"
    }
  ]
}
```

- Log: "Parsed M Testing Farm results for failed tests"

**Rationale:** Fetching Testing Farm data in bulk for all failed TEST PipelineRuns here allows
Analysis.1 to do pattern detection across all failures before fetching any test logs. This is much
faster than fetching TF data one-by-one during analysis. Successful tests don't need investigation,
so we skip them.

#### Data.5: Organize Fetched Resources

**Progress:** Echo "=== Data.5: Organizing fetched resources ==="

Extract individual resources from the JSON arrays:

**For PipelineRuns:**

- Read from: `pipelineruns.json` (JSON array)
- For each item in the array:
  - Extract: `name` from `.metadata.name`, `status` from `.status.conditions[0].reason`
  - Determine type: BUILD (has label `pipelinesascode.tekton.dev/sha`) or TEST (has label
    `pac.test.appstudio.openshift.io/sha`)
  - Save to: `api-responses/pipelinerun-{name}-{status}-{build|test}.json`

**For TaskRuns:**

- Read from: `taskruns.json` (JSON array)
- For each item in the array:
  - Extract: `name` from `.metadata.name`, `status` from `.status.conditions[0].reason`
  - Save to: `api-responses/taskrun-{name}-{status}.json`

**Identify failing resources:**

PipelineRuns with non-successful status should be investigated. Success statuses are:

- "Succeeded" - typical for TEST PipelineRuns
- "Completed" - typical for BUILD PipelineRuns

Any other status indicates a failure or abnormal termination:

- "Failed" - explicit failure
- "PipelineRunTimeout" - exceeded time limit
- "Cancelled" / "PipelineRunCancelled" - cancelled
- Other statuses - investigate as potential failures

Focus investigation on both BUILD and TEST PipelineRuns with non-successful status. BUILD failures
(e.g., `{package}-main-on-pull-request-*`) are common in RPM builds due to dependency issues.

**Summary tracking:**

Track and log counts for reporting:

- PipelineRuns by type: BUILD count, TEST count
- PipelineRuns by status:
  - BUILD: succeeded count, failed count
  - TEST: succeeded count, failed count
- Overall: Total PipelineRuns, Total failures

Display format:

```text
PipelineRuns: X BUILD (Y succeeded, Z failed), A TEST (B succeeded, C failed)
Total failures: N
```

This makes it immediately clear how many failures exist and what type they are.

**Note:** The k8s_helper.py already fetched from both Kubearchive (historical) and K8s API (live),
combining and deduplicating results to ensure completeness.

### Analysis Phase

#### Analysis.1: Analyze Failures

**Progress:** Echo "=== Analysis.1: Analyzing all failures ==="

**Goal:** Investigate each PipelineRun failure individually to understand what happened, then
summarize across failures to identify common issues.

**Approach:** Investigate each failure on its own first (understand the specifics), then group by
commonalities (understand the patterns).

**Note:** Use `../containers/ci/internal/k8s_helper.py` for fetching logs when needed.

---

#### Analysis.1a: Investigate Each Failed PipelineRun

**Progress:** Echo "=== Analysis.1a: Investigating individual failures ==="

**Goal:** For each failed PipelineRun, determine what failed and why.

For each PipelineRun file with non-successful status (i.e., not "Succeeded" or "Completed"):

**1. Identify failure type and extract metadata:**

- **Type**: BUILD (filename ends with `-build.json`) or TEST (ends with `-test.json`)
- **Status**: Extract from filename (between last two dashes before `.json`)
- **PipelineRun name**: from filename (remove status and type suffixes)
- **Component/Package**: `.metadata.labels["appstudio.openshift.io/component"]`
- **Architecture**: infer from PipelineRun name or labels (aarch64/x86-64/ppc64le/s390x)
- **Failure message**: `.status.conditions[0].message`

**Note:** Common non-successful statuses include "Failed", "PipelineRunTimeout", "Cancelled",
"PipelineRunCancelled", etc.

**2. Investigate based on type:**

### For BUILD Failures (RPM Package Builds)

**What to fetch:**

- Failed TaskRun details
- Pod logs from failed TaskRun

**How to investigate:**

1. **Find failed TaskRun**:
   - Look at PipelineRun's `.status.childReferences[]`
   - Find TaskRun with failed status
   - Load its file: `taskrun-{name}-Failed.json`

2. **Get failure details from TaskRun**:
   - Failure message: `.status.conditions[0].message`
   - Pod name: `.status.podName`
   - If failure message is clear enough, you may not need logs

3. **Identify the failed step** (common RPM build steps):
   - `calculate-deps-{arch}`: Dependency resolution failed
   - `rpmbuild-{arch}`: RPM compilation/build failed
   - `process-sources`: Source processing failed
   - `prepare-mock-config`: Mock configuration failed

4. **Fetch pod logs if needed** (when failure message isn't clear):
   - Extract pod name from failed TaskRun's `.status.podName` field
   - Use k8s_helper.py to fetch logs (namespace auto-detected from URL):

     ```bash
     python3 ../containers/ci/internal/k8s_helper.py --cluster-url {cluster-url} \
       pod log {pod-name} > logs/build-{pr-name}-{pod-name}.log
     ```

   - The helper automatically tries both Kubearchive and K8s API

5. **Analyze for root cause**:
   - Read last 100 lines of log
   - Look for error patterns:
     - **Dependency issues**: "No package", "nothing provides", "Requires:", "conflicts with"
     - **Build timing**: Check if a dependency package was building in parallel
     - **Compilation**: "error:", "failed to compile", "syntax error"
     - **Patch failures**: "Hunk #N FAILED", "patch does not apply"
     - **Network**: "timeout", "connection refused", "504 Gateway"
     - **Resources**: "out of memory", "disk full", "quota exceeded"
   - Extract 3-5 key error lines

6. **Document findings**:
   - Root cause summary (1 sentence)
   - Key error messages
   - Evidence files used
   - **For dependency timing issues**: Note which packages were building in parallel

### For TEST Failures

**Check if Testing Farm or other:**

- If PipelineRun name is in `analysis/tf-mapping.txt` -> TEST-TF
- Otherwise -> TEST-OTHER

### For TEST-TF (Testing Farm) Failures

**What to fetch:**

- Testing Farm results.xml (already fetched in Data.5)
- Test output logs for failed tests (fetch on-demand)

**How to investigate:**

1. **Get Testing Farm summary** (already available from Data.5):
   - Find TF request ID from `analysis/tf-mapping.txt`
   - Load `analysis/tf-summary-{request-id}.json`
   - This shows which tests failed

2. **Review failed tests**:
   - List of failed tests is in `failed_tests[]` array
   - Each has: name, duration, log_url

3. **Fetch test logs for understanding** (fetch 1-2 representative failed tests):
   - Get log_url from TF summary
   - `GET {log_url}` (direct URL from Testing Farm)
   - Save as `logs/tf-{pr-name}-{test-name}.log`
   - Don't fetch ALL logs - just enough to understand the failure

4. **Analyze for root cause**:
   - Read last 50 lines of representative test log
   - Look for:
     - RPM installation errors: "No package", "conflicts", "nothing provides"
     - Runtime errors: "connection refused", "server not running"
     - Test failures: "FAIL", "assertion failed", "unexpected"
     - Timeouts: "timeout", "deadline exceeded"
   - Extract 3-5 key error lines

5. **Document findings**:
   - Which tests failed (list test names)
   - Root cause summary
   - Key error messages
   - Evidence files used

### For TEST-OTHER (Non-Testing Farm) Failures

**What to fetch:**

- Failed TaskRun details
- Pod logs from failed TaskRun

**How to investigate:**

1. **Find failed TaskRun** (same as BUILD):
   - Look at PipelineRun's `.status.childReferences[]`
   - Find TaskRun with failed status
   - Load: `taskrun-{name}-Failed.json`

2. **Get failure details**:
   - Failure message: `.status.conditions[0].message`
   - Pod name: `.status.podName`

3. **Fetch pod logs if needed**:
   - Extract pod name from failed TaskRun's `.status.podName` field
   - Use k8s_helper.py to fetch logs (namespace auto-detected from URL):

     ```bash
     python3 ../containers/ci/internal/k8s_helper.py --cluster-url {cluster-url} \
       pod log {pod-name} > logs/test-{pr-name}-{pod-name}.log
     ```

   - The helper automatically tries both Kubearchive and K8s API

4. **Analyze for root cause**:
   - Read last 100 lines
   - Look for test-specific errors
   - Extract key error messages

5. **Document findings**:
   - Root cause summary
   - Key error messages
   - Evidence files used

**Output for each investigated PipelineRun:**

Save to `analysis/individual-failures.jsonl` (JSON Lines - one JSON object per line):

```json
{"pipelinerun": "pr-name", "type": "BUILD|TEST-TF|TEST-OTHER", "package": "...", "arch": "aarch64|x86-64|ppc64le|s390x", "failed_step": "calculate-deps|rpmbuild|...", "root_cause": "summary", "key_errors": ["line1", "line2"], "failed_tests": ["test1"] (if TEST-TF), "evidence": ["logs/...", "api-responses/..."], "cluster": "...", "namespace": "..."}
```

**Implementation Note:** Use simple heredoc or cat for JSON generation rather than complex `jq -n`
with multiple variables. Simpler approaches are more reliable and easier to debug. Ensure proper
escaping of quotes in error messages.

**Known Issue:** When looping over files with `for file in pipelinerun-*-Failed-build.json`, the
loop runs for every matching file INCLUDING when extracting PR_NAME from the filename, creating
duplicate records. Always check the actual number of files vs records created. The loop should only
iterate once per actual file.

Log: "Investigated N failed PipelineRuns (X BUILD, Y TEST-TF, Z TEST-OTHER)"

---

#### Analysis.1b: Summarize and Group Failures

**Progress:** Echo "=== Analysis.1b: Grouping failures by root cause ==="

**Goal:** Identify common patterns across individual failures to show which issues affect multiple
builds/tests.

**How to summarize:**

1. **Read all individual failure investigations**:
   - Load `analysis/individual-failures.jsonl`
   - Parse each line as a JSON object

2. **Group by similarity**:

   **Look for common patterns:**
   - Same package + similar root_cause = likely related
   - Same key error messages = same issue
   - Same failed_step across different architectures = architecture-independent issue
   - **Parallel build timing**: Multiple packages failed with dependency errors at the same time

   **Grouping logic:**
   - If 2+ failures have:
     - Same package pattern
     - Similar root cause text (fuzzy match or keyword overlap)
     - Same error signatures
   - Then group them together

   **Example groups:**
   - "sendmail calculate-deps: missing setup dependency" affecting x86-64 and aarch64
   - "Package timing issue: setup not ready when sendmail needed it"
   - "kernel rpmbuild failure" affecting single architecture

3. **Identify cross-architecture impact**:
   - If grouped failures include multiple architectures -> mark as cross-arch
   - If failure only on some architectures (e.g., x86-64/aarch64 but not ppc64le/s390x) -> note the
     pattern

4. **Check for parallel build timing issues**:
   - Compare PipelineRun start times
   - If Package A depends on Package B, and both started at the same time, this is likely the root
     cause
   - **Recommendation**: Split into separate MRs or rebuild after dependency is available

5. **Output summary**:

Save to `analysis/failure-summary.json`:

```json
{
  "groups": [
    {
      "root_cause": "sendmail calculate-deps failed - setup package not yet available",
      "count": 2,
      "type": "BUILD",
      "failed_step": "calculate-deps",
      "cross_arch": false,
      "affected_archs": ["x86-64", "aarch64"],
      "succeeded_archs": ["ppc64le", "s390x"],
      "affected_packages": ["sendmail"],
      "pipelineruns": ["sendmail-main-on-pull-request-j9kmf"],
      "representative": "sendmail-main-on-pull-request-j9kmf",
      "evidence": ["logs/build-sendmail-..."],
      "recommendation": "Rebuild after setup MR is merged, or split into separate MRs"
    }
  ],
  "unique": [
    {
      "root_cause": "...",
      "count": 1,
      "pipelinerun": "...",
      "package": "...",
      "evidence": [...]
    }
  ],
  "summary": {
    "total_failures": 2,
    "groups": 1,
    "unique": 0,
    "parallel_build_issues": 1,
    "most_common_issue": "dependency timing - setup not ready (2 failures)"
  }
}
```

Log: "Summarized failures: N groups affecting M failures, P unique failures"

**Note:** Grouping is helpful for showing patterns, but each failure was already individually
investigated in Analysis.1a. The summary just helps identify which issues affect multiple
components/architectures.

**For each failed PipelineRun**, create an explanation record containing:

- PipelineRun name, type (BUILD/TEST), package, architecture
- Failed step (for BUILD failures)
- Root cause (shared or unique)
- Key error messages
- Evidence examined (which files/logs were analyzed)
- Reference to representative logs (for pattern groups)
- Pattern membership (if part of a pattern, reference the representative)
- **Recommendation** (especially for parallel build timing issues)

**Group explanations by root cause and type** for easier reporting:

- BUILD pattern groups: "dependency resolution failure (2 BUILD failures on x86-64, aarch64)"
- TEST-TF pattern groups: "RPM installation failure (2 TEST failures)"
- Singletons: individual explanations

Save explanations to `analysis/explanations.json` with structure:

```json
{
  "patterns": [
    {
      "type": "BUILD|TEST-TF|TEST-OTHER",
      "root_cause": "summary",
      "count": 2,
      "representative": "pipelinerun-name",
      "members": ["pr-1", "pr-2", ...],
      "evidence_files": ["logs/...", "api-responses/..."],
      "recommendation": "..."
    }
  ],
  "singletons": [
    {
      "pipelinerun": "name",
      "type": "BUILD|TEST-TF|TEST-OTHER",
      "root_cause": "summary",
      "evidence_files": [...],
      "recommendation": "..."
    }
  ]
}
```

Store explanations for use in Report.1 report generation.

Log: "Generated explanations for all failures (N BUILD, M TEST-TF, P TEST-OTHER)"

**Note:** This pattern-based approach:

- Handles both BUILD and TEST failures
- Fetches ~3-5 logs instead of ~20 (for pattern-heavy scenarios)
- Detects patterns before deep investigation
- Scales better (100 failures = similar time to 10)
- Generates better insights by seeing all failures together
- **Identifies parallel build timing issues** common in RPM multi-package MRs

### Report Generation Phase

#### Report.1: Generate Report

**Progress:** Echo "=== Report.1: Generating final report ==="

Build the final report in markdown format with the following sections:

**IMPORTANT - Link Format:** All file paths in the report must be **relative paths** from the
temporary directory, NOT absolute `file://` URLs. For example:

- Good: `logs/tf-results-abc123.xml`
- Bad: `file:///tmp/tmp.xyz/logs/tf-results-abc123.xml`

This makes the report portable and the links clickable when the report is moved or shared.

**1. Executive Summary** (prepend to existing report.md):

- MR title, author, state
- **Commit SHA** being analyzed
- Konflux test status: number of failing tests found
- If no failures: "No failing Konflux tests found"
- If failures exist: count and summary

**2. Key Findings** (actionable guidance for humans): After analyzing all failures in Analysis.1,
synthesize using the pattern detection:

- **All failures must be fixed**: List all root causes grouped by pattern - these are blockers for
  merge
  - For each pattern: "{root-cause-summary} ({N} failures: packages/archs affected)"
  - For singletons: "{root-cause-summary} (1 failure: package)"
- **Failure breakdown by root cause**:
  - Group failures by their root cause from Analysis.1b
  - Show how many failures each root cause accounts for
  - Example: "sendmail calculate-deps: 2 failures on x86-64 and aarch64"
- **Cross-architecture impact**: Which failures occur on multiple architectures vs single
  architecture
- **Parallel build timing issues**: If detected, clearly explain:
  - Which packages have dependencies on each other
  - When each package started building
  - **Recommendation**: "Split into separate MRs" or "Rebuild after dependency is merged"
- **Recommended fix order**: Prioritize by number of failures resolved
- **Quick wins**: Which single fixes would resolve the most failures
- **Direct links to representative evidence**: For each root cause, provide relative path links to
  the representative logs used for analysis:
  - **Make the root cause title (bold text) a clickable link** to the most representative
    human-readable artifact (typically the test output log if fetched, otherwise results.xml)
  - List additional evidence below:
    - Testing Farm results.xml: `logs/tf-results-{request-id}.xml`
    - Test output logs: `logs/tf-{description}.log` (if fetched - include all relevant logs)
    - Build logs: `logs/build-{package}-{pod-name}.log`
    - Representative PipelineRun JSON: `api-responses/pipelinerun-{name}-Failed-build.json`
  - Note: "This representative log explains all {N} failures with this root cause"

**Example format:**

```markdown
**[sendmail calculate-deps: missing setup dependency](logs/build-sendmail-pod.log)** (2 failures on
x86-64, aarch64):

- Parallel build timing: setup and sendmail started at 19:32:41, but sendmail finished (failed) at
  19:39 while setup didn't complete until 19:46
- Build log:
  [`logs/build-sendmail-calculate-deps-x86-64-pod.log`](logs/build-sendmail-calculate-deps-x86-64-pod.log)
- PipelineRun JSON:
  [`api-responses/pipelinerun-sendmail-main-on-pull-request-j9kmf-Failed-build.json`](api-responses/pipelinerun-sendmail-main-on-pull-request-j9kmf-Failed-build.json)
- **Recommendation**: Split setup and sendmail into separate MRs, merge setup first
```

**3. Work Log** (already built incrementally in report.md):

- Format as bulleted list of major steps completed
- Each entry should be a brief description of what was done (not timestamps)

**4. Detailed Findings** (append to report.md):

**Group by root cause** (from Analysis.1b pattern detection):

For each root cause group:

- **Section header**: Root cause summary (N failures)
- **Root cause explanation**:
  - What failed and why
  - Key error messages
  - Investigation steps taken
  - **For parallel build timing**: Show start/completion times of related packages
  - **Representative evidence** (relative paths from temp directory):
    - PipelineRun JSON: `api-responses/pipelinerun-{representative-name}-Failed-build.json`
    - Build logs: `logs/build-{package}-{pod-name}.log`
    - Testing Farm results: `logs/tf-results-{request-id}.xml`
    - Test output logs: `logs/tf-{description}.log` (list all logs that were fetched for this
      failure pattern)
- **All affected PipelineRuns** (list):
  - For each PipelineRun in this group:
    - Name and link to Konflux UI
    - Package, architecture
    - Failed step
    - Testing Farm request ID (link to results.xml for this specific run, if applicable)
    - Note: "Shares root cause with representative above"

For singleton failures (not part of a pattern):

- PipelineRun name and link to Konflux UI
- Package, architecture, cluster/namespace
- Failed step
- Root cause explanation specific to this failure
- Key error messages
- Investigation steps
- **Direct links to artifacts** (relative paths):
  - PipelineRun JSON: `api-responses/pipelinerun-{name}-Failed-build.json`
  - Build logs: `logs/build-{package}-{pod-name}.log`
  - Testing Farm results: `logs/tf-results-{request-id}.xml` (if applicable)
  - Test output logs: `logs/tf-{description}.log` (if fetched - list all relevant logs)
  - TaskRun JSON files: `api-responses/taskrun-{name}-{status}.json` (if relevant)

**5. Downloaded Artifacts** (append to report.md):

- List all PipelineRun JSON files with relative path links:
  `[pipelinerun-{name}-{status}.json](api-responses/pipelinerun-{name}-{status}.json)` (grouped by
  status for clarity)
- List all TaskRun JSON files with relative path links (if relevant):
  `[taskrun-{name}-{status}.json](api-responses/taskrun-{name}-{status}.json)`
- List all build log files: `[build-{package}-{pod-name}.log](logs/build-{package}-{pod-name}.log)`
- List all Testing Farm results.xml files with relative path links:
  `[tf-results-{request-id}.xml](logs/tf-results-{request-id}.xml)`
- List all fetched test logs with relative path links:
  `[tf-{description}.log](logs/tf-{description}.log)`
- Include full absolute path to temporary directory at the top of this section

**Note:**

- The filename convention includes status for easy identification: `-Failed`, `-Succeeded`,
  `-Running`, etc.
- All artifact paths in the list should be markdown links using relative paths for portability

Combine all sections into the final `report.md` file.

#### Report.2: Display Output

**Progress:** Echo "=== Report.2: Analysis complete! ==="

Display the complete generated report to the user.

Print a summary at the end showing:

- "Failure analysis complete!"
- Commit SHA analyzed
- Total PipelineRuns fetched (succeeded + failed)
- Number of failed PipelineRuns investigated
- **Highlight from Key Findings**: 1-2 sentence summary of priority issues
- Location of the report file
- Location of all downloaded artifacts (temporary directory path)

If no failing tests were found, display:

- "No failing Konflux tests found in this MR"
- Commit SHA analyzed
- MR is clear from Konflux perspective (but note: GitLab jobs not checked)

**Note:** Even if some PipelineRuns/TaskRuns were unavailable, the analysis completes successfully
with partial results for what was accessible.

## Error Handling

**Philosophy:** Provide partial reports when possible. Only fail for errors that prevent any
analysis.

### Fatal Errors (exit immediately - cannot continue)

- **Setup.1**: Missing required commands -> Exit with message "Required command not found:
  {command}"
- **Setup.1**: k8s_helper.py not found -> Exit with message "k8s_helper.py not found at
  ../containers/ci/internal/k8s_helper.py - ensure containers repo is checked out"
- **Setup.2**: Invalid MR URL format -> Exit with message "Invalid MR URL format"
- **Setup.3**: Cannot create temp directory -> Exit with message "Cannot create temporary workspace"
- **Data.2**: GitLab API failure -> Exit with message "Cannot fetch MR details"
- **Data.3**: Commit statuses API failure -> Exit with message "Cannot fetch commit statuses"
- **Data.4**: Kubernetes auth failure (401) -> Exit with message "Kubernetes credentials expired -
  run: oc login --server=<https://api.kflux-prd-rh03.nnv1.p1.openshiftapps.com:6443>"

### Non-Fatal Errors (log warning, mark as unavailable, continue with available data)

- **Analysis.1**: Cannot fetch pod logs:
  - Log warning: "Logs for pod {name} unavailable (may have expired or been deleted)"
  - Note in explanation
  - Continue with available data

**Note on Resource Availability:** The k8s_helper.py fetches from both Kubearchive (historical) and
K8s API (live). If resources are unavailable from both sources, this indicates an authentication or
connectivity issue.

**Partial Report Behavior:** Even if some logs are unavailable, the command completes successfully
and reports on what WAS available. This allows useful analysis based on PipelineRun/TaskRun metadata
even without logs.

### Command Exit Code Handling

- Exit code 0: Success
- Non-zero exit code: Command failed
  - `glab api`: GitLab API error (check error message)
  - `k8s_helper.py`: Kubernetes/Kubearchive access error (check stderr)
  - `curl` (for Testing Farm): HTTP error, timeout, or connection failed

For Testing Farm fetches, use `curl -f` to treat HTTP errors (4xx, 5xx) as failures.
