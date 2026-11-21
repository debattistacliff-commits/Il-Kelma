"""Painted controls for Kelma Pad."""
from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QAbstractButton, QGraphicsDropShadowEffect, QSlider, QStyle, QStyleOptionSlider


BLUE = "#4F8CFF"
PURPLE = "#7B5CFF"
CHIP_GRADIENT = f"qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {BLUE},stop:1 {PURPLE})"


def gradient(rect, first=BLUE, last=PURPLE):
    fill = QLinearGradient(rect.topLeft(), rect.topRight())
    fill.setColorAt(0, QColor(first))
    fill.setColorAt(1, QColor(last))
    return fill


def shadow(widget, colour="#14213D", alpha=30, blur=28, offset=8):
    effect = QGraphicsDropShadowEffect(widget)
    tint = QColor(colour)
    tint.setAlpha(alpha)
    effect.setColor(tint)
    effect.setBlurRadius(blur)
    effect.setOffset(0, offset)
    widget.setGraphicsEffect(effect)
    return effect


class ToggleSwitch(QAbstractButton):
    def __init__(self, checked=False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(56, 32)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._knob_x = 27.0 if checked else 3.0
        self.animation = QPropertyAnimation(self, b"knobX", self)
        self.animation.setDuration(230)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self.animate)

    @Property(float)
    def knobX(self):
        return self._knob_x

    @knobX.setter
    def knobX(self, value):
        self._knob_x = value
        self.update()

    def animate(self, checked):
        self.animation.stop()
        self.animation.setStartValue(self.knobX)
        self.animation.setEndValue(27.0 if checked else 3.0)
        self.animation.start()

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            track = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            painter.setBrush(gradient(track) if self.isChecked() else QColor("#C9CFDB"))
            if self.hasFocus():
                painter.setPen(QColor("#14213D"))
            painter.drawRoundedRect(track, 15, 15)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(20, 33, 61, 50))
            painter.drawEllipse(QRectF(self.knobX, 4, 26, 26))
            painter.setBrush(QColor("white"))
            painter.drawEllipse(QRectF(self.knobX, 3, 26, 26))


class Swatch(QAbstractButton):
    def __init__(self, extent, colours, *, checkmark=False, ink="#293359"):
        super().__init__()
        self.colours = colours
        self.checkmark = checkmark
        self.ink = ink
        self.setFixedSize(extent, extent)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def nextCheckState(self):
        # The settings group owns which swatch is selected.
        pass

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            ring = QRectF(self.rect()).adjusted(2, 2, -2, -2)
            if self.isChecked() or self.hasFocus():
                painter.setPen(QPen(QColor("#5B63FF" if self.isChecked() else "#14213D"), 2))
                painter.setBrush(QColor("white"))
                painter.drawEllipse(ring)
            dot = ring.adjusted(4, 4, -4, -4)
            painter.setPen(QColor(20, 33, 61, 35))
            painter.setBrush(gradient(dot, *self.colours))
            painter.drawEllipse(dot)
            if self.checkmark and self.isChecked():
                center = dot.center()
                tick = QPainterPath(center + QPointF(-7, 0))
                tick.lineTo(center + QPointF(-2, 5))
                tick.lineTo(center + QPointF(8, -6))
                pen = QPen(QColor(self.ink), 3)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPath(tick)


class SizeSlider(QSlider):
    def paintEvent(self, event):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self)
        x = handle.center().x()
        track = QRectF(12, self.height() / 2 - 4, self.width() - 24, 8)
        filled = QRectF(track)
        filled.setRight(max(track.left(), x))
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#D9DEEA"))
            painter.drawRoundedRect(track, 4, 4)
            painter.setBrush(gradient(filled))
            painter.drawRoundedRect(filled, 4, 4)
            painter.setPen(QColor("#14213D") if self.hasFocus() else QColor("#5B63FF"))
            painter.setBrush(QColor("#5B63FF"))
            painter.drawEllipse(QPointF(x, track.center().y()), 12, 12)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("white"))
            painter.drawEllipse(QPointF(x, track.center().y()), 9, 9)


class DockAction(QAbstractButton):
    def __init__(self, label, icon, colours=None):
        super().__init__()
        self.colours = colours
        self.dark = False
        self.setText(label)
        self.setIcon(icon)
        self.setToolTip(label.replace("\n", " / "))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(104 if colours else 254, 128)

    def setText(self, text):
        super().setText(text)
        self.setAccessibleName(text.replace("\n", " / "))
        self.update()

    def setIcon(self, icon):
        super().setIcon(icon)
        self.update()

    def paintEvent(self, event):
        orb = self.colours is not None
        ink = "#14213D" if orb and not self.dark else "white"
        subtitle = ("#DCE6FC" if self.dark else "#5A6478") if orb else "#F2EDFF"
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(0, -3 if self.underMouse() and not self.isDown() else 0)
            painter.setPen(Qt.PenStyle.NoPen)
            rect = QRectF(10, 6, 84, 84) if orb else QRectF(5, 21, 244, 84)
            glow_colour = QColor(self.colours[1] if orb else PURPLE)
            if orb:
                center = rect.center() + QPointF(0, 4)
                halo = QRadialGradient(center, 53)
                glow_colour.setAlpha(83)
                halo.setColorAt(.72, glow_colour)
                glow_colour.setAlpha(0)
                halo.setColorAt(1, glow_colour)
                painter.setBrush(halo)
                painter.drawEllipse(center, 53, 53)
            else:
                for spread, alpha in ((9, 10), (6, 18), (3, 32)):
                    glow_colour.setAlpha(alpha)
                    painter.setBrush(glow_colour)
                    painter.drawRoundedRect(rect.adjusted(-spread, 4 - spread, spread, 4 + spread), 48, 48)
            if orb:
                light, dark = self.colours
                fill = QRadialGradient(rect.topLeft() + QPointF(25, 21), 72)
                fill.setColorAt(0, QColor(light))
                fill.setColorAt(.8, QColor(dark))
            else:
                fill = gradient(rect)
            painter.setBrush(fill)
            painter.setPen(QColor("#14213D") if self.hasFocus() else QColor(255, 255, 255, 125))
            painter.drawRoundedRect(rect, 42, 42)
            self.icon().paint(painter, 36 if orb else 24, 32 if orb else 47, 32, 32)
            english, maltese = self.text().split("\n")
            align = Qt.AlignmentFlag.AlignCenter if orb else Qt.AlignmentFlag.AlignVCenter
            painter.setPen(QColor(ink))
            font = self.font()
            font.setBold(True)
            font.setPixelSize(15 if orb else 18)
            painter.setFont(font)
            painter.drawText(QRectF(0, 94, 104, 18) if orb else QRectF(67, 39, 175, 25), align, english)
            font.setBold(False)
            font.setPixelSize(12)
            painter.setFont(font)
            painter.setPen(QColor(subtitle))
            painter.drawText(QRectF(0, 112, 104, 16) if orb else QRectF(67, 64, 175, 20), align, maltese)

