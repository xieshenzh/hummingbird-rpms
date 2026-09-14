Name:           ros-jazzy-ros-core
Version:        0.11.0
Release:        1%{?dist}
Summary:        ROS 2 ros_core variant metapackage (REP 2001)

License:        Apache-2.0
URL:            https://github.com/ros2/variants
Source0:        https://github.com/ros2/variants/archive/refs/tags/0.11.0.tar.gz#/ros-jazzy-ros-core-0.11.0.tar.gz

BuildArch:      noarch

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
BuildRequires:  ros-jazzy-ament-cmake

Requires:       ros-jazzy-ament-cmake
Requires:       ros-jazzy-ament-cmake-auto
Requires:       ros-jazzy-ament-cmake-gtest
Requires:       ros-jazzy-ament-cmake-gmock
Requires:       ros-jazzy-ament-cmake-pytest
Requires:       ros-jazzy-ament-cmake-ros
Requires:       ros-jazzy-ament-index-cpp
Requires:       ros-jazzy-ament-index-python
Requires:       ros-jazzy-ament-lint-auto
Requires:       ros-jazzy-ament-lint-common
Requires:       ros-jazzy-rcl-lifecycle
Requires:       ros-jazzy-rclcpp
Requires:       ros-jazzy-rclcpp-action
Requires:       ros-jazzy-rclcpp-lifecycle
Requires:       ros-jazzy-rclpy
Requires:       ros-jazzy-rosidl-default-generators
Requires:       ros-jazzy-rosidl-default-runtime
Requires:       ros-jazzy-ros-environment
Requires:       ros-jazzy-common-interfaces
Requires:       ros-jazzy-launch
Requires:       ros-jazzy-launch-testing
Requires:       ros-jazzy-launch-testing-ament-cmake
Requires:       ros-jazzy-launch-xml
Requires:       ros-jazzy-launch-yaml
Requires:       ros-jazzy-launch-ros
Requires:       ros-jazzy-launch-testing-ros
Requires:       ros-jazzy-ros2launch
Requires:       ros-jazzy-ros2cli-common-extensions
Requires:       ros-jazzy-sros2
Requires:       ros-jazzy-sros2-cmake
Requires:       ros-jazzy-class-loader
Requires:       ros-jazzy-pluginlib
Requires:       ros-jazzy-ament-package

%description
ROS 2 ros_core variant metapackage (REP 2001). Part of the ROS 2 Jazzy stack, installed under %{_libdir}/ros-jazzy.

%prep
%autosetup -n variants-0.11.0
# Some monorepos ship LICENSE per-subdir; stage it for %%license.
[ -f LICENSE ] || cp -f ros_core/LICENSE LICENSE 2>/dev/null || :

%build
# The colcon build happens in %%install.

%install
_manifest="$PWD/%{name}.files"
export PYTHONUNBUFFERED=1
source %{_libdir}/ros-jazzy/setup.bash
%py3_shebang_fix .

colcon build \
    --merge-install \
    --cmake-args -DPYTHON_EXECUTABLE="%{__python3}" -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DBUILD_TESTING=OFF \
    --base-paths ros_core \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select ros_core

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
%license LICENSE

%changelog
%autochangelog
