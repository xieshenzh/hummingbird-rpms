Name:           ros-jazzy-ament-package
Version:        0.16.5
Release:        1%{?dist}
Summary:        The parser for the manifest files in the ament buildsystem

License:        Apache-2.0
URL:            https://github.com/ament/ament_package
Source0:        https://github.com/ament/ament_package/archive/refs/tags/%{version}.tar.gz#/%{name}-%{version}.tar.gz

# ament_package is pure Python.
BuildArch:      noarch

# colcon (with colcon-ros) drives the build/install and generates the
# prefix-level setup scripts. catkin_pkg parses package.xml.
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command

Requires:       python3
Requires:       python3-setuptools

%description
ament_package provides the parser for the manifest files (package.xml) used by
the ament buildsystem in ROS 2. As the root of the ament build tree, this
package also ships the ROS 2 Jazzy prefix-level environment setup scripts
(setup.bash, setup.sh, local_setup.*, ...) under %{_libdir}/ros-jazzy.

%prep
%autosetup -n ament_package-%{version}

%build
# ament_package is pure Python; nothing to compile here.

%install
export PYTHONUNBUFFERED=1

# Normalise Python shebangs in the source tree before building.
%py3_shebang_fix .

# Build and install into the ROS 2 Jazzy prefix using colcon's merge-install
# layout. This is the step that generates the prefix-level setup scripts
# (setup.bash / setup.sh / local_setup.* / _local_setup_util*.py) under
# %{_libdir}/ros-jazzy.
colcon build \
    --merge-install \
    --cmake-args -DPYTHON_EXECUTABLE="%{__python3}" -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    --base-paths . \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select ament_package

# colcon bakes the buildroot path into COLCON_CURRENT_PREFIX in the generated
# setup scripts; strip it so the installed scripts reference the real prefix.
find %{buildroot}%{_libdir}/ros-jazzy/ -type f \
    -exec sed -i 's:COLCON_CURRENT_PREFIX="%{buildroot}:COLCON_CURRENT_PREFIX=":g' {} \;
find %{buildroot}%{_libdir}/ros-jazzy/ -type f \
    -exec sed -i 's:COLCON_CURRENT_PREFIX=%{buildroot}:COLCON_CURRENT_PREFIX=:g' {} \;

# Replace any leftover @PYTHON_EXECUTABLE@ template shebangs.
for file in $(grep -rIl '^#!.*@PYTHON_EXECUTABLE@.*$' %{buildroot} || :) ; do
    sed -i 's:^#!\s*@PYTHON_EXECUTABLE@\s*:#!%{__python3}:' "$file"
done

# Normalise all remaining Python shebangs in the installed tree.
%py3_shebang_fix %{buildroot}

%files
%license LICENSE
%doc CHANGELOG.rst CONTRIBUTING.md
%{_libdir}/ros-jazzy/

%changelog
%autochangelog