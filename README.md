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
