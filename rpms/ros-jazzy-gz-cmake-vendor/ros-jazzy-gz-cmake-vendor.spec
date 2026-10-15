Name:           ros-jazzy-gz-cmake-vendor
Version:        0.0.12
Release:        1%{?dist}
Summary:        Vendor package building gz-cmake 3.6.0 (Gazebo Harmonic) from source

License:        Apache-2.0
URL:            https://github.com/gazebo-release/gz_cmake_vendor
Source0:        https://github.com/gazebo-release/gz_cmake_vendor/archive/refs/tags/%{version}.tar.gz#/%{name}-%{version}.tar.gz
# gz-cmake upstream, pre-staged because mock has NO network. ament_vendor() is
# rewired in %%prep to build it offline via VCS_TYPE path. Pinned to the exact tag
# the vendor requests (gz-cmake3_3.6.0).
Source1:        https://github.com/gazebosim/gz-cmake/archive/refs/tags/gz-cmake3_3.6.0.tar.gz#/gz-cmake-3.6.0.tar.gz

BuildArch:      noarch

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

Requires:       ros-jazzy-ament-package

%description
Vendor package that builds gz-cmake 3.6.0 (Gazebo Harmonic) from upstream source
and installs it under %{_libdir}/ros-jazzy. Part of the Gazebo simulation stack
for ROS 2 Jazzy.

%prep
%autosetup -n gz_cmake_vendor-%{version}
# Stage the gz-cmake upstream source (mock has no network) and pre-apply the
# vendor's in-repo patch, then rewire ament_vendor() to build from the local path
# (VCS_TYPE path performs no download). VCS_TYPE path forbids the PATCHES arg, so
# the patch is applied here and the PATCHES line is removed below.
tar -xzf %{SOURCE1} -C %{_builddir}
_gzsrc="%{_builddir}/gz-cmake-gz-cmake3_3.6.0"
patch -d "$_gzsrc" -p1 < patches/0001-fix-pkg-config-dir.patch
sed -i "s#VCS_URL https://github.com/gazebosim/\${GITHUB_NAME}.git#VCS_TYPE path\n  VCS_URL $_gzsrc#" CMakeLists.txt
sed -i "/^  PATCHES patches$/d" CMakeLists.txt
# sanity: show the rewired ament_vendor call
grep -n -A8 "ament_vendor(" CMakeLists.txt

%build
# The colcon build happens in %%install.

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
    --packages-select gz_cmake_vendor

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
