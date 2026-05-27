Name:           config-settings-test
Version:        1.0.0
Release:        1%{?dist}
Summary:        Test config_settings support

License:        MIT
URL:            ...
Source0:        config_settings_test_backend.py


%description
%{summary}.


%prep
%autosetup -cT

cp -p %{sources} .

cat <<'EOF' >config_settings.py
"""
This is a test package
"""
EOF

cat <<'EOF' >pyproject.toml
[build-system]
build-backend = "config_settings_test_backend"
backend-path = ["."]
requires = ["flit-core", "packaging", "pip"]

[project]
name = "config_settings"
version = "%{version}"
dynamic = ["description"]
EOF


%generate_buildrequires
%pyproject_buildrequires --config-settings abc=123 -C xyz=456 --config-settings=--option-with-dashes=1 -C--option-with-dashes=2
%{!?el9:%pyproject_buildrequires --config-settings abc=123 -C xyz=456 --config-settings=--option-with-dashes=1 -C--option-with-dashes=2 --wheel}


%build
# The way the macro is invoked serves as as a regression test
# for option parsing bug fixed in 2acda15.
# Use case extracted from python-nanobind-2.12.0-1.fc45.
# Note that the : makes everything one giant argument for RPM
# and this only works becasue:
#  1. Each -C/--config-settings is on separate line.
#  2. There are no spaces other than those separating each -C/--config-settings.
%{!?el9:%{pyproject_wheel: \
--config-settings="abc=123" \
-C"xyz=456" \
--config-settings=--option-with-dashes=1 \
-C=--option-with-dashes=2 \
}}


%changelog
* Fri May 19 2023 Maxwell G <maxwell@gtmx.me>
- Initial package
