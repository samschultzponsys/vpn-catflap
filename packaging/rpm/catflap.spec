Name:           catflap
Version:        0.1
Release:        1%{?dist}
Summary:        Safe one-click WireGuard remote access to your sites
License:        Apache-2.0
URL:            https://github.com/samschultzponsys/vpn-catflap
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-pytest
BuildRequires:  desktop-file-utils

Requires:       NetworkManager
Requires:       python3-keyring
Requires:       python3-cryptography
Requires:       iproute
Requires:       iputils
Recommends:     python3-pyside6
Recommends:     wireguard-tools

%description
catflap turns "I want remote access to site X" into a safe split-tunnel
WireGuard connection managed by NetworkManager, provisioned through the
UniFi, TP-Link Omada or Alta Labs gateway (or an imported .conf), and
controlled from a tray icon.

%prep
%autosetup -n %{name}-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files catflap
install -Dm644 packaging/catflap.desktop %{buildroot}%{_datadir}/applications/catflap.desktop
install -Dm644 packaging/catflap.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/catflap.svg

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/catflap.desktop
%pytest -q

%files -f %{pyproject_files}
%license LICENSE NOTICE
%doc README.md docs/
%{_bindir}/catflap
%{_bindir}/catflap-tray
%{_datadir}/applications/catflap.desktop
%{_datadir}/icons/hicolor/scalable/apps/catflap.svg

%changelog
* Wed Nov 11 2026 catflap maintainers - 0.1-1
- Milestone 1: CLI, UniFi driver, NetworkManager backend, tray app
