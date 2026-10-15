Name:           ros-jazzy-mcap-vendor
Version:        0.26.11
Release:        1%{?dist}
Summary:        ROS 2 mcap_vendor (MCAP C++ library)

License:        Apache-2.0
URL:            https://github.com/ros2/rosbag2
Source0:        https://github.com/ros2/rosbag2/archive/refs/tags/0.26.11.tar.gz#/ros-jazzy-mcap-vendor-0.26.11.tar.gz
# mcap upstream source, pre-downloaded (mock has no network); FetchContent is
# redirected to this via FETCHCONTENT_SOURCE_DIR_MCAP in %%install.
Source1:        https://github.com/foxglove/mcap/archive/refs/tags/releases/cpp/v1.3.1.tar.gz#/mcap-1.3.1.tar.gz

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  patchelf
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
BuildRequires:  ros-jazzy-ament-cmake
BuildRequires:  ros-jazzy-liblz4-vendor
BuildRequires:  ros-jazzy-zstd-vendor
BuildRequires:  lz4-devel
BuildRequires:  libzstd-devel
BuildRequires:  git

Requires:       ros-jazzy-liblz4-vendor
Requires:       ros-jazzy-zstd-vendor
Requires:       ros-jazzy-ament-package

%description
ROS 2 mcap_vendor (MCAP C++ library). Part of the ROS 2 Jazzy stack, installed under %{_libdir}/ros-jazzy.

%prep
%autosetup -n rosbag2-0.26.11
# Stage the pre-downloaded mcap source and pre-apply the gcc patch, then
# neutralize the in-CMake git-apply so the patch is not applied twice.
tar -xzf %{SOURCE1} -C %{_builddir}
patch -d %{_builddir}/mcap-releases-cpp-v1.3.1 -p3 < mcap_vendor/patches/0001-fix-gcc15.patch
sed -i "s|COMMAND git apply|COMMAND true git apply|" mcap_vendor/CMakeLists.txt
# Some monorepos ship LICENSE per-subdir; stage it for %%license.
[ -f LICENSE ] || cp -f mcap_vendor/LICENSE LICENSE 2>/dev/null || :

%build
# The colcon build happens in %%install.

%install
_manifest="$PWD/%{name}.files"
export PYTHONUNBUFFERED=1
%set_build_flags
source %{_libdir}/ros-jazzy/setup.bash
%py3_shebang_fix .

colcon build \
    --merge-install \
    --cmake-args -DPYTHON_EXECUTABLE="%{__python3}" -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DBUILD_TESTING=OFF \
    -DFETCHCONTENT_SOURCE_DIR_MCAP=%{_builddir}/mcap-releases-cpp-v1.3.1 \
    -DCMAKE_C_FLAGS="${CFLAGS}" \
    -DCMAKE_CXX_FLAGS="${CXXFLAGS}" \
    -DCMAKE_EXE_LINKER_FLAGS="${LDFLAGS}" \
    -DCMAKE_SHARED_LINKER_FLAGS="${LDFLAGS}" \
    --base-paths mcap_vendor \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select mcap_vendor

find %{buildroot}%{_libdir}/ros-jazzy/ -type f ! -name '*.so*' \
    -exec sh -c 'file "$1" | grep -q text && sed -i "s:%{buildroot}::g" "$1"' _ {} \;

rm -rf %{buildroot}%{_libdir}/ros-jazzy/{.catkin,.rosinstall,_setup*,local_setup*,setup*,env.sh,.colcon_install_layout,COLCON_IGNORE,_local_setup*}

find %{buildroot}%{_libdir}/ros-jazzy/ -name '*.so*' -type f \
    -exec patchelf --shrink-rpath --allowed-rpath-prefixes %{_libdir} {} \;

find %{buildroot} -type d -name '__pycache__' -exec rm -rf {} +
for file in $(grep -rIl '^#!.*@PYTHON_EXECUTABLE@.*$' %{buildroot} || :) ; do
    sed -i 's:^#!\s*@PYTHON_EXECUTABLE@\s*:#!%{__python3}:' "$file"
done
%py3_shebang_fix %{buildroot}

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
%license LICENSE

%changelog
%autochangelog
