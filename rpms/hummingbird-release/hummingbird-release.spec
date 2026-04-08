%global distro  Hummingbird OS

# Fedora version we're tracking
%global fedora_version 43

Name:           hummingbird-release
Version:        20251124
Release:        1.11%{?dist}
Summary:        %{distro} release files
License:        GPL-2.0-or-later
URL:            https://hummingbird-project.io/
BuildArch:      noarch

Provides:       hummingbird-release = %{version}-%{release}

Requires:       hummingbird-repos
Provides:       hummingbird-release-eula
Provides:       redhat-release-eula

# required by dnf
# https://github.com/rpm-software-management/dnf/blob/4.2.23/dnf/const.py.in#L26
Provides:       system-release = %{version}-%{release}
Conflicts:      system-release

Source200:      EULA
Source201:      LICENSE

Source500:      hummingbird.repo

Source603:      RPM-GPG-KEY-hummingbird-release


%package -n hummingbird-repos
Summary:        Hummingbird package repositories
Provides:       system-repos = %{version}-%{release}
Provides:       hummingbird-repos = %{version}
Requires:       hummingbird-gpg-keys = %{version}-%{release}

%package -n hummingbird-gpg-keys
Summary:        Hummingbird RPM keys


%description
%{distro} release files.

%description -n hummingbird-repos
This package provides the package repository files for Hummingbird.

%description -n hummingbird-gpg-keys
This package provides the RPM signature keys for Hummingbird.


%install
# copy license doc here for %%license and %%doc macros
mkdir -p ./docs
cp %{SOURCE201} ./docs

# create /etc/system-release and /etc/hummingbird-release
install -d -m 0755 %{buildroot}%{_sysconfdir}
echo "%{distro} release" > %{buildroot}%{_sysconfdir}/hummingbird-release
ln -s hummingbird-release %{buildroot}%{_sysconfdir}/system-release
ln -s hummingbird-release %{buildroot}%{_sysconfdir}/redhat-release

# -------------------------------------------------------------------------
# Definitions for /etc/os-release and for macros in macros.dist.  These
# macros are useful for spec files where distribution-specific identifiers
# are used to customize packages.

# Name of vendor / name of distribution. Typically used to identify where
# the binary comes from in --help or --version messages of programs.
# Examples: gdb.spec, clang.spec
%global dist_vendor Red Hat
%global dist_name   %{distro}

# The namespace for purl
# https://github.com/package-url/purl-spec
# for example as in: pkg:rpm/centos/python-setuptools@69.2.0-10.el10?arch=src"
%global dist_purl_namespace hummingbird

# URL of the homepage of the distribution
# Example: gstreamer1-plugins-base.spec
%global dist_home_url https://project-hummingbird.io/

# Bugzilla / bug reporting URLs shown to users.
# Examples: gcc.spec
%global dist_bug_report_url https://issues.redhat.com/

# debuginfod server, as used in elfutils.spec.
%global dist_debuginfod_url https://debuginfod.project-hummingbird.io/
# -------------------------------------------------------------------------


# Create the os-release file
install -d -m 0755 %{buildroot}%{_prefix}/lib
cat > %{buildroot}%{_prefix}/lib/os-release << EOF
NAME="%{dist_name}"
VERSION="%{version}"
VERSION_ID="%{version}"
ID="hummingbird"
ID_LIKE="fedora rhel"
CPE_NAME="cpe:/a:redhat:hummingbird:1"
HOME_URL="%{dist_home_url}"
VENDOR_NAME="%{dist_vendor}"
VENDOR_URL="%{dist_home_url}"
BUG_REPORT_URL="%{dist_bug_report_url}"
PRETTY_NAME="%{distro} %{version}"
EOF

# Create the symlink for /etc/os-release
ln -s ../usr/lib/os-release %{buildroot}%{_sysconfdir}/os-release

