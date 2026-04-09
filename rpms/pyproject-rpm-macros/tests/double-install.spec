Name:           double-install
Version:        0
Release:        0%{?dist}
Summary:        Install 2 wheels
License:        BSD-3-Clause AND MIT
%global         markupsafe_version 2.0.1
%global         tldr_version 0.4.4
Source1:        https://github.com/pallets/markupsafe/archive/%{markupsafe_version}/MarkupSafe-%{markupsafe_version}.tar.gz
Source2:        %{pypi_source tldr %{tldr_version}}

BuildRequires:  gcc
BuildRequires:  python3-devel

%description
This package tests that we can build and install 2 wheels at once.
One of them is "noarch" and one has an extension module.
This also tests the -d option for %%pyproject_buildrequires/%%pyproject_wheel.


%prep
%setup -Tc
tar xf %{SOURCE1}
tar xf %{SOURCE2}


%generate_buildrequires
%pyproject_buildrequires -R -d markupsafe-%{markupsafe_version}
%pyproject_buildrequires -R -d tldr-%{tldr_version}


%build
%pyproject_wheel -d markupsafe-%{markupsafe_version}
%pyproject_wheel -d tldr-%{tldr_version}


%install
set -o pipefail
(
# This should install both the wheels:
%pyproject_install
) 2>&1 | tee install.log
#pyproject_save_files is not possible with 2 dist-infos


%check
# Internal check for the value of %%{pyproject_build_lib}
cd markupsafe-%{markupsafe_version}
%if 0%{?rhel} == 9
test "%{pyproject_build_lib}" == "%{_builddir}/%{buildsubdir}/markupsafe-%{markupsafe_version}/build/lib.%{python3_platform}-%{python3_version}"
%else
test "%{pyproject_build_lib}" == "%{_builddir}/%{buildsubdir}/markupsafe-%{markupsafe_version}/build/lib.%{python3_platform}-cpython-%{python3_version_nodots}"
%endif
cd ../tldr-%{tldr_version}
test "%{pyproject_build_lib}" == "%{_builddir}/%{buildsubdir}/tldr-%{tldr_version}/build/lib"
cd ..
# Internal regression check for %%pyproject_install with multiple wheels
grep 'binary operator expected' install.log && exit 1 || true
grep 'too many arguments' install.log && exit 1 || true


%files
%{_bindir}/tldr*
%pycached %{python3_sitelib}/tldr.py
%{python3_sitelib}/tldr-%{tldr_version}.dist-info/
%{python3_sitearch}/[Mm]arkup[Ss]afe-%{markupsafe_version}.dist-info/
%{python3_sitearch}/markupsafe/
