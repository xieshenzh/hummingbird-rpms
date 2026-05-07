Name:           python-markdown-it-py
Version:        3.0.0
Release:        0%{?dist}
Summary:        Python port of markdown-it
License:        MIT
URL:            https://github.com/executablebooks/markdown-it-py
Source0:        %{url}/archive/v%{version}/markdown-it-py-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel

%description
This package tests generating of runtime requirements from pyproject.toml
Upstream has got many more extras than we package,
so it's a good example to test it's filtered correctly.

%package -n     python3-markdown-it-py
Summary:        %{summary}

%description -n python3-markdown-it-py
...

%pyproject_extras_subpkg -n python3-markdown-it-py linkify

%prep
%autosetup -p1 -n markdown-it-py-%{version}

%generate_buildrequires
%pyproject_buildrequires --extras testing,linkify --pyproject-dependencies

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files markdown_it --no-assert-license

%check
# sphinx-copybutton is in [rtd] extra, should not appear
grep "python3dist(sphinx-copybutton)" %_pyproject_buildrequires  && exit 1 || true
#  "pytest-benchmark" is in [benchmarking] extra, should not appear
grep "python3dist(pytest-benchmark)" %_pyproject_buildrequires  && exit 1 || true
# "pytest-regressions" is in [testing] extra, should appear
grep "python3dist(pytest-regressions)" %_pyproject_buildrequires
# "linkify-it-py" is in [linkify] extra, should appear
grep "python3dist(linkify-it-py)" %_pyproject_buildrequires


%files -n python3-markdown-it-py -f %{pyproject_files}
%{_bindir}/markdown-it
