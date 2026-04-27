Name:           python-virtualenv
Version:        20.19.0
Release:        0%{?dist}
Summary:        Tool to create isolated Python environments

License:        MIT
URL:            http://pypi.python.org/pypi/virtualenv
Source:         %{pypi_source virtualenv}

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-pytest

%description
This specfile was added as a regression test to
https://src.fedoraproject.org/rpms/pyproject-rpm-macros/pull-request/363

It uses hatchling without %%pyproject_buildrequires -w.


%package -n     python3-virtualenv
Summary:        %{summary}

%description -n python3-virtualenv
...


%prep
%autosetup -p1 -n virtualenv-%{version}
# Relax version bounds of some dependencies for EL 9
%pyproject_patch_dependency distlib:set_lower:0.3.2
%pyproject_patch_dependency filelock:set_lower:3.3.1
%pyproject_patch_dependency platformdirs:set_upper:5
%pyproject_patch_dependency platformdirs:set_lower:2.3
%pyproject_patch_dependency hatchling:set_lower:0.25
%pyproject_patch_dependency hatch-vcs:set_lower:0.2.1
# Drop the option for flaky
sed -i 's/--no-success-flaky-report//' pyproject.toml
# Hacky backport of https://src.fedoraproject.org/rpms/python-virtualenv/c/87b1f95664
%if 0%{?rhel} != 9
sed -i 's/_nonwrappers/_hookimpls/' tests/conftest.py
%endif


%generate_buildrequires
%pyproject_buildrequires -w


%build
# %%pyproject_buildrequires -w makes this redundant
# %%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files -l virtualenv
%{?el9:
# old version of setuptools_scm produces files incompatible with
# assumptions in virtualenv code, we append the expected attributes:
echo '__version__, __version_tuple__ = version, version_tuple' >> %{buildroot}%{python3_sitelib}/virtualenv/version.py
}


%check
# test_main fails when .dist-info is not deleted at the end of %%pyproject_buildrequires
# tests/integration/test_zipapp.py imports flaky
PIP_CERT=/etc/pki/tls/certs/ca-bundle.crt \
%pytest -v -k test_main --ignore tests/integration/test_zipapp.py


%files -n python3-virtualenv -f %{pyproject_files}
%doc README.md
%{_bindir}/virtualenv
