Name:           gpgverify-test-all-but-first-bad
Version:        1
Release:        1
Summary:        gpgverify testcase, only the first signature matches
License:        FSFAP
Source1:        dummy.tar.gz
Source2:        dummy.tar.gz.asc
Source3:        key-1.gpg
Source4:        key-2.gpg
Source5:        key-3.gpg
Source6:        key-4.gpg
Source7:        key-5.gpg
BuildRequires:  gpgverify

%description
Building this package shall fail because the tarball doesn't match most of the
signatures in the signature file.

%prep
%{gpgverify} --keyrings '%{_sourcedir}'/key-* --data='%{SOURCE1}' --signature='%{SOURCE2}'
echo 'Execution of prep continues.'

%changelog
* Tue Feb 17 2026 Björn Persson <Bjorn@Rombobjörn.se> - 1-1
- created
