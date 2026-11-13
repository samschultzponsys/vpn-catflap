"""Render catflap/resources/catflap.svg into a multi-size Windows .ico.

Usage: python packaging/windows/make_ico.py <output.ico>
"""
import sys
from io import BytesIO

from PIL import Image
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtSvg import QSvgRenderer

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["make_ico", "-platform", "offscreen"])
renderer = QSvgRenderer("catflap/resources/catflap.svg")
img = QtGui.QImage(256, 256, QtGui.QImage.Format_ARGB32)
img.fill(QtCore.Qt.transparent)
p = QtGui.QPainter(img)
p.setRenderHint(QtGui.QPainter.Antialiasing)
renderer.render(p)
p.end()
buf = QtCore.QBuffer()
buf.open(QtCore.QIODevice.WriteOnly)
img.save(buf, "PNG")
Image.open(BytesIO(bytes(buf.data()))).save(
    sys.argv[1], sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
