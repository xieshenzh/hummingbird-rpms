Name:           fake-requirements
Version:        0
Release:        0%{?dist}

Summary:        ...
License:        MIT

BuildRequires:  pyproject-rpm-macros


%description
Fake spec file to test %%pyproject_buildrequires --no-use-build-system works as expected

%prep
cat > requirements.txt <<EOF
click!=5.0.0,>=4.1 # comment to increase test complexity
tomli>=0.10.0
EOF

%generate_buildrequires
%pyproject_buildrequires requirements.txt --no-use-build-system


%check
grep '^((python3dist(click) < 5 or python3dist(click) > 5) with python3dist(click) >= 4.1)$' %{_pyproject_buildrequires}
grep '^python3dist(tomli) >= 0.10$' %{_pyproject_buildrequires}

grep 'python3dist(pip)' %{_pyproject_buildrequires} && exit 1 || true
grep 'python3dist(wheel)' %{_pyproject_buildrequires} && exit 1 || true
test -f /usr/bin/pip && exit 1 || true
