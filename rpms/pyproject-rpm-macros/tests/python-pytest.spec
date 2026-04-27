%global pypi_name pytest
Name:           python-%{pypi_name}
Version:        8.0.2
Release:        0%{?dist}
Summary:        Simple powerful testing with Python
License:        MIT
URL:            https://pytest.org
Source0:        %{pypi_source}

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros

# no xmlschema packaged for EPEL 10 yet, cannot run tests on EPEL
%if 0%{?fedora}
%bcond_without tests
%else
%bcond_with tests
%endif

%description
This is a pure Python package with executables. It has a test suite in tox.ini
and test dependencies specified via the [testing] extra.
Building this tests:
- generating runtime and test dependencies by both tox.ini and extras
- pyproject.toml with the setuptools backend and setuptools-scm
- passing arguments into %%tox

%package -n python3-%{pypi_name}
Summary:        %{summary}

%description -n python3-%{pypi_name}
%{summary}.


%prep
%autosetup -p1 -n %{pypi_name}-%{version}
# remove optional test dependencies we don't like to pull in
%pyproject_patch_dependency mock:ignore
%pyproject_patch_dependency nose:ignore


%generate_buildrequires
%pyproject_buildrequires %{?with_tests:-x testing -t}


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files -l '*pytest' py +auto


%check
%if %{with tests}
# Only run one test (which uses a test-only dependency, hypothesis)
# See how to pass options trough the macro to tox, trough tox to pytest
%tox -- -- -k "metafunc and not test_parametrize_" -Wdefault
%else
%pyproject_check_import
%endif


%files -n python3-%{pypi_name} -f %{pyproject_files}
%doc README.rst
%doc CHANGELOG.rst
