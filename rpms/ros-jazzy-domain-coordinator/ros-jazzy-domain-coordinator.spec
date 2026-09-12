Name:           ros-jazzy-domain-coordinator
Version:        0.12.2
Release:        1%{?dist}
Summary:        A tool to coordinate unique ROS_DOMAIN_IDs across multiple processes

License:        Apache-2.0
URL:            https://github.com/ros2/ament_cmake_ros
# ros2/ament_cmake_ros ships ament_cmake_ros, ament_cmake_ros_core and
# domain_coordinator in one repo; this spec carves out domain_coordinator
# (build_type: ament_python) with colcon's --packages-select.
Source0:        https://github.com/ros2/ament_cmake_ros/archive/refs/tags/%{version}.tar.gz#/%{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
BuildRequires:  ros-jazzy-ament-package

Requires:       python3-setuptools
Requires:       ros-jazzy-ament-package

%description
A tool to coordinate unique ROS_DOMAIN_IDs across multiple processes. Used by
the ament isolated-test helpers and launch_testing to run tests in parallel
without interference. Part of ROS 2 Jazzy, installed under %{_libdir}/ros-jazzy.

%prep
%autosetup -n ament_cmake_ros-%{version}

%build
# Pure Python (ament_python); nothing to compile.

%install
_manifest="$PWD/%{name}.files"
export PYTHONUNBUFFERED=1
source %{_libdir}/ros-jazzy/setup.bash
%py3_shebang_fix .

colcon build \
    --merge-install \
    --cmake-args -DPYTHON_EXECUTABLE="%{__python3}" -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DBUILD_TESTING=OFF \
    --base-paths domain_coordinator \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select domain_coordinator

# Strip the buildroot path from generated text files.
find %{buildroot}%{_libdir}/ros-jazzy/ -type f ! -name '*.so*' \
    -exec sh -c 'file "$1" | grep -q text && sed -i "s:%{buildroot}::g" "$1"' _ {} \;

# Prefix-level setup scripts are owned by ros-jazzy-ament-package.
rm -rf %{buildroot}%{_libdir}/ros-jazzy/{.catkin,.rosinstall,_setup*,local_setup*,setup*,env.sh,.colcon_install_layout,COLCON_IGNORE,_local_setup*}

find %{buildroot} -type d -name '__pycache__' -exec rm -rf {} +
%py3_shebang_fix %{buildroot}

# Generate the packaged-file manifest. Shared scaffolding dirs are owned by
# ros-jazzy-ament-package; own everything else.
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
    -printf '%%%%dir %{_libdir}/ros-jazzy/%%P\n' >> "$_manifest"
cd - >/dev/null

%files -f %{name}.files
%license LICENSE

%changelog
%autochangelog
