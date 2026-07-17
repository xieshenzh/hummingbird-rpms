Name:           python-poetry-core
Version:        1.4.0
Release:        0%{?dist}
Summary:        Poetry PEP 517 Build Backend

License:        MIT
URL:            https://pypi.org/project/poetry-core/
Source0:        %{pypi_source poetry_core}

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros

%description
Test a build with pyproject.toml backend-path = [.]
poetry-core builds with poetry-core.


%package -n python3-poetry-core
Summary:        %{summary}

%description -n python3-poetry-core
...


%prep
%autosetup -p1 -n poetry_core-%{version}


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install
# the license is not marked as License-File by poetry-core, hence -L
%pyproject_save_files -L poetry

# internal check for our macros, -l must fail:
%pyproject_save_files -l poetry && exit 1 || true

%files -n python3-poetry-core -f %{pyproject_files}
%doc README.md
%license LICENSE
