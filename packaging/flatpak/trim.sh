#!/bin/sh
# Remove parts of the PySide BaseApp that catflap never uses (build tools,
# PyOpenGL, FFmpeg, and every Qt binding except Core/Gui/Widgets/DBus).
# Cuts the installed size by roughly two thirds.
set -eu
SP=$(echo /app/lib/python3*/site-packages)
rm -rf /app/lib/libLLVM* /app/lib/libclang* /app/lib/debug
rm -rf "$SP"/shiboken6_generator* "$SP"/OpenGL "$SP"/pyopengl-* "$SP"/patchelf-* \
       "$SP"/pyproject_hooks* "$SP"/distro "$SP"/distro-*
cd "$SP/PySide6"
rm -rf Qt/lib Qt/libexec include typesystems glue QtAsyncio scripts doc
for f in *.abi3.so *.pyi; do
  case "$f" in
    QtCore.*|QtGui.*|QtWidgets.*|QtDBus.*) ;;
    *) rm -f "$f" ;;
  esac
done
