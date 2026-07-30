%bcond_without check

Name:           gorget
Version:        0.1.11
Release:        0.1%{?dist}
Summary:        Containerized source-pipeline tool for RPM package supply-chain trust
License:        MIT

URL:            https://github.com/gorget-project/gorget
# GitHub's archive URL basename (v0.1.1.tar.gz) doesn't match the
# gorget-0.1.1.tar.gz name dist-git-client/the lookaside cache use --
# the #/ fragment tells rpmbuild the local filename to expect instead of
# deriving it from the URL, matching what's actually in "sources".
Source:         %{url}/archive/v%{version}.tar.gz#/%{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  python3-devel
%if %{with check}
BuildRequires:  %{py3_dist pytest}
BuildRequires:  %{py3_dist pytest-mock}
BuildRequires:  %{py3_dist responses}
%endif

%description
Gorget fetches upstream source tarballs directly from their origin (rather
than an intermediate lookaside cache), applies transforms, verifies
integrity, enforces dependency policy, and emits lookaside-ready artifacts.

Each package gets a declarative <package>.source-pipeline.yaml describing
exactly how its sources are produced. When no pipeline YAML exists, gorget
falls back to fetching every Source URL declared in the package's spec file.

%prep
%autosetup -n gorget-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files gorget

%check
%pyproject_check_import
%if %{with check}
# Integration tests shell out to real external tools (rpmspec, gpg, git, go,
# npm, cargo) not guaranteed present in the build root -- they already skip
# themselves via shutil.which() guards when a tool is missing, but excluding
# the whole marked set here keeps the package build itself hermetic and fast.
%pytest -m "not integration"
%endif

%files -f %{pyproject_files}
%doc README.md
%{_bindir}/gorget

%changelog
%autochangelog
