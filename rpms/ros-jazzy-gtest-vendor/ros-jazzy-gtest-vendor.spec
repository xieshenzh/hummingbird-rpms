Name:           ros-jazzy-gtest-vendor
Version:        1.14.9000
Release:        1%{?dist}
Summary:        Vendored GoogleTest sources for ROS 2 Jazzy

License:        BSD-3-Clause
URL:            https://github.com/ament/googletest
# ament/googletest ships gtest_vendor (googletest/) and gmock_vendor (googlemock/)
# in one repo; tag 1.14.9000 == the jazzy branch head. This spec carves out
# gtest_vendor. It is a build_type=cmake "vendor" package: it compiles nothing
# (project(NONE)) and only installs the GoogleTest source tree + a CMake config,
# to be built in-tree by ament_cmake_gtest at consumer build time. Hence noarch.
Source0:        https://github.com/ament/googletest/archive/refs/tags/%{version}.tar.gz#/%{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  cmake
BuildRequires:  make

%description
Vendored GoogleTest (gtest) source package for ROS 2 Jazzy. Installs the
GoogleTest source and a CMake config under %{_libdir}/ros-jazzy so that
ament_cmake_gtest can compile it per-consumer. Compiles nothing itself.

%prep
%autosetup -n googletest-%{version}

%build
# project(NONE): no compilation, only a configure step to prepare install rules.
cmake -S googletest -B googletest/build \
    -DCMAKE_INSTALL_PREFIX=%{_libdir}/ros-jazzy

%install
DESTDIR=%{buildroot} cmake --install googletest/build

%files
%license LICENSE
# gtest_vendor owns its own subtrees; it has no ROS deps, so it also owns the
# shared prefix dir nodes (harmless double-ownership with ros-jazzy-ament-package).
%dir %{_libdir}/ros-jazzy
%dir %{_libdir}/ros-jazzy/share
%dir %{_libdir}/ros-jazzy/src
%{_libdir}/ros-jazzy/share/gtest_vendor/
%{_libdir}/ros-jazzy/src/gtest_vendor/

%changelog
%autochangelog
