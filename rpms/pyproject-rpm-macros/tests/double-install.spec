Name:           double-install
Version:        0
Release:        0%{?dist}
Summary:        Install 2 wheels with extras subpackages
License:        MIT
%global         markdown_it_py_version 3.0.0
%global         setuptools_scm_version 6.3.2
Source1:        https://github.com/executablebooks/markdown-it-py/archive/v%{markdown_it_py_version}/markdown-it-py-%{markdown_it_py_version}.tar.gz
Source2:        %{pypi_source setuptools_scm %{setuptools_scm_version}}

BuildArch:      noarch
BuildRequires:  python3-devel

%description
This package tests that we can build and install 2 wheels at once.
This also tests the -d option for %%pyproject_buildrequires/%%pyproject_wheel
and the -D option for %%pyproject_save_files/%%pyproject_check_import/%%pyproject_extras_subpkg.


%package -n python3-double-markdown-it-py
Summary:        markdown-it-py from double-install test

%description -n python3-double-markdown-it-py
markdown-it-py subpackage.

# Note: Throughout this spec, we use mixed case and -_ inconsistently in -D to assert it is correctly normalized
%pyproject_extras_subpkg -n python3-double-markdown-it-py -D MARKDOWN-it_py linkify


%package -n python3-double-setuptools-scm
Summary:        setuptools_scm from double-install test

%description -n python3-double-setuptools-scm
setuptools_scm subpackage.

%pyproject_extras_subpkg -n python3-double-setuptools-scm -D Setuptools_scm toml


%prep
%setup -Tc
tar xf %{SOURCE1}
tar xf %{SOURCE2}


%generate_buildrequires
%pyproject_buildrequires --extras linkify --pyproject-dependencies --directory markdown-it-py-%{markdown_it_py_version}
%pyproject_buildrequires --extras toml --directory setuptools_scm-%{setuptools_scm_version}


%build
%pyproject_wheel --directory markdown-it-py-%{markdown_it_py_version}
%pyproject_wheel --directory setuptools_scm-%{setuptools_scm_version}


%install
set -o pipefail
(
# This should install both the wheels:
%pyproject_install
) 2>&1 | tee install.log
%pyproject_save_files --dist-name markdown-it_Py markdown_it --no-assert-license
%pyproject_save_files -D setuptools--SCM -l setuptools_scm


%check
%pyproject_check_import -D markdown_IT-py
%pyproject_check_import -D setuptoolS-_scm

# Internal check for the value of %%{pyproject_build_lib}
cd setuptools_scm-%{setuptools_scm_version}
test "%{pyproject_build_lib}" == "%{_builddir}/%{buildsubdir}/setuptools_scm-%{setuptools_scm_version}/build/lib"
cd ..
# Internal regression check for %%pyproject_install with multiple wheels
grep 'binary operator expected' install.log && exit 1 || true
grep 'too many arguments' install.log && exit 1 || true

# Internal check: per-package ghost distinfo files should exist with correct content
test "$(cat %{_pyproject_ghost_distinfo -D markdown-it-py})" == "%ghost %dir %{python3_sitelib}/markdown_it_py-%{markdown_it_py_version}.dist-info"
test "$(cat %{_pyproject_ghost_distinfo -D setuptools-scm})" == "%ghost %dir %{python3_sitelib}/setuptools_scm-%{setuptools_scm_version}.dist-info"


%files -n python3-double-markdown-it-py -f %{pyproject_files -D Markdown_it-py}
%{_bindir}/markdown-it

%files -n python3-double-setuptools-scm -f %{pyproject_files --dist-name setuptools_scm}
