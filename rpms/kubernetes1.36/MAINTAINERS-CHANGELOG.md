# Release Changelog

This project contains the spec file and associated artifacts used to build [Kubernetes](https://kubernetes.io) rpms for Fedora.

All notable changes to this project will be documented in this file.

This document's structure is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Under Consideration

- Incorporate support for automated bundled version provides. The
  current spec file basically ignores bundled provides.

## [1.31.1] - 2024.08.13
- Kubernetes 1.31 released.


## [1.30.10] - 2024.05

- Kubernetes 1.30.1 released.
- New spec file submitted for review
- Retains subpackage layout and organization; otherwise largely new code.
- Does not include bundled vendor provides pending additional work.
- Use gobuild instead of make to build packages according to Fedora golang
  standards.

## [1.30.0] - 2024.04

- Kubernetes 1.30.0 released. 
- Designate v1.30.x as the change over to versioned kubernetes rpms.
- Try to create new spec file using go2rpm as starting point.

## [1.2.9]

### Added

- Initial commit of separate MAINTAINERS-CHANGELOG.md




