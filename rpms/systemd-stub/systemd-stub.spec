Name:           systemd-stub
Version:        1
Release:        2.1%{?dist}
Summary:        Stub systemd packages for containers
License:        MIT
URL:            https://hummingbird-project.io

# Provide all the systemd packages we want to stub out
# version has to be high to satisfy versioned dependencies, such
# as rpm having "Conflicts: systemd < 253.5-6"
%define version 999
# DISABLE: systemd-stub satisfies BuildRequires, which breaks a ton of builds
# re-enable this once we figure out a way to install the stub *only* for
# container builds
# Provides:       systemd = %{version}-%{release}
# Provides:       systemd-shared = %{version}-%{release}
# Provides:       systemd-sysusers = %{version}-%{release}
# Provides:       systemd-pam = %{version}-%{release}

%description
Empty stub systemd packages for satisfying dependencies in containers

%prep

%build

%install
# Create minimal directory structure to keep RPM happy
mkdir -p %{buildroot}%{_docdir}/%{name}
echo "This is a stub package providing systemd dependencies for containers." > %{buildroot}%{_docdir}/%{name}/README

%files
%{_docdir}/%{name}/README

%changelog
%autochangelog
