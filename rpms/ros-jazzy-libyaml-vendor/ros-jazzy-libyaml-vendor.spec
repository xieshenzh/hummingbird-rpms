Name:           ros-jazzy-libyaml-vendor
Version:        1.6.4
Release:        1%{?dist}
Summary:        Vendor package for the libyaml C library

License:        Apache-2.0
URL:            https://github.com/ros2/libyaml_vendor
Source0:        https://github.com/ros2/libyaml_vendor/archive/refs/tags/%{version}.tar.gz#/%{name}-%{version}.tar.gz

# noarch: when the system libyaml is present, ament_vendor() only installs the
# bundled Findyaml.cmake + extras that re-export it — nothing is compiled here.
BuildArch:      noarch

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  pkgconf-pkg-config
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
# buildtool_depend
BuildRequires:  ros-jazzy-ament-cmake
BuildRequires:  ros-jazzy-ament-cmake-vendor-package
# build_depend: libyaml-dev -> Fedora libyaml-devel. Its presence makes
# find_package(yaml 0.2.5 EXACT QUIET) SATISFIED (Fedora ships libyaml 0.2.5,
# an EXACT match), so ament_vendor() skips the git download (offline build).
BuildRequires:  libyaml-devel

# ament_export_dependencies(yaml): downstream find_package(libyaml_vendor) runs
# the bundled Findyaml.cmake, which locates system libyaml via pkg-config — so
# consumers need libyaml-devel (headers + yaml-0.1.pc) and pkg-config present.
Requires:       libyaml-devel
Requires:       pkgconf-pkg-config

%description
libyaml_vendor is an ament wrapper around the libyaml C library. When a suitable
system libyaml is available it provides a Findyaml CMake module that re-exports
it; otherwise it builds libyaml from source. Here it uses Fedora's system
libyaml (0.2.5). Installed under %{_libdir}/ros-jazzy.

%prep
%autosetup -n libyaml_vendor-%{version}

%build
# Vendor CMake shim + Findyaml module only (system libyaml is used); no compile.

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
    --packages-select libyaml_vendor

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
