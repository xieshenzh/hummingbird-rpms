Name:           python-coverage-pth
Version:        0.0.2
Release:        0%{?dist}
Summary:        Coverage PTH file to enable coverage at the virtualenv level
License:        BSD-2-Clause
URL:            https://github.com/dougn/coverage_pth
Source:         %{pypi_source coverage_pth}

BuildArch:      noarch
BuildRequires:  python3-devel

%description
This package exists to test %%pyproject_save_files -M.
It contains no Python modules, just a single .pth file.


%package -n     python3-coverage-pth
Summary:        %{summary}

%description -n python3-coverage-pth
...


%prep
%autosetup -p1 -n coverage_pth-%{version}
# support multi-digit Python versions in setup.py regexes
sed -i 's/d)/d+)/' setup.py


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install

# internal check for our macros:
# this should not work without -M
%pyproject_save_files --no-assert-license && exit 1 || true

# but this should:
%pyproject_save_files --no-assert-license --allow-no-modules


%files -n python3-coverage-pth -f %{pyproject_files}
%{python3_sitelib}/coverage_pth.pth
