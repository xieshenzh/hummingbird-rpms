Name:           ros-jazzy-rosidl-generator-py
Version:        0.22.2
Release:        1%{?dist}
Summary:        Generate the Python interfaces for ROS interface types

License:        Apache-2.0
URL:            https://github.com/ros2/rosidl_python
Source0:        https://github.com/ros2/rosidl_python/archive/refs/tags/0.22.2.tar.gz#/ros-jazzy-rosidl-generator-py-0.22.2.tar.gz

BuildArch:      noarch

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
BuildRequires:  ros-jazzy-ament-cmake-ros
BuildRequires:  ros-jazzy-ament-cmake
BuildRequires:  ros-jazzy-ament-cmake-python
BuildRequires:  ros-jazzy-ament-index-python
BuildRequires:  ros-jazzy-python-cmake-module
BuildRequires:  ros-jazzy-rosidl-generator-c
BuildRequires:  ros-jazzy-rosidl-pycommon
BuildRequires:  ros-jazzy-rosidl-typesupport-c
BuildRequires:  ros-jazzy-rosidl-typesupport-interface
BuildRequires:  ros-jazzy-rosidl-runtime-c
BuildRequires:  ros-jazzy-rmw
BuildRequires:  python3-numpy

Requires:       ros-jazzy-rpyutils
Requires:       python3-numpy
Requires:       ros-jazzy-ament-cmake
Requires:       ros-jazzy-rosidl-generator-c
Requires:       ros-jazzy-rosidl-typesupport-c
Requires:       ros-jazzy-rosidl-runtime-c
Requires:       ros-jazzy-rmw
Requires:       ros-jazzy-ament-index-python
Requires:       python3
Requires:       ros-jazzy-ament-package

%description
Generate the Python interfaces for ROS interface types. Part of the ROS 2 Jazzy stack, installed under %{_libdir}/ros-jazzy.

%prep
%autosetup -n rosidl_python-0.22.2

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
    --base-paths rosidl_generator_py \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select rosidl_generator_py

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
