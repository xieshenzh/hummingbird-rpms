Name:           ros-jazzy-gz-gui-vendor
Version:        0.0.5
Release:        1%{?dist}
Summary:        Gazebo gz-gui 8.4.0 (Qt5 icu78) vendor.

License:        Apache-2.0
URL:            https://github.com/gazebo-release/gz_gui_vendor
Source0:        https://github.com/gazebo-release/gz_gui_vendor/archive/refs/tags/0.0.5.tar.gz#/ros-jazzy-gz-gui-vendor-0.0.5.tar.gz
Source1:        https://github.com/gazebosim/gz-gui/archive/refs/tags/gz-gui8_8.4.0.tar.gz#/gz-gui-8.4.0.tar.gz

# Vendor ExternalProject builds install prebuilt .so's; skip RPM debuginfo.
%global debug_package %{nil}

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  git
BuildRequires:  patch
BuildRequires:  patchelf
BuildRequires:  pkgconf-pkg-config
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
BuildRequires:  ros-jazzy-ament-cmake
BuildRequires:  ros-jazzy-ament-cmake-vendor-package
BuildRequires:  ros-jazzy-ament-package
BuildRequires:  protobuf-devel
BuildRequires:  tinyxml2-devel
BuildRequires:  zeromq-devel
BuildRequires:  cppzmq-devel
BuildRequires:  qt5-qtbase-devel
BuildRequires:  qt5-qtdeclarative-devel
BuildRequires:  qt5-qtquickcontrols2-devel
BuildRequires:  ros-jazzy-gz-cmake-vendor
BuildRequires:  ros-jazzy-gz-common-vendor
BuildRequires:  ros-jazzy-gz-math-vendor
BuildRequires:  ros-jazzy-gz-msgs-vendor
BuildRequires:  ros-jazzy-gz-plugin-vendor
BuildRequires:  ros-jazzy-gz-rendering-vendor
BuildRequires:  ros-jazzy-gz-transport-vendor
BuildRequires:  ros-jazzy-gz-tools-vendor
BuildRequires:  ros-jazzy-gz-utils-vendor

Requires:       ros-jazzy-ament-package
Requires:       qt5-qtbase
Requires:       qt5-qtdeclarative
Requires:       qt5-qtquickcontrols2
Requires:       ros-jazzy-gz-cmake-vendor
Requires:       ros-jazzy-gz-common-vendor
Requires:       ros-jazzy-gz-math-vendor
Requires:       ros-jazzy-gz-msgs-vendor
Requires:       ros-jazzy-gz-plugin-vendor
Requires:       ros-jazzy-gz-rendering-vendor
Requires:       ros-jazzy-gz-transport-vendor
Requires:       ros-jazzy-gz-tools-vendor
Requires:       ros-jazzy-gz-utils-vendor

%description
Gazebo gz-gui 8.4.0 (Qt5 icu78) vendor. Part of the Gazebo simulation stack for ROS 2 Jazzy, under %{_libdir}/ros-jazzy.

