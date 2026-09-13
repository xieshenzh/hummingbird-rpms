Name:           ros-jazzy-rclpy
Version:        7.1.12
Release:        1%{?dist}
Summary:        The ROS 2 client library in Python

License:        Apache-2.0
URL:            https://github.com/ros2/rclpy
Source0:        https://github.com/ros2/rclpy/archive/refs/tags/7.1.12.tar.gz#/ros-jazzy-rclpy-7.1.12.tar.gz

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  patchelf
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
BuildRequires:  ros-jazzy-ament-cmake
BuildRequires:  ros-jazzy-python-cmake-module
BuildRequires:  ros-jazzy-pybind11-vendor
BuildRequires:  pybind11-devel
BuildRequires:  ros-jazzy-rcpputils
BuildRequires:  ros-jazzy-rcutils
BuildRequires:  ros-jazzy-rmw-implementation-cmake
BuildRequires:  ros-jazzy-lifecycle-msgs
BuildRequires:  ros-jazzy-rcl
BuildRequires:  ros-jazzy-rcl-action
BuildRequires:  ros-jazzy-rcl-interfaces
BuildRequires:  ros-jazzy-rcl-lifecycle
BuildRequires:  ros-jazzy-rcl-logging-interface
BuildRequires:  ros-jazzy-rcl-yaml-param-parser
BuildRequires:  ros-jazzy-rmw
BuildRequires:  ros-jazzy-rmw-implementation
BuildRequires:  ros-jazzy-rosidl-runtime-c
BuildRequires:  ros-jazzy-unique-identifier-msgs
BuildRequires:  openssl-devel
BuildRequires:  tinyxml2-devel
BuildRequires:  asio-devel
BuildRequires:  lttng-ust-devel

Requires:       ros-jazzy-lifecycle-msgs
Requires:       ros-jazzy-rcl
Requires:       ros-jazzy-rcl-action
Requires:       ros-jazzy-rcl-interfaces
Requires:       ros-jazzy-rcl-lifecycle
Requires:       ros-jazzy-rcl-logging-interface
Requires:       ros-jazzy-rcl-yaml-param-parser
Requires:       ros-jazzy-rmw
Requires:       ros-jazzy-rmw-implementation
Requires:       ros-jazzy-rosidl-runtime-c
Requires:       ros-jazzy-unique-identifier-msgs
Requires:       ros-jazzy-action-msgs
Requires:       ros-jazzy-ament-index-python
Requires:       ros-jazzy-builtin-interfaces
Requires:       ros-jazzy-rosgraph-msgs
Requires:       ros-jazzy-rpyutils
Requires:       ros-jazzy-rcpputils
Requires:       ros-jazzy-rcutils
Requires:       python3-pyyaml
Requires:       ros-jazzy-ament-package

%description
The ROS 2 client library in Python. Part of the ROS 2 Jazzy stack, installed under %{_libdir}/ros-jazzy.

%prep
%autosetup -n rclpy-7.1.12

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
    -DCMAKE_C_FLAGS="${CFLAGS}" \
    -DCMAKE_CXX_FLAGS="${CXXFLAGS}" \
    -DCMAKE_EXE_LINKER_FLAGS="${LDFLAGS}" \
    -DCMAKE_SHARED_LINKER_FLAGS="${LDFLAGS}" \
    --base-paths rclpy \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select rclpy

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
