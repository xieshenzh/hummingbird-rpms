Name:           package-notes
Version:        0.19
Release:        1%{?dist}
Summary:        ELF Package and Dlopen Notes
License:        0BSD
URL:            https://github.com/systemd/package-notes
Source:         https://github.com/systemd/package-notes/archive/v%{version_no_tilde}/%{name}-%{version_no_tilde}.tar.gz

BuildArch:      noarch
BuildRequires:  make

Requires:       python3dist(pyelftools)

%description
This package provides rpm macros to generate an '.note.package' ELF note in
compiled binaries (programs and shared libraries) to provide metadata about
the package for which the code was compiled.

See https://systemd.io/ELF_PACKAGE_METADATA/ for the overview and details.

It also provides scripts to extract and display '.note.dlopen' ELF notes that
are used to describe libraries loaded via dlopen(3).

See https://systemd.io/ELF_DLOPEN_METADATA/ for the overview and details.

%files
%{_bindir}/dlopen-notes
%{_fileattrsdir}/dlopen_notes.attr
%{_mandir}/man1/dlopen-notes.1*

%package srpm-macros
Summary:        RPM macros to add .note.package ELF note
Obsoletes:      package-notes < 0.5
# Those are minimum versions that implement --package-metadata
Conflicts:      binutils < 2.37-34
Conflicts:      binutils-gold < 2.37-34
Conflicts:      mold < 1.3.0
Conflicts:      lld < 14.0.5-4

%files srpm-macros
%{_rpmconfigdir}/redhat/redhat-package-notes
%{_rpmmacrodir}/macros.package-notes-srpm

%description srpm-macros
RPM macros to insert a section with an ELF note with a JSON payload that
describes the package the binary was built for via a compiler spec file.

%prep
%autosetup -p1

%build
sed "s|@OSCPE@|$(cat /usr/lib/system-release-cpe)|" rpm/redhat-package-notes.in >rpm/redhat-package-notes

%install
%make_install

install -Dt %{buildroot}%{_rpmconfigdir}/redhat/ rpm/redhat-package-notes
install -m0644 -Dt %{buildroot}%{_rpmmacrodir}/  rpm/macros.package-notes-srpm
install -m0644 -Dt %{buildroot}%{_fileattrsdir}/ rpm/dlopen_notes.attr

%changelog
%autochangelog
