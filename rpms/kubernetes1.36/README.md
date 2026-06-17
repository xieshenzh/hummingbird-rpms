# kubernetes1.36

Kubernetes v1.36 rpms for Fedora.

Kubernetes releases are tracked upstream at https://kubernetes.io/releases/. Upstream is the canonical source for Kubernetes lifecycle plans and status.

## Kubernetes Package Workflow

The workflow to revise the spec file uses the ```newrelease``` script. This script uses a configuration file (```newrelease.conf```) and a template spec file to generate a revised spec file for use in the standard Fedora build processes. Edits made directly to the spec file will be over-written during the next update cycle.

### Generate a configuration file, if none present

1. Run

    ```
    newrelease -c
    ```

### Upstream has a new minor or patch release

1. Edit the newrelease.conf file with the revised patch number.

2. Run

    ```
    newrelease -y
    ```

3. Follow normal Fedora build and release process.

### Other modifications to the spec file to fix errors or add features

1. Edit the template (initially ```./template/kubernetes-template.spec``` but set in configuration file)

2. Run

    ```
    newrelease -y
    ```

3. Follow normal Fedora build and release process.

