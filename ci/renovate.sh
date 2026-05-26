#!/bin/bash
set -euo pipefail

# This script is primarily meant to be used by Renovate as a safe
# postUpgradeTask script to regenerate rendered Tekton YAML after
# image digest updates in generate_resources.py.

make generate-host
