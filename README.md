# Welcome to Project Hummingbird

Project Hummingbird builds a collection of minimal, hardened, and secure container images, aiming to
provide purpose-built containers with a significantly reduced attack surface. This strong focus on
security combined with a highly automated update workflow results in containers with nearly zero
CVEs.

For more details on the project, please refer to the
[Hummingbird containers Git repository](https://gitlab.com/redhat/hummingbird/containers).

## Overview

This is a mono repository that contains:

- Spec files for all RPM components
- Package definitions for RPM components
- Build configurations and automation

## Goals

The primary goal is to establish a fully automated process for:

- Building RPM packages
- Managing package dependencies
- Updating packages automatically
- Providing reliable RPM components for container image builds

## Structure

All spec files and package definitions are organized within this mono repository to facilitate
centralized management and automated builds.

## Scope

We aim to having all rpms that directly go into our
[Hummingbird containers](https://gitlab.com/redhat/hummingbird/containers) built from this
repository. However, this does _not_ include the transitive set of `BuildRequires` -- we keep the
Fedora stable repository available for [building packages](./mock/mock.cfg) and also
[testing](./ci/repos/).

## Evaluating CVEs in Hummingbird RPMs

In general, Hummingbird RPMs track Fedora releases which drive detection and
refreshes of what Hummingbird ships. CVE identifiers and affected-product
metadata come from public sources \- the [CVE Program](https://www.cve.org/)
(CVE Records) and [NIST NVD](https://nvd.nist.gov/), which Red Hat’s Product
Security and automation consume alongside Hummingbird's build and disclosure
processes.

Project Hummingbird uses the
[`hummingbird-tools/hummingbird_tools/cve_analysis.py`][cve-script]
tooling to triage HUM Security component issues.  [The cve\_analysis.py script
correlates CVE and NVD version data with the latest RPM
versions][cve-docs] in the Hummingbird content repo, surfaces rudimentary
upstream and Fedora fix
information, and can update Jira state and labels when operators choose to
apply those recommendations. **Authoritative consumer-facing CVE disposition
for what is shipped is reflected in Red Hat’s
[VEX feed](https://security.access.redhat.com/data/csaf/v2/vex-feed)**, which
records whether a product is affected, not affected, or fixed (and at which
version).

CVE resolutions fall into these three patterns:

1. **Not affected by version** — After comparing the shipped RPM version to
affected and fixed ranges from the CVE Record and NVD, the latest build is
outside all stated affected configurations (or matches an explicit “not
affected” / fixed boundary). The issue is closed as not affected from a
version-analysis perspective.

2. **Fixed in a Konflux build** — A remediation is delivered through the
Konflux pipeline; the resulting build is what is shipped, and the VEX feed
records the specific fix (fixed / fixed-in-version semantics as appropriate) so
downstream tools and customers see an explicit fixed state tied to that
release.  Resolutions with this pattern must include a “Fixes: CVE-YYYY-XXXX”
in the Merge Request Description and/or a commit.

3. **Package moved forward independently** — A CVE is filed while work is in
flight, but the component is refreshed for other reasons (e.g. routine
rebases from Fedora). Once the repo carries a build whose version clears the
advisory’s affected range, the same “not affected” style conclusion applies.
Note that the difference in resolution here is operational (ie., the update was
not driven directly by that ticket) rather than a technical difference.

For step-by-step instructions on manually triaging, assigning, and closing
CVE Jira tickets, see the [manual CVE triage process][cve-manual]. For
details on the automated `cve_analysis.py` tool, CLI flags, managed labels,
and bot-vs-human interaction, see the [CVE analysis documentation][cve-docs].

[cve-script]: https://gitlab.com/redhat/hummingbird/tools/-/blob/main/hummingbird-tools/hummingbird_tools/cve_analysis.py
[cve-docs]: https://gitlab.com/redhat/hummingbird/tools/-/blob/main/documentation/hummingbird-tools-cve-analysis.md
[cve-manual]: https://gitlab.com/redhat/hummingbird/tools/-/blob/main/hummingbird-tools/hummingbird_tools/cve-manual-process.md
