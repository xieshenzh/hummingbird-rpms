Summary: Utility to set/show the host name or domain name
Name: hostname
Version: 3.25
Release: 4.2%{?dist}
License: GPL-2.0-or-later
URL: https://tracker.debian.org/pkg/hostname
Source0: https://ftp.debian.org/debian/pool/main/h/hostname/hostname_%{version}.tar.xz
Source1: https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt
Source2: nis-domainname
Source3: nis-domainname.service
BuildRequires: gcc
BuildRequires: make

# NOTE: We are *not* requiring systemd on purpose, because we want to allow
#       hostname package to be installed in containers without the systemd.

# Initial changes
Patch1: hostname-rh.patch

%description
This package provides commands which can be used to display the system's
DNS name, and to display or set its hostname or NIS domain name.

%prep
%setup -q -n hostname
cp %{SOURCE1} %{SOURCE2} %{SOURCE3} .
%patch -P 1 -p1

%build
make CFLAGS="%{optflags} $CFLAGS -D_GNU_SOURCE" LDFLAGS="$RPM_LD_FLAGS"

%install
make DESTDIR=%{buildroot} install

install -m 0755 -d %{buildroot}%{_libexecdir}/%{name}
install -m 0755 -d %{buildroot}%{_prefix}/lib/systemd/system
install -m 0755 nis-domainname         %{buildroot}%{_libexecdir}/%{name}
install -m 0644 nis-domainname.service %{buildroot}%{_prefix}/lib/systemd/system

%post
if [ $1 -eq 1 ]; then
  # Initial installation...
  systemctl --no-reload preset nis-domainname.service &>/dev/null || :
fi

%preun
if [ $1 -eq 0 ]; then
  # Package removal, not upgrade...
  systemctl --no-reload disable --now nis-domainname.service &>/dev/null || :
fi

# NOTE: Nothing to do for upgrade (in postun), nis-domainname.service is oneshot.

%files
%doc COPYRIGHT
%{!?_licensedir:%global license %%doc}
%license gpl-2.0.txt
%{_bindir}/*
%{_mandir}/man1/*
%{_prefix}/lib/systemd/system/*
%{_libexecdir}/%{name}

%changelog
%autochangelog
