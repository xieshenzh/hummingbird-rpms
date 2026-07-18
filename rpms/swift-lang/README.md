# Swift Language Fedora Package

## update-swift-version.sh

A script to update the Swift language package to a new version by automatically downloading the official Swift configuration and updating the RPM spec file.

### Usage

```bash
./update-swift-version.sh <swift-version>
```

### Example

```bash
./update-swift-version.sh 6.1.3
```

### What it does

1. Downloads the Swift release configuration from the official Swift repository
2. Parses the repository versions for all Swift components
3. Updates the `swift-lang.spec` file with new source URLs and version information
4. Removes old forge sources and adds new ones based on the Swift release configuration

### Requirements

- `curl` - for downloading configuration files
- `jq` - for parsing JSON configuration
- `sed` - for updating the spec file

### After running

Review the changes to `swift-lang.spec` and remove any patches that are no longer needed for the new version.
