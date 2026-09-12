Name:           ros-jazzy-foonathan-memory-vendor
Version:        1.4.1
Release:        1%{?dist}
Summary:        Vendor package for the foonathan_memory library (Fast DDS dependency)

License:        Apache-2.0
URL:            https://github.com/eProsima/foonathan_memory_vendor
Source0:        https://github.com/eProsima/foonathan_memory_vendor/archive/refs/tags/v%{version}.tar.gz#/%{name}-%{version}.tar.gz

# noarch: when the system foonathan_memory is present the vendor installs only a
# CMake config that re-exports it — no library is compiled here.
BuildArch:      noarch

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  git
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
# Installs under the ROS prefix; ament-package owns the prefix + setup scripts.
BuildRequires:  ros-jazzy-ament-package
# Their presence makes `cmake --find-package -DNAME=foonathan_memory -DMODE=EXIST`
# succeed, so the vendor takes the "Found foonathan_memory" branch and skips the
# ExternalProject git download (offline build). Fedora ships 0.7.4.
# NOTE: -tools is REQUIRED too: Fedora's foonathan_memory-config.cmake imports a
# target pointing at /usr/bin/nodesize_dbg (shipped by -tools); without it the
# config FATAL_ERRORs, the probe fails, and the vendor falls back to the clone.
BuildRequires:  foonathan-memory-devel
BuildRequires:  foonathan-memory-tools

# Downstream find_package(foonathan_memory_vendor) re-exports foonathan_memory,
# so consumers need its CMake config (in -devel) — and -tools, which that config
# imports — at configure time.
Requires:       foonathan-memory-devel
Requires:       foonathan-memory-tools
Requires:       ros-jazzy-ament-package

%description
foonathan_memory_vendor is an eProsima wrapper around the foonathan_memory
allocator library used by Fast DDS. When a suitable system foonathan_memory is
available it installs a CMake config re-exporting it; otherwise it builds the
library from source. Here it uses Fedora's system foonathan-memory. Installed
under %{_libdir}/ros-jazzy.

%prep
%autosetup -n foonathan_memory_vendor-%{version}

%build
# Vendor CMake config only (system foonathan_memory is used); nothing to compile.

%install
_manifest="$PWD/%{name}.files"
export PYTHONUNBUFFERED=1
source %{_libdir}/ros-jazzy/setup.bash
%py3_shebang_fix .

colcon build \
    --merge-install \
    --cmake-args -DPYTHON_EXECUTABLE="%{__python3}" -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DBUILD_TESTING=OFF \
    --base-paths . \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select foonathan_memory_vendor

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
