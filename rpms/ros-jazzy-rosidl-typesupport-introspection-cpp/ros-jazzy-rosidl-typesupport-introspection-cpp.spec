Name:           ros-jazzy-rosidl-typesupport-introspection-cpp
Version:        4.6.9
Release:        1%{?dist}
Summary:        Generate the C++ interfaces for runtime introspection

License:        Apache-2.0
URL:            https://github.com/ros2/rosidl
Source0:        https://github.com/ros2/rosidl/archive/refs/tags/4.6.9.tar.gz#/ros-jazzy-rosidl-typesupport-introspection-cpp-4.6.9.tar.gz

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  patchelf
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
BuildRequires:  ros-jazzy-ament-cmake-ros
BuildRequires:  ros-jazzy-ament-cmake
BuildRequires:  ros-jazzy-rosidl-generator-c
BuildRequires:  ros-jazzy-rosidl-generator-cpp
BuildRequires:  ros-jazzy-rosidl-pycommon
BuildRequires:  ros-jazzy-rosidl-runtime-cpp
BuildRequires:  ros-jazzy-rosidl-typesupport-introspection-c
BuildRequires:  ros-jazzy-rosidl-cmake
BuildRequires:  ros-jazzy-rosidl-runtime-c
BuildRequires:  ros-jazzy-ament-index-python
BuildRequires:  ros-jazzy-rosidl-cli
BuildRequires:  ros-jazzy-rosidl-parser
BuildRequires:  ros-jazzy-rosidl-typesupport-interface

Requires:       ros-jazzy-ament-cmake
Requires:       python3
Requires:       ros-jazzy-rosidl-generator-c
Requires:       ros-jazzy-rosidl-generator-cpp
Requires:       ros-jazzy-rosidl-pycommon
Requires:       ros-jazzy-rosidl-cmake
Requires:       ros-jazzy-rosidl-runtime-c
Requires:       ros-jazzy-rosidl-runtime-cpp
Requires:       ros-jazzy-ament-index-python
Requires:       ros-jazzy-rosidl-cli
Requires:       ros-jazzy-rosidl-parser
Requires:       ros-jazzy-rosidl-typesupport-interface
Requires:       ros-jazzy-rosidl-typesupport-introspection-c
Requires:       ros-jazzy-ament-package

%description
Generate the C++ interfaces for runtime introspection. Part of the ROS 2 Jazzy rosidl interface-generation stack,
installed under %{_libdir}/ros-jazzy.

%prep
%autosetup -n rosidl-4.6.9

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
    --base-paths rosidl_typesupport_introspection_cpp \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select rosidl_typesupport_introspection_cpp

# Strip the buildroot path from generated text files (never touch ELF).
find %{buildroot}%{_libdir}/ros-jazzy/ -type f ! -name '*.so*' \
    -exec sh -c 'file "$1" | grep -q text && sed -i "s:%{buildroot}::g" "$1"' _ {} \;

# Prefix-level setup scripts are owned by ros-jazzy-ament-package.
rm -rf %{buildroot}%{_libdir}/ros-jazzy/{.catkin,.rosinstall,_setup*,local_setup*,setup*,env.sh,.colcon_install_layout,COLCON_IGNORE,_local_setup*}

# Keep only rpaths inside %{_libdir}; drop baked buildroot rpaths.
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
