Name:           gpgverify-test-multiple-signatures
Version:        1
Release:        1
Summary:        gpgverify testcase, multiple concatenated signatures
License:        FSFAP
Source1:        dummy.tar.gz
Source2:        dummy.tar.gz.asc
Source3:        key-1.gpg
Source4:        key-2.gpg
Source5:        key-3.gpg
Source6:        key-4.gpg
BuildRequires:  gpgverify

%description
This tests verification of a signature file that contains multiple valid
ASCII-armored signatures by different keys. It also passes multiple keyrings
to gpgverify through wildcard expansion.

%prep
%{gpgverify} --keyrings '%{_sourcedir}'/key-* --data='%{SOURCE1}' --signature='%{SOURCE2}'
echo 'Execution of prep continues.'

%changelog
* Mon Feb 16 2026 Björn Persson <Bjorn@Rombobjörn.se> - 1-1
- created
