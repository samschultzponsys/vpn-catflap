#!/bin/sh
# Installs catflap into the Flatpak (/app). Run by flatpak-builder from the repo root.
set -eu
SITE=$(python3 -c 'import sysconfig; print(sysconfig.get_path("purelib", vars={"base": "/app"}))')
mkdir -p "$SITE"
cp -r catflap "$SITE/"
python3 -m compileall -q "$SITE/catflap"
install -Dm755 packaging/flatpak/catflap.sh /app/bin/catflap
install -Dm755 packaging/flatpak/catflap-tray.sh /app/bin/catflap-tray
install -Dm644 packaging/flatpak/io.github.samschultzponsys.CatFlap.desktop -t /app/share/applications
install -Dm644 packaging/catflap.svg /app/share/icons/hicolor/scalable/apps/io.github.samschultzponsys.CatFlap.svg
install -Dm644 packaging/flatpak/io.github.samschultzponsys.CatFlap.metainfo.xml -t /app/share/metainfo
install -Dm644 LICENSE NOTICE -t /app/share/licenses/io.github.samschultzponsys.CatFlap
install -Dm755 packaging/flatpak/trim.sh /app/share/catflap/trim.sh
