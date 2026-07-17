Name:           python-ipykernel
Version:        6.30.0
Release:        0%{?dist}
Summary:        IPython Kernel for Jupyter
License:        BSD-3-Clause
URL:            https://github.com/ipython/ipykernel
Source0:        https://github.com/ipython/ipykernel/archive/v%{version}/ipykernel-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-devel

%description
This package contains data files.
Building this tests that data files are not listed when +auto is not used
with %%pyproject_save_files.
Run %%pyproject_check_import on installed package and exclude unwanted modules
(if they're not excluded, build fails).
- We don't want to pull test dependencies just to check import
- The others fail to find `gi` and `matplotlib` which weren't declared
  in the upstream metadata


%package -n python3-ipykernel
Summary:        %{summary}

%description -n python3-ipykernel
...

%prep
%autosetup -p1 -n ipykernel-%{version}

# Remove the dependency on debugpy.
# See https://github.com/ipython/ipykernel/pull/767
%pyproject_patch_dependency debugpy:ignore

%generate_buildrequires
%pyproject_buildrequires --runtime

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files --assert-license 'ipykernel*' --auto

%check
# The global definition here serves as as a regression test
# for option parsing bug fixed in 2acda15.
# Use case extracted from python-pylero-0.1.1-6.fc44,
# enhanced with combining short and long options.
%global _check_import_excludes %{expand:
-e '*.test*' --exclude 'ipykernel.gui*'
--exclude 'ipykernel.pylab.*' -e 'ipykernel.trio*'
-e 'ipykernel.datapub' --exclude 'ipykernel.pickleutil'
-e 'ipykernel.serialize'
}
%pyproject_check_import %_check_import_excludes

%files -n python3-ipykernel -f %{pyproject_files}
%doc README.md

