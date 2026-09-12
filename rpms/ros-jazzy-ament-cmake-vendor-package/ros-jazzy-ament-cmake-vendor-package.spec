Name:           ros-jazzy-ament-cmake-vendor-package
Version:        2.5.6
Release:        1%{?dist}
Summary:        Macros for maintaining a 'vendor' package in the ament buildsystem

License:        Apache-2.0
URL:            https://github.com/ament/ament_cmake
# Upstream ships all ament_cmake_* subpackages in one monorepo tarball; this
# spec carves out ament_cmake_vendor_package with colcon's --packages-select.
Source0:        https://github.com/ament/ament_cmake/archive/refs/tags/%{version}.tar.gz#/%{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
# buildtool_depend
BuildRequires:  ros-jazzy-ament-cmake-core
BuildRequires:  ros-jazzy-ament-cmake-export-dependencies

# buildtool_export_depend: consumers of ament_vendor() need these at configure
# time (and git/vcstool to fetch VCS sources when a system lib is not found).
Requires:       ros-jazzy-ament-cmake-core
Requires:       ros-jazzy-ament-cmake-export-dependencies
Requires:       git
Requires:       python3-vcstool

%description
ament_cmake_vendor_package provides the ament_vendor() CMake macro used to
maintain 'vendor' packages: it locates a system library and, only if absent,
downloads and builds the upstream source. Part of the ament buildsystem for
ROS 2 Jazzy, installed under %{_libdir}/ros-jazzy.

%prep
%autosetup -n ament_cmake-%{version}

%build
# CMake modules / Python helpers only; nothing to compile.

%install
_manifest="$PWD/%{name}.files"
export PYTHONUNBUFFERED=1
source %{_libdir}/ros-jazzy/setup.bash
%py3_shebang_fix .

colcon build \
    --merge-install \
    --cmake-args -DPYTHON_EXECUTABLE="%{__python3}" -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DBUILD_TESTING=OFF \
    --base-paths ament_cmake_vendor_package \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select ament_cmake_vendor_package

# Strip the buildroot path from generated text files.
find %{buildroot}%{_libdir}/ros-jazzy/ -type f ! -name '*.so*' \
    -exec sh -c 'file "$1" | grep -q text && sed -i "s:%{buildroot}::g" "$1"' _ {} \;

# Prefix-level setup scripts are owned by ros-jazzy-ament-package.
rm -rf %{buildroot}%{_libdir}/ros-jazzy/{.catkin,.rosinstall,_setup*,local_setup*,setup*,env.sh,.colcon_install_layout,COLCON_IGNORE,_local_setup*}

find %{buildroot} -type d -name '__pycache__' -exec rm -rf {} +
for file in $(grep -rIl '^#!.*@PYTHON_EXECUTABLE@.*$' %{buildroot} || :) ; do
    sed -i 's:^#!\s*@PYTHON_EXECUTABLE@\s*:#!%{__python3}:' "$file"
done
%py3_shebang_fix %{buildroot}

# Generate the packaged-file manifest. Shared scaffolding dirs are owned by
# ros-jazzy-ament-package / ros-jazzy-ament-cmake-core; own everything else.
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
    ! -path './share/colcon-core' \
    ! -path './share/colcon-core/packages' \
    -printf '%%%%dir %{_libdir}/ros-jazzy/%%P\n' >> "$_manifest"
cd - >/dev/null

%files -f %{name}.files
%license LICENSE

%changelog
%autochangelog
