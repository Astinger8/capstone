import pyqtgraph.opengl as gl
from pyqtgraph.opengl.GLGraphicsItem import GLGraphicsItem
from OpenGL.GL import *
from pyqtgraph.Qt import QtCore, QtGui
import numpy as np
import logging
import traceback

log = logging.getLogger(__name__)

class GLTextItem(GLGraphicsItem):
    def __init__(self, X=None, Y=None, Z=None, text=None, color=QtCore.Qt.white):
        GLGraphicsItem.__init__(self)

        self.text = text
        self.X = X
        self.Y = Y
        self.Z = Z
        self.font = QtGui.QFont('Helvetica', 12)
        self.color = color
        self.texture_id = None
        self.texture_width = 0
        self.texture_height = 0
        self.last_text = None


    def setFont(self, fontObj):
        self.font = fontObj
        self.update()

    def setColor(self, color):
        self.color = color
        self.last_text = None
        self.update()

    def setGLViewWidget(self, GLViewWidget):
        self.GLViewWidget = GLViewWidget

    def setText(self, text):
        self.text = text
        self.update()

    def setX(self, X):
        self.X = X
        self.update()

    def setY(self, Y):
        self.Y = Y
        self.update()

    def setZ(self, Z):
        self.Z = Z
        self.update()

    def setPosition(self, X, Y, Z):
        self.X = X+0.25
        self.Z = Z+0.6
        self.Y = Y
        self.text = '('+str(X)[:4]+', ' + str(Y)[:4]+', '+str(Z)[:4]+')'
        self.update()

    def _create_texture_from_text(self):
        """Create an OpenGL texture from the text string"""
        if not self.text:
            return

        # Create QImage to render text
        font_metrics = QtGui.QFontMetrics(self.font)
        text_width = font_metrics.horizontalAdvance(self.text)
        text_height = font_metrics.height()

        # Add padding
        padding = 4
        img_width = text_width + padding * 2
        img_height = text_height + padding * 2

        # Create image with transparent background
        image = QtGui.QImage(img_width, img_height, QtGui.QImage.Format_RGBA8888)
        image.fill(QtCore.Qt.transparent)

        # Draw text on image
        painter = QtGui.QPainter(image)
        painter.setFont(self.font)
        painter.setPen(QtGui.QColor(self.color))
        painter.drawText(padding, padding + font_metrics.ascent(), self.text)
        painter.end()

        # Convert to OpenGL texture
        image = image.convertToFormat(QtGui.QImage.Format_RGBA8888)
        img_data = image.constBits()

        # Delete old texture if exists
        if self.texture_id is not None:
            glDeleteTextures([self.texture_id])

        # Generate new texture
        self.texture_id = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self.texture_id)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, img_width, img_height,
                     0, GL_RGBA, GL_UNSIGNED_BYTE, img_data)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glBindTexture(GL_TEXTURE_2D, 0)

        self.texture_width = img_width
        self.texture_height = img_height
        self.last_text = self.text

    def paint(self):
        """Render text as a 2D overlay using projected 3D coordinates"""
        try:
            # Early return if no data
            if self.X is None or self.Y is None or self.Z is None or self.text is None:
                return

            if not hasattr(self, 'GLViewWidget') or self.GLViewWidget is None:
                return

            # Create/update texture if text changed
            if self.text != self.last_text:
                self._create_texture_from_text()

            if self.texture_id is None:
                return

            # Get the modelview and projection matrices
            modelview = glGetDoublev(GL_MODELVIEW_MATRIX)
            projection = glGetDoublev(GL_PROJECTION_MATRIX)
            viewport = glGetIntegerv(GL_VIEWPORT)

            # Project 3D position to screen coordinates
            from OpenGL.GLU import gluProject
            win_x, win_y, win_z = gluProject(self.X, self.Y, self.Z,
                                              modelview, projection, viewport)

            # Skip if behind camera
            if win_z < 0 or win_z > 1:
                return

            # Save OpenGL state
            glPushAttrib(GL_ALL_ATTRIB_BITS)
            glMatrixMode(GL_PROJECTION)
            glPushMatrix()
            glLoadIdentity()
            glOrtho(0, viewport[2], 0, viewport[3], -1, 1)

            glMatrixMode(GL_MODELVIEW)
            glPushMatrix()
            glLoadIdentity()

            # Enable blending for transparency
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glEnable(GL_TEXTURE_2D)
            glDisable(GL_DEPTH_TEST)

            # Bind texture and draw quad
            glBindTexture(GL_TEXTURE_2D, self.texture_id)
            glColor4f(1.0, 1.0, 1.0, 1.0)

            # Scale factor for text size (adjust as needed)
            scale = 1.0
            w = self.texture_width * scale
            h = self.texture_height * scale

            # Draw textured quad at screen position
            glBegin(GL_QUADS)
            glTexCoord2f(0, 1); glVertex2f(win_x, win_y)
            glTexCoord2f(1, 1); glVertex2f(win_x + w, win_y)
            glTexCoord2f(1, 0); glVertex2f(win_x + w, win_y + h)
            glTexCoord2f(0, 0); glVertex2f(win_x, win_y + h)
            glEnd()

            glBindTexture(GL_TEXTURE_2D, 0)

            # Restore OpenGL state
            glMatrixMode(GL_PROJECTION)
            glPopMatrix()
            glMatrixMode(GL_MODELVIEW)
            glPopMatrix()
            glPopAttrib()

        except Exception as e:
            log.error(f"Exception in GLTextItem.paint(): {type(e).__name__}: {e}")
            log.error(f"Text item state: X={self.X}, Y={self.Y}, Z={self.Z}, text={self.text}")

    def __del__(self):
        """Clean up OpenGL texture"""
        if self.texture_id is not None:
            try:
                glDeleteTextures([self.texture_id])
            except:
                pass