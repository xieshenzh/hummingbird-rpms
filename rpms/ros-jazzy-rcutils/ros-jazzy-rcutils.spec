Name:           ros-jazzy-rcutils
Version:        6.7.6
Release:        1%{?dist}
Summary:        Common C functions and data structures used in ROS 2

License:        Apache-2.0
URL:            https://github.com/ros2/rcutils
Source0:        https://github.com/ros2/rcutils/archive/refs/tags/%{version}.tar.gz#/%{name}-%{version}.tar.gz

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  patchelf
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
# buildtool_depend: ament_cmake_ros (pulls ament_cmake_gtest/gmock/pytest, which
# ament_cmake_ros-extras.cmake find_package(... REQUIRED)s at configure) + empy.
BuildRequires:  ros-jazzy-ament-cmake-ros
BuildRequires:  ros-jazzy-ament-package
BuildRequires:  python3-empy
# depend: libatomic (rcutils uses C11 atomics; -latomic on this arch).
BuildRequires:  libatomic

Requires:       ros-jazzy-ament-cmake
Requires:       ros-jazzy-ament-package
Requires:       libatomic

%description
rcutils provides common C functions and data structures used throughout the
ROS 2 codebase: logging, error handling, string and filesystem helpers,
time, and portable macros. Installed under %{_libdir}/ros-jazzy.

%prep
%autosetup -n rcutils-%{version}

%build
# The compile happens in %%install via colcon; propagate the build flags there.

%install
_manifest="$PWD/%{name}.files"
export PYTHONUNBUFFERED=1

# Propagate Fedora's hardening / optimization flags into the CMake compile.
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
    --base-paths . \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select rcutils

# Strip the buildroot path from generated text files (never touch ELF).
find %{buildroot}%{_libdir}/ros-jazzy/ -type f ! -name '*.so*' \
    -exec sh -c 'file "$1" | grep -q text && sed -i "s:%{buildroot}::g" "$1"' _ {} \;

# Prefix-level setup scripts are owned by ros-jazzy-ament-package.
rm -rf %{buildroot}%{_libdir}/ros-jazzy/{.catkin,.rosinstall,_setup*,local_setup*,setup*,env.sh,.colcon_install_layout,COLCON_IGNORE,_local_setup*}

# Keep only rpaths that point inside %{_libdir}; drop baked buildroot rpaths.
find %{buildroot}%{_libdir}/ros-jazzy/ -name '*.so*' -type f \
    -exec patchelf --shrink-rpath --allowed-rpath-prefixes %{_libdir} {} \;

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
