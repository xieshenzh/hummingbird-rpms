Name:           ros-jazzy-ros-gz-image
Version:        1.0.25
Release:        1%{?dist}
Summary:        ROS 2 ros_gz_image.

License:        Apache-2.0
URL:            https://github.com/gazebosim/ros_gz
Source0:        https://github.com/gazebosim/ros_gz/archive/refs/tags/1.0.25.tar.gz#/ros-jazzy-ros-gz-image-1.0.25.tar.gz

# Vendor packages skip debuginfo; regular ament pkgs keep it (default).
BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  patchelf
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
BuildRequires:  ros-jazzy-ament-cmake
BuildRequires:  protobuf-devel
BuildRequires:  cppzmq-devel
BuildRequires:  zeromq-devel
BuildRequires:  ros-jazzy-gz-cmake-vendor
BuildRequires:  qt5-qtbase-devel
BuildRequires:  qt5-qtdeclarative-devel
BuildRequires:  qt5-qtquickcontrols2-devel
BuildRequires:  openssl-devel
BuildRequires:  asio-devel
BuildRequires:  lttng-ust-devel
BuildRequires:  tinyxml2-devel
BuildRequires:  libuuid-devel
BuildRequires:  pkgconf-pkg-config
BuildRequires:  ros-jazzy-image-transport
BuildRequires:  ros-jazzy-ros-gz-bridge
BuildRequires:  ros-jazzy-rclcpp
BuildRequires:  ros-jazzy-sensor-msgs
BuildRequires:  ros-jazzy-gz-msgs-vendor
BuildRequires:  ros-jazzy-gz-transport-vendor

Requires:       ros-jazzy-image-transport
Requires:       ros-jazzy-ros-gz-bridge
Requires:       ros-jazzy-rclcpp
Requires:       ros-jazzy-sensor-msgs
Requires:       ros-jazzy-gz-msgs-vendor
Requires:       ros-jazzy-gz-transport-vendor

Requires:       ros-jazzy-ament-package

%description
ROS 2 ros_gz_image. Part of the ROS 2 Jazzy stack, installed under %{_libdir}/ros-jazzy.

%prep
%autosetup -n ros_gz-1.0.25
# Some monorepos ship LICENSE per-subdir; stage it for %%license.
[ -f LICENSE ] || cp -f ros_gz_image/LICENSE LICENSE 2>/dev/null || :
sed -i '0,/^find_package(gz_/s##list(APPEND CMAKE_MODULE_PATH "/usr/lib64/ros-jazzy/opt/gz_cmake_vendor/share/cmake/gz-cmake3/cmake3")\nlist(PREPEND CMAKE_PREFIX_PATH "/usr/lib64/ros-jazzy/opt/gz_cmake_vendor")\nfind_package(gz-cmake3 REQUIRED)\nfind_package(CPPZMQ REQUIRED)\nfind_package(gz_#' ros_gz_image/CMakeLists.txt


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
    --base-paths ros_gz_image \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select ros_gz_image

find %{buildroot}%{_libdir}/ros-jazzy/ -type f ! -name '*.so*' \
    -exec sh -c 'file "$1" | grep -q text && sed -i "s:%{buildroot}::g" "$1"' _ {} \;

rm -rf %{buildroot}%{_libdir}/ros-jazzy/{.catkin,.rosinstall,_setup*,local_setup*,setup*,env.sh,.colcon_install_layout,COLCON_IGNORE,_local_setup*}

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


%changelog
%autochangelog
