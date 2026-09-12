Name:           ros-jazzy-console-bridge-vendor
Version:        1.7.2
Release:        1%{?dist}
Summary:        Vendor package for the console_bridge logging library

License:        Apache-2.0
URL:            https://github.com/ros/console_bridge_vendor
Source0:        https://github.com/ros/console_bridge_vendor/archive/refs/tags/%{version}.tar.gz#/%{name}-%{version}.tar.gz

# noarch: when the system console_bridge is present, ament_vendor() only writes
# a CMake shim that re-exports it — nothing is compiled here.
BuildArch:      noarch

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
# buildtool_depend
BuildRequires:  ros-jazzy-ament-cmake
BuildRequires:  ros-jazzy-ament-cmake-vendor-package
# depend: libconsole-bridge-dev -> Fedora console-bridge-devel. Its presence
# makes find_package(console_bridge 1.0.1 QUIET) SATISFIED, so ament_vendor()
# skips the git download (offline build). Fedora ships console-bridge 1.0.2.
BuildRequires:  console-bridge-devel

# Downstream find_package(console_bridge_vendor) re-exports console_bridge, so
# consumers need the console_bridge CMake config (in -devel) at configure time.
Requires:       console-bridge-devel

%description
console_bridge_vendor is an ament wrapper around the console_bridge logging
library. When a suitable system console_bridge is available it provides nothing
but a dependency on it; otherwise it builds console_bridge from source. Here it
uses Fedora's system console-bridge. Installed under %{_libdir}/ros-jazzy.

%prep
%autosetup -n console_bridge_vendor-%{version}

%build
# Vendor CMake shim only (system console_bridge is used); nothing to compile.

%install
_manifest="$PWD/%{name}.files"
export PYTHONUNBUFFERED=1
source %{_libdir}/ros-jazzy/setup.bash
%py3_shebang_fix .

colcon build \
    --merge-install \
    --cmake-args -DPYTHON_EXECUTABLE="%{__python3}" -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DBUILD_TESTING=OFF \
    --base-paths . \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select console_bridge_vendor

# Strip the buildroot path from generated text files.
find %{buildroot}%{_libdir}/ros-jazzy/ -type f ! -name '*.so*' \
    -exec sh -c 'file "$1" | grep -q text && sed -i "s:%{buildroot}::g" "$1"' _ {} \;

# Prefix-level setup scripts are owned by ros-jazzy-ament-package.
rm -rf %{buildroot}%{_libdir}/ros-jazzy/{.catkin,.rosinstall,_setup*,local_setup*,setup*,env.sh,.colcon_install_layout,COLCON_IGNORE,_local_setup*}

find %{buildroot} -type d -name '__pycache__' -exec rm -rf {} +
for file in $(grep -rIl '^#!.*@PYTHON_EXECUTABLE@.*$' %{buildroot} || :) ; do
    sed -i 's:^#!\s*@PYTHON_EXECUTABLE@\s*:#!%{__python3}:' "$file"
done
%py3_shebang_fix %{buildroot}

# Generate the packaged-file manifest. Shared scaffolding dirs are owned by
# ros-jazzy-ament-package / ros-jazzy-ament-cmake-core; own everything else.
cd %{buildroot}%{_libdir}/ros-jazzy
find . -mindepth 1 \( -type f -o -type l \) -printf '%{_libdir}/ros-jazzy/%%P\n' > "$_manifest"
find . -mindepth 1 -type d \
    ! -path './lib' \
    ! -path './lib/python%{python3_version}' \
    ! -path './lib/python%{python3_version}/site-packages' \
    ! -path './share' \
    ! -path './share/ament_index' \
    ! -path './share/ament_index/resource_index' \
    ! -path './share/ament_index/resource_index/packages' \
    ! -path './share/ament_index/resource_index/package_run_dependencies' \
    ! -path './share/ament_index/resource_index/parent_prefix_path' \
    ! -path './share/colcon-core' \
    ! -path './share/colcon-core/packages' \
    -printf '%%%%dir %{_libdir}/ros-jazzy/%%P\n' >> "$_manifest"
cd - >/dev/null

%files -f %{name}.files
%license LICENSE

%changelog
%autochangelog
