# Tests fail on i686:
%ifarch %{ix86}
%bcond tests 0
%else
%bcond tests 0
%endif

Name:       pgbouncer
Version:    1.25.2
Release:    1.1%{?dist}
Summary:    Lightweight connection pooler for PostgreSQL
License:    ISC and BSD-2-Clause
URL:        https://www.pgbouncer.org

Source0:    %{url}/downloads/files/%{version}/%{name}-%{version}.tar.gz
Patch0:     %{name}-ini.patch

BuildRequires:  c-ares-devel >= 1.11
BuildRequires:  make
BuildRequires:  gcc
BuildRequires:  openssl-devel
BuildRequires:  pandoc
BuildRequires:  pkgconfig(libevent)

%if %{with tests}
# Test dependencies:
BuildRequires:  openssl
BuildRequires:  postgresql-contrib
BuildRequires:  postgresql-server
BuildRequires:  python3dist(filelock)
BuildRequires:  python3dist(psycopg)
BuildRequires:  python3dist(pytest)
BuildRequires:  python3dist(pytest-asyncio)
BuildRequires:  python3dist(pytest-timeout)
BuildRequires:  python3dist(pytest-xdist)
%endif

Requires:   c-ares >= 1.11

%description
pgbouncer is a lightweight connection pooler for PostgreSQL and uses libevent
for low-level socket handling.

%prep
%autosetup -p1

%build
%configure \
    --enable-debug \
    --with-cares

%make_build V=1

%install
%make_install

# Let RPM pick up docs in the files section
rm -fr %{buildroot}%{_docdir}/%{name}

%if %{with tests}
%check
# Parallel tests fail (make check), run them sequentially:
pytest
%endif

%files
%license COPYRIGHT
%doc NEWS.md README.md doc/*.md
%{_bindir}/%{name}
%{_mandir}/man1/%{name}.*
%{_mandir}/man5/%{name}.*

%changelog
%autochangelog
