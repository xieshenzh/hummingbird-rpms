Name:           python-httpbin
Version:        0.7.0
Release:        0%{?dist}
Summary:        HTTP Request & Response Service, written in Python + Flask
License:        MIT
URL:            https://github.com/Runscope/httpbin
Source0:        %{url}/archive/v%{version}/httpbin-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros

%if 0%{?rhel} != 9
# Wekrzeug in Fedora and EL10+ isn't compatible with our httpbin
Patch:          python-httpbin-werkzeug-2.1.patch
%endif

# no flask, itsdangerous, raven, werkzeug packaged for EPEL 9 yet
# cannot run tests on EPEL and also cannot BuildRequire runtime deps
%if 0%{?fedora}
%bcond_without tests
%else
%bcond_with tests
%endif

%description
This package buildrequires a package with extra: raven[flask].


%package -n python3-httpbin
Summary:            %{summary}

%description -n python3-httpbin
%{summary}.


%prep
%autosetup -n httpbin-%{version} -p1

# brotlipy wrapper is not packaged, httpbin works fine with brotli
sed -i s/brotlipy/brotli/ setup.py

# update test_httpbin.py to reflect new behavior of werkzeug
sed -i /Content-Length/d test_httpbin.py

# https://github.com/postmanlabs/httpbin/issues/647
sed -Ei 's/\bdef (test_(relative_)?redirect_(to_post|n_(equals_to|higher_than)_1))/def no\1/' test_httpbin.py

%generate_buildrequires
%pyproject_buildrequires %{?with_tests:-t}%{!?with_tests:-R}


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files -l httpbin


%if %{with tests}
%check
%if 0%{?rhel} == 9
# this version of httpbin is not compatible with werkzeug 3+
%tox
%endif

# Internal check for our macros
# The runtime dependencies contain raven[flask], we assert we got them.
# The %%tox above also dies without it, but this makes it more explicit
%{python3} -c 'import blinker, flask'  # transitive deps
%endif


%files -n python3-httpbin -f %{pyproject_files}
%doc README*