# write cpe to /usr/lib/system-release-cpe and symlink to /etc/system-release-cpe
echo "cpe:/a:redhat:hummingbird:1" > %{buildroot}%{_prefix}/lib/system-release-cpe
ln -s ../lib/system-release-cpe %{buildroot}%{_sysconfdir}/system-release-cpe

# create /etc/issue, /etc/issue.net and /etc/issue.d
echo '\S' > %{buildroot}%{_sysconfdir}/issue
echo 'Kernel \r on \m' >> %{buildroot}%{_sysconfdir}/issue
cp %{buildroot}%{_sysconfdir}/issue{,.net}
echo >> %{buildroot}%{_sysconfdir}/issue
mkdir -p %{buildroot}%{_sysconfdir}/issue.d

# set up the dist tag macros
mkdir -p %{buildroot}%{_rpmmacrodir}
cat > %{buildroot}%{_rpmmacrodir}/macros.dist << EOF
# dist macros.

%%__bootstrap ~bootstrap
%%distcore            .hum1
%%dist                %%{!?distprefix0:%%{?distprefix}}%%{expand:%%{lua:for i=0,9999 do print("%%{?distprefix" .. i .."}") end}}%%{distcore}%%{?with_bootstrap:%%{__bootstrap}}%%{?buildrelease:+build%%{buildrelease}}
%%dist_vendor         %{dist_vendor}
%%dist_name           %{dist_name}
%%dist_purl_namespace %{dist_purl_namespace}
%%dist_home_url       %{dist_home_url}
%%dist_bug_report_url %{dist_bug_report_url}
%%dist_debuginfod_url %{dist_debuginfod_url}

# Hummingbird macro - enables %%{?hummingbird} conditionals in spec files
%%hummingbird         1

# Fedora compatibility macros - enables %%{?fedora} conditionals in spec files
%%fedora              %{fedora_version}
%%fc%{fedora_version} 1
EOF

# use unbranded datadir
install -d -m 0755 %{buildroot}%{_datadir}/hummingbird-release
ln -s hummingbird-release %{buildroot}%{_datadir}/redhat-release
install -p -m 0644 %{SOURCE200} %{buildroot}%{_datadir}/hummingbird-release/

# Create yum repos directory and stub hummingbird.repo
install -d -m 0755 %{buildroot}%{_sysconfdir}/yum.repos.d
touch %{buildroot}%{_sysconfdir}/yum.repos.d/hummingbird.repo

# copy yum repos
install -p -m 0644 %{SOURCE500} %{buildroot}%{_sysconfdir}/yum.repos.d/

# copy GPG keys
install -d -m 0755 %{buildroot}%{_sysconfdir}/pki/rpm-gpg
install -p -m 0644 %{SOURCE603} %{buildroot}%{_sysconfdir}/pki/rpm-gpg/


%files
%license docs/LICENSE
%{_sysconfdir}/redhat-release
%{_sysconfdir}/system-release
%{_sysconfdir}/hummingbird-release
%{_sysconfdir}/system-release-cpe
%{_sysconfdir}/os-release
%config(noreplace) %{_sysconfdir}/issue
%config(noreplace) %{_sysconfdir}/issue.net
%dir %{_sysconfdir}/issue.d
%dir %{_sysconfdir}/yum.repos.d
%ghost %{_sysconfdir}/yum.repos.d/hummingbird.repo
%{_rpmmacrodir}/macros.dist
%{_datadir}/redhat-release
%{_datadir}/hummingbird-release
%{_prefix}/lib/os-release
%{_prefix}/lib/system-release-cpe

%files -n hummingbird-repos
%config(noreplace) %{_sysconfdir}/yum.repos.d/hummingbird.repo

%files -n hummingbird-gpg-keys
%{_sysconfdir}/pki/rpm-gpg


%changelog
* Mon Nov 17 2025 Robert Sturla <rsturla@redhat.com> - 0.0.1
- Initial Hummingbird release
