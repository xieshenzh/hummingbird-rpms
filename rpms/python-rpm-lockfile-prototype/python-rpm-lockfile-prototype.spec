%global pypi_name rpm-lockfile-prototype

Name:           python-%{pypi_name}
Version:        0.27.0
Release:        0.1%{?dist}
Summary:        Generate lockfiles for RPM package dependencies

License:        GPL-3.0-or-later
URL:            https://github.com/konflux-ci/rpm-lockfile-prototype
Source:         %{url}/archive/v%{version}/%{pypi_name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  python3-dnf

%global _description %{expand:
Tool for generating lockfiles for RPM package dependency resolution,
enabling builds without network access. Resolves RPM transactions and
outputs lockfile formats compatible with Hermeto.}

%description %{_description}

%package -n python3-%{pypi_name}
Summary:        %{summary}
Requires:       python3-dnf
Recommends:     skopeo

%description -n python3-%{pypi_name} %{_description}

%prep
%autosetup -p1 -n %{pypi_name}-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files rpm_lockfile

%check
%pyproject_check_import

%files -n python3-%{pypi_name} -f %{pyproject_files}
%doc README.md
%{_bindir}/rpm-lockfile-prototype
%{_bindir}/caching-rpm-lockfile-prototype

%changelog
%autochangelog
