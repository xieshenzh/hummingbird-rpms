Name:           ros-jazzy-gz-utils-vendor
Version:        0.0.5
Release:        1%{?dist}
Summary:        Gazebo gz-utils 2.2.1 vendor.

License:        Apache-2.0
URL:            https://github.com/gazebo-release/gz_utils_vendor
Source0:        https://github.com/gazebo-release/gz_utils_vendor/archive/refs/tags/0.0.5.tar.gz#/ros-jazzy-gz-utils-vendor-0.0.5.tar.gz
Source1:        https://github.com/gazebosim/gz-utils/archive/refs/tags/gz-utils2_2.2.1.tar.gz#/gz-utils-2.2.1.tar.gz

# Vendor ExternalProject builds install prebuilt .so's; skip RPM debuginfo.
%global debug_package %{nil}

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  git
BuildRequires:  patch
BuildRequires:  pkgconf-pkg-config
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
BuildRequires:  ros-jazzy-ament-cmake
BuildRequires:  ros-jazzy-ament-cmake-vendor-package
BuildRequires:  ros-jazzy-ament-package
BuildRequires:  spdlog-devel
BuildRequires:  ros-jazzy-gz-cmake-vendor

Requires:       ros-jazzy-ament-package
Requires:       spdlog-devel
Requires:       ros-jazzy-gz-cmake-vendor

%description
Gazebo gz-utils 2.2.1 vendor. Part of the Gazebo simulation stack for ROS 2 Jazzy, under %{_libdir}/ros-jazzy.

%prep
%autosetup -n gz_utils_vendor-0.0.5
# Offline vendoring: stage upstream, pre-apply vendor patches, rewire
# ament_vendor() to VCS_TYPE path (local source, no network clone in mock).
tar -xzf %{SOURCE1} -C %{_builddir}
_gzsrc="%{_builddir}/$(tar tzf %{SOURCE1} | head -1 | cut -d/ -f1)"
if [ -d patches ]; then for p in patches/*.patch patches/*.diff; do [ -e "$p" ] && patch -d "$_gzsrc" -p1 < "$p"; done; fi
sed -i "s#VCS_URL https://github.com/[^ ]*#VCS_TYPE path\n  VCS_URL $_gzsrc#" CMakeLists.txt
sed -i "/^  PATCHES patches$/d" CMakeLists.txt
grep -n -A6 "ament_vendor(" CMakeLists.txt || :

%build

%install
_manifest="$PWD/%{name}.files"
export PYTHONUNBUFFERED=1
source %{_libdir}/ros-jazzy/setup.bash

colcon build \
    --merge-install \
    --cmake-args -DPYTHON_EXECUTABLE="%{__python3}" -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DBUILD_TESTING=OFF \
    --base-paths . \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select gz_utils_vendor

find %{buildroot}%{_libdir}/ros-jazzy/ -type f ! -name '*.so*' \
    -exec sh -c 'file "$1" | grep -q text && sed -i "s:%{buildroot}::g" "$1"' _ {} \;
rm -rf %{buildroot}%{_libdir}/ros-jazzy/{.catkin,.rosinstall,_setup*,local_setup*,setup*,env.sh,.colcon_install_layout,COLCON_IGNORE,_local_setup*}
find %{buildroot} -type d -name '__pycache__' -exec rm -rf {} +

cd %{buildroot}%{_libdir}/ros-jazzy
find . -mindepth 1 \( -type f -o -type l \) -printf '%{_libdir}/ros-jazzy/%%P\n' > "$_manifest"
find . -mindepth 1 -type d \
    ! -path './lib' ! -path './lib/python%{python3_version}' ! -path './lib/python%{python3_version}/site-packages' \
    ! -path './share' ! -path './share/ament_index' ! -path './share/ament_index/resource_index' \
    ! -path './share/ament_index/resource_index/packages' ! -path './share/ament_index/resource_index/package_run_dependencies' \
    ! -path './share/ament_index/resource_index/parent_prefix_path' ! -path './share/colcon-core' ! -path './share/colcon-core/packages' \
    -printf '%%%%dir %{_libdir}/ros-jazzy/%%P\n' >> "$_manifest"
cd - >/dev/null

%files -f %{name}.files

%changelog
%autochangelog
