Name:           ros-jazzy-ament-cmake-core
Version:        2.5.6
Release:        1%{?dist}
Summary:        The core of the ament buildsystem in CMake

License:        Apache-2.0
URL:            https://github.com/ament/ament_cmake
# Upstream ships all ament_cmake_* subpackages in one monorepo tarball; this
# spec carves out ament_cmake_core with colcon's --packages-select.
Source0:        https://github.com/ament/ament_cmake/archive/refs/tags/%{version}.tar.gz#/%{name}-%{version}.tar.gz

# Pure CMake/Python macros; no compiled artifacts.
BuildArch:      noarch

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
BuildRequires:  ros-jazzy-ament-package

Requires:       cmake
Requires:       python3-catkin_pkg
Requires:       ros-jazzy-ament-package

%description
The core of the ament buildsystem in CMake. Several subcomponents provide
specific functionality: environment (prefix-level setup files), environment
hooks (package-level setup files), index (store and retrieve information without
crawling), package templates (from the ament_package Python package) and symlink
install (use symlinks for CMake install commands).

%prep
%autosetup -n ament_cmake-%{version}

%build
# ament_cmake_core ships CMake modules and Python helpers; nothing to compile.

%install
export PYTHONUNBUFFERED=1

# ament_cmake_core builds against the prefix-level scripts owned by
# ros-jazzy-ament-package.
source %{_libdir}/ros-jazzy/setup.bash

%py3_shebang_fix .

colcon build \
    --merge-install \
    --cmake-args -DPYTHON_EXECUTABLE="%{__python3}" -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DBUILD_TESTING=OFF \
    --base-paths ament_cmake_core \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select ament_cmake_core

# Strip the buildroot path from any generated text files.
find %{buildroot}%{_libdir}/ros-jazzy/ -type f ! -name '*.so*' \
    -exec sh -c 'file "$1" | grep -q text && sed -i "s:%{buildroot}::g" "$1"' _ {} \;

# The prefix-level setup scripts are owned by ros-jazzy-ament-package; drop the
# copies colcon regenerates here so the two packages do not conflict.
rm -rf %{buildroot}%{_libdir}/ros-jazzy/{.catkin,.rosinstall,_setup*,local_setup*,setup*,env.sh,.colcon_install_layout,COLCON_IGNORE,_local_setup*}

# Drop bytecode and template shebangs.
find %{buildroot} -type d -name '__pycache__' -exec rm -rf {} +
for file in $(grep -rIl '^#!.*@PYTHON_EXECUTABLE@.*$' %{buildroot} || :) ; do
    sed -i 's:^#!\s*@PYTHON_EXECUTABLE@\s*:#!%{__python3}:' "$file"
done
%py3_shebang_fix %{buildroot}

%files
%license LICENSE
%{_libdir}/ros-jazzy/share/ament_cmake_core/
%{_libdir}/ros-jazzy/share/ament_index/resource_index/packages/ament_cmake_core
%{_libdir}/ros-jazzy/share/colcon-core/packages/ament_cmake_core
# ament-package owns .../resource_index/packages but not these two marker dirs;
# ament_cmake_core is the first (and a universal transitive dep) to create them,
# so it owns them for the rest of the ament_cmake tier.
%dir %{_libdir}/ros-jazzy/share/ament_index/resource_index/package_run_dependencies
%{_libdir}/ros-jazzy/share/ament_index/resource_index/package_run_dependencies/ament_cmake_core
%dir %{_libdir}/ros-jazzy/share/ament_index/resource_index/parent_prefix_path
%{_libdir}/ros-jazzy/share/ament_index/resource_index/parent_prefix_path/ament_cmake_core

%changelog
%autochangelog