Name:           ros-jazzy-gmock-vendor
Version:        1.14.9000
Release:        1%{?dist}
Summary:        Vendored GoogleMock sources for ROS 2 Jazzy

License:        BSD-3-Clause
URL:            https://github.com/ament/googletest
# ament/googletest ships gtest_vendor (googletest/) and gmock_vendor (googlemock/)
# in one repo; tag 1.14.9000 == the jazzy branch head. This spec carves out
# gmock_vendor. build_type=cmake vendor package: compiles nothing (project(NONE)),
# only installs the GoogleMock source tree + CMake config. noarch.
Source0:        https://github.com/ament/googletest/archive/refs/tags/%{version}.tar.gz#/%{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  cmake
BuildRequires:  make

# build_export_depend: consumers building against gmock_vendor also need
# gtest_vendor's sources. gtest_vendor owns the shared prefix dir nodes.
Requires:       ros-jazzy-gtest-vendor

%description
Vendored GoogleMock (gmock) source package for ROS 2 Jazzy. Installs the
GoogleMock source and a CMake config under %{_libdir}/ros-jazzy so that
ament_cmake_gmock can compile it per-consumer. Compiles nothing itself.

%prep
%autosetup -n googletest-%{version}

%build
# project(NONE): no compilation, only a configure step to prepare install rules.
cmake -S googlemock -B googlemock/build \
    -DCMAKE_INSTALL_PREFIX=%{_libdir}/ros-jazzy

%install
DESTDIR=%{buildroot} cmake --install googlemock/build

%files
%license LICENSE
%{_libdir}/ros-jazzy/share/gmock_vendor/
%{_libdir}/ros-jazzy/src/gmock_vendor/

%changelog
%autochangelog
