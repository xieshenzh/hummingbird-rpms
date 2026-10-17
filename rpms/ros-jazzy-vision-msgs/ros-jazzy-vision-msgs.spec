Name:           ros-jazzy-vision-msgs
Version:        4.1.1
Release:        1%{?dist}
Summary:        ROS 2 vision_msgs.

License:        Apache-2.0
URL:            https://github.com/ros-perception/vision_msgs
Source0:        https://github.com/ros-perception/vision_msgs/archive/refs/tags/4.1.1.tar.gz#/ros-jazzy-vision-msgs-4.1.1.tar.gz

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
BuildRequires:  ros-jazzy-ament-cmake-ros
BuildRequires:  ros-jazzy-rosidl-default-generators
BuildRequires:  ros-jazzy-python-cmake-module
BuildRequires:  python3-empy
BuildRequires:  ros-jazzy-std-msgs
BuildRequires:  ros-jazzy-geometry-msgs

Requires:       ros-jazzy-rosidl-default-runtime
Requires:       ros-jazzy-std-msgs
Requires:       ros-jazzy-geometry-msgs

Requires:       ros-jazzy-ament-package

%description
ROS 2 vision_msgs. Part of the ROS 2 Jazzy stack, installed under %{_libdir}/ros-jazzy.

%prep
%autosetup -n vision_msgs-4.1.1
# Some monorepos ship LICENSE per-subdir; stage it for %%license.
[ -f LICENSE ] || cp -f vision_msgs/LICENSE LICENSE 2>/dev/null || :



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
    --base-paths vision_msgs \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select vision_msgs

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