%prep
%autosetup -n gz_gui_vendor-0.0.5
# Offline vendoring: stage upstream, pre-apply vendor patches, rewire
# ament_vendor() to VCS_TYPE path (local source, no network clone in mock).
tar -xzf %{SOURCE1} -C %{_builddir}
_gzsrc="%{_builddir}/$(tar tzf %{SOURCE1} | head -1 | cut -d/ -f1)"
if [ -d patches ]; then for p in patches/*.patch patches/*.diff; do [ -e "$p" ] && patch -d "$_gzsrc" -p1 < "$p"; done; fi
# gcc16 + protobuf 36.2: the generated descriptor accessors name()/full_name()/
# type_name() now return std::string_view, which no longer implicitly converts to
# const std::string&. Wrap them at the TopicViewer call sites (gz-gui8_8.4.0).
sed -i "s#msgField->name()#std::string(msgField->name())#g; s#messageType->full_name()#std::string(messageType->full_name())#g; s#msgField->type_name()#std::string(msgField->type_name())#g" "$_gzsrc/src/plugins/topic_viewer/TopicViewer.cc"
sed -i "s#VCS_URL https://github.com/[^ ]*#VCS_TYPE path\n  VCS_URL $_gzsrc#" CMakeLists.txt
sed -i "/^  PATCHES$/{N;d}" CMakeLists.txt    # multiline PATCHES form (e.g. ogre-next)
sed -i "/^  PATCHES patches$/d" CMakeLists.txt  # inline PATCHES form (e.g. dartsim/rendering)
# POC: DART physics engine omitted (fcl missing in Fedora 44); drop the hard
# gz_dartsim_vendor requirement so gz-physics builds its bullet + TPE engines.
sed -i "/find_package(gz_dartsim_vendor REQUIRED)/d" CMakeLists.txt
sed -i "/^  gz_dartsim_vendor$/d" CMakeLists.txt
# POC: GUI omitted (Fedora Qt5 needs icu77, Hummingbird base ships icu78);
# drop the hard gz_gui_vendor requirement so gz-sim builds headless (server).
sed -i "/find_package(gz_gui_vendor REQUIRED)/d" CMakeLists.txt
sed -i "/^  gz_gui_vendor$/d" CMakeLists.txt
grep -n -A6 "ament_vendor(" CMakeLists.txt || :

%build

%install
_manifest="$PWD/%{name}.files"
export PYTHONUNBUFFERED=1
# gz libs bake CMAKE_INSTALL_PREFIX (which includes the buildroot) into the
# .so as a resource/descriptor fallback path; runtime uses the GZ_*/ament env
# hooks instead. Skip rpmbuild's buildroot-path check for these vendor libs.
export QA_SKIP_BUILD_ROOT=1
source %{_libdir}/ros-jazzy/setup.bash

colcon build \
    --merge-install \
    --cmake-args -DPYTHON_EXECUTABLE="%{__python3}" -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTING=OFF \
    --base-paths . \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select gz_gui_vendor

find %{buildroot}%{_libdir}/ros-jazzy/ -type f ! -name '*.so*' \
    -exec sh -c 'file "$1" | grep -q text && sed -i "s:%{buildroot}::g" "$1"' _ {} \;
rm -rf %{buildroot}%{_libdir}/ros-jazzy/{.catkin,.rosinstall,_setup*,local_setup*,setup*,env.sh,.colcon_install_layout,COLCON_IGNORE,_local_setup*}
find %{buildroot} -type d -name '__pycache__' -exec rm -rf {} +
# Strip the buildroot RPATH the vendor ExternalProject bakes into its .so's
# (else rpmbuild check-buildroot aborts). Keep only rpaths under %{_libdir}.
find %{buildroot}%{_libdir}/ros-jazzy/ -name '*.so*' -type f \
    -exec patchelf --shrink-rpath --allowed-rpath-prefixes %{_libdir} {} \; 2>/dev/null || :

cd %{buildroot}%{_libdir}/ros-jazzy
find . -mindepth 1 \( -type f -o -type l \) -printf '%{_libdir}/ros-jazzy/%%P\n' > "$_manifest"
find . -mindepth 1 -type d \
    ! -path './lib' ! -path './lib/python%{python3_version}' ! -path './lib/python%{python3_version}/site-packages' \
    ! -path './share' ! -path './share/ament_index' ! -path './share/ament_index/resource_index' \
    ! -path './share/ament_index/resource_index/packages' ! -path './share/ament_index/resource_index/package_run_dependencies' \
    ! -path './share/ament_index/resource_index/parent_prefix_path' ! -path './share/colcon-core' ! -path './share/colcon-core/packages' \
    -printf '%%%%dir %{_libdir}/ros-jazzy/%%P\n' >> "$_manifest"
cd - >/dev/null

%files -f %{name}.files

%changelog
%autochangelog
