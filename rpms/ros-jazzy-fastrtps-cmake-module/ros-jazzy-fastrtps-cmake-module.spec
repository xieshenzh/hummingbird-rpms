Name:           ros-jazzy-fastrtps-cmake-module
Version:        3.6.4
Release:        1%{?dist}
Summary:        CMake module to find eProsima Fast DDS

License:        Apache-2.0
URL:            https://github.com/ros2/rosidl_typesupport_fastrtps
Source0:        https://github.com/ros2/rosidl_typesupport_fastrtps/archive/refs/tags/3.6.4.tar.gz#/ros-jazzy-fastrtps-cmake-module-3.6.4.tar.gz

BuildArch:      noarch

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
BuildRequires:  ros-jazzy-ament-cmake
BuildRequires:  ros-jazzy-ament-package

Requires:       ros-jazzy-ament-cmake
Requires:       ros-jazzy-ament-package

%description
CMake module to find eProsima Fast DDS. Part of the ROS 2 Jazzy stack, installed under %{_libdir}/ros-jazzy.

%prep
%autosetup -n rosidl_typesupport_fastrtps-3.6.4

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
    --base-paths fastrtps_cmake_module \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select fastrtps_cmake_module

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
