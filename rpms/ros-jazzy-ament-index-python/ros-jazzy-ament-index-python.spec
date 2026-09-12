Name:           ros-jazzy-ament-index-python
Version:        1.8.4
Release:        1%{?dist}
Summary:        Python API to access the ament resource index

License:        Apache-2.0
URL:            https://github.com/ament/ament_index
# Upstream ships ament_index_cpp and ament_index_python in one tarball; this
# spec carves out ament_index_python (build_type: ament_python).
Source0:        https://github.com/ament/ament_index/archive/refs/tags/%{version}.tar.gz#/%{name}-%{version}.tar.gz

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
ament_index_python provides a Python API to access the ament resource index:
the mechanism ROS 2 packages use to register and discover resources (packages,
plugins, message types, ...) without crawling the filesystem. Installed under
%{_libdir}/ros-jazzy.

%prep
%autosetup -n ament_index-%{version}

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
    --base-paths ament_index_python \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select ament_index_python

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
