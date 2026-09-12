Name:           ros-jazzy-fastcdr
Version:        2.2.8
Release:        1%{?dist}
Summary:        eProsima Fast CDR serialization library for ROS 2

License:        Apache-2.0
URL:            https://github.com/eProsima/Fast-CDR
Source0:        https://github.com/eProsima/Fast-CDR/archive/refs/tags/v%{version}.tar.gz#/%{name}-%{version}.tar.gz

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  patchelf
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
# Installs under the ROS prefix; ament-package owns the prefix + setup scripts.
BuildRequires:  ros-jazzy-ament-package

Requires:       ros-jazzy-ament-package

%description
eProsima Fast CDR is a C++ library implementing the Common Data Representation
(CDR) serialization defined by the OMG — the mechanism DDS uses for its
interoperability wire protocol (DDSI-RTPS). It is a plain CMake package (no ROS
middleware dependencies) built here under %{_libdir}/ros-jazzy for the Fast DDS
stack.

%prep
%autosetup -n Fast-CDR-%{version}

%build
# The compile happens in %%install via colcon; propagate the build flags there.

%install
_manifest="$PWD/%{name}.files"
export PYTHONUNBUFFERED=1

# Propagate Fedora's hardening / optimization flags into the CMake compile.
%set_build_flags

source %{_libdir}/ros-jazzy/setup.bash

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
    --packages-select fastcdr

# Strip the buildroot path from generated text files (never touch ELF).
find %{buildroot}%{_libdir}/ros-jazzy/ -type f ! -name '*.so*' \
    -exec sh -c 'file "$1" | grep -q text && sed -i "s:%{buildroot}::g" "$1"' _ {} \;

# Prefix-level setup scripts are owned by ros-jazzy-ament-package.
rm -rf %{buildroot}%{_libdir}/ros-jazzy/{.catkin,.rosinstall,_setup*,local_setup*,setup*,env.sh,.colcon_install_layout,COLCON_IGNORE,_local_setup*}

# Keep only rpaths that point inside %{_libdir}; drop baked buildroot rpaths.
find %{buildroot}%{_libdir}/ros-jazzy/ -name '*.so*' -type f \
    -exec patchelf --shrink-rpath --allowed-rpath-prefixes %{_libdir} {} \;

find %{buildroot} -type d -name '__pycache__' -exec rm -rf {} +

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
    ! -path './share/colcon-core' \
    ! -path './share/colcon-core/packages' \
    -printf '%%%%dir %{_libdir}/ros-jazzy/%%P\n' >> "$_manifest"
cd - >/dev/null

%files -f %{name}.files
%license LICENSE

%changelog
%autochangelog
