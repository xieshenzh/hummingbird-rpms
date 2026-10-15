Name:           ros-jazzy-rcl-logging-spdlog
Version:        3.1.1
Release:        1%{?dist}
Summary:        spdlog-based logging backend for rcl

License:        Apache-2.0
URL:            https://github.com/ros2/rcl_logging
Source0:        https://github.com/ros2/rcl_logging/archive/refs/tags/3.1.1.tar.gz#/ros-jazzy-rcl-logging-spdlog-3.1.1.tar.gz

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  patchelf
BuildRequires:  python3-devel
BuildRequires:  python3-colcon-common-extensions
BuildRequires:  python3-catkin_pkg
BuildRequires:  python-unversioned-command
BuildRequires:  ros-jazzy-ament-cmake-ros
BuildRequires:  ros-jazzy-spdlog-vendor
BuildRequires:  spdlog-devel
BuildRequires:  ros-jazzy-rcl-logging-interface
BuildRequires:  ros-jazzy-rcpputils
BuildRequires:  ros-jazzy-rcutils
BuildRequires:  ros-jazzy-ament-package

Requires:       ros-jazzy-rcl-logging-interface
Requires:       ros-jazzy-rcpputils
Requires:       ros-jazzy-rcutils
Requires:       ros-jazzy-spdlog-vendor
Requires:       spdlog-devel
Requires:       ros-jazzy-ament-package

%description
spdlog-based logging backend for rcl. Part of the ROS 2 Jazzy stack, installed under %{_libdir}/ros-jazzy.

%prep
%autosetup -n rcl_logging-3.1.1
# --- Hummingbird fix: avoid spdlog's GLOBAL REGISTRY in the logging backend ---
# rcl_logging_external_shutdown() calls spdlog::drop("root"), which dereferences
# spdlog's global registry singleton. When a ROS 2 C++ process exits WITHOUT a
# clean rclcpp::shutdown() (e.g. `ros2 bag play` failing on a missing converter),
# that call runs from the libc atexit / rclcpp::Context destructor path AFTER the
# registry singleton's own static destructor has run -> use-after-free in
# spdlog::details::registry::drop (nondeterministic SIGSEGV/SIGABRT). It bites on
# spdlog 1.17 (Hummingbird) though the upstream source is unchanged. Fix: keep the
# root logger ONLY in the file-static g_root_logger and never put it in the global
# registry, so shutdown touches no registry. This also removes the duplicate-name
# throw on re-initialize. See docs/ros-base-rpms.md.
_s=rcl_logging_spdlog/src/rcl_logging_spdlog.cpp
# flush_every() is a registry-global periodic flush that no longer reaches our
# (unregistered) logger -> drop it; flush explicitly on shutdown instead.
sed -i '/spdlog::flush_every(std::chrono::seconds(5));/d' "$_s"
sed -i '/spdlog::register_logger(g_root_logger);/d' "$_s"
sed -i 's|spdlog::drop("root");|if (g_root_logger) { g_root_logger->flush(); }|' "$_s"

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
    --base-paths rcl_logging_spdlog \
    --install-base %{buildroot}%{_libdir}/ros-jazzy/ \
    --packages-select rcl_logging_spdlog

find %{buildroot}%{_libdir}/ros-jazzy/ -type f ! -name '*.so*' \
    -exec sh -c 'file "$1" | grep -q text && sed -i "s:%{buildroot}::g" "$1"' _ {} \;

rm -rf %{buildroot}%{_libdir}/ros-jazzy/{.catkin,.rosinstall,_setup*,local_setup*,setup*,env.sh,.colcon_install_layout,COLCON_IGNORE,_local_setup*}

find %{buildroot}%{_libdir}/ros-jazzy/ -name '*.so*' -type f \
    -exec patchelf --shrink-rpath --allowed-rpath-prefixes %{_libdir} {} \;

find %{buildroot} -type d -name '__pycache__' -exec rm -rf {} +
for file in $(grep -rIl '^#!.*@PYTHON_EXECUTABLE@.*$' %{buildroot} || :) ; do
    sed -i 's:^#!\s*@PYTHON_EXECUTABLE@\s*:#!%{__python3}:' "$file"
done
%py3_shebang_fix %{buildroot}

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
%license LICENSE

%changelog
%autochangelog
