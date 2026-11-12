#!/bin/sh
# Installs catflap into the Flatpak (/app). Run by flatpak-builder from the repo root.
set -eu
SITE=$(python3 -c 'import sysconfig; print(sysconfig.get_path("purelib", vars={"base": "/app"}))')
mkdir -p "$SITE"
cp -r catflap "$SITE/"
python3 -m compileall -q "$SITE/catflap"
install -Dm755 packaging/flatpak/catflap.sh /app/bin/catflap
install -Dm755 packaging/flatpak/catflap-tray.sh /app/bin/catflap-tray
ID=io.github.samschultzponsys.CatFlap
# The launcher uses an icon name that changes with every release (artwork + version), so
# desktops that cache icons by name (KDE does, even across reinstalls) can't keep
# showing an old icon. The plain name stays for AppStream/software centres.
REV=$(cat catflap/resources/catflap.svg catflap/version.py | sha256sum | cut -c1-8)
install -Dm644 catflap/resources/catflap.svg /app/share/icons/hicolor/scalable/apps/$ID.svg
install -Dm644 catflap/resources/catflap.svg /app/share/icons/hicolor/scalable/apps/$ID.icon$REV.svg
mkdir -p /app/share/applications
sed "s/^Icon=.*/Icon=$ID.icon$REV/" packaging/flatpak/$ID.desktop > /app/share/applications/$ID.desktop
install -Dm644 packaging/flatpak/io.github.samschultzponsys.CatFlap.metainfo.xml -t /app/share/metainfo
install -Dm644 LICENSE NOTICE -t /app/share/licenses/io.github.samschultzponsys.CatFlap
install -Dm755 packaging/flatpak/trim.sh /app/share/catflap/trim.sh
