# General Library Imports
from collections import deque
import time
import numpy as np

# PyQt Imports
from PySide2.QtCore import Qt, QTimer
from PySide2.QtGui import QPixmap
import pyqtgraph as pg
from PySide2.QtWidgets import QGroupBox, QGridLayout, QLabel, QWidget, QVBoxLayout, QHBoxLayout, QSplitter

# Logger


class AutoScaleLabel(QLabel):
    """QLabel whose font size scales with width, optionally capped by height."""
    def __init__(self, text='', bg='#46484f', fg='white',
                 w_divisor=10, h_divisor=None, align=Qt.AlignCenter):
        super().__init__(text)
        self._bg = bg
        self._fg = fg
        self._w_divisor = w_divisor
        self._h_divisor = h_divisor
        self.setWordWrap(True)
        self.setAlignment(align)
        self.setMinimumWidth(1)
        self.setMinimumHeight(1)
        self._refreshTimer = QTimer(self)
        self._refreshTimer.setSingleShot(True)
        self._refreshTimer.setInterval(10)
        self._refreshTimer.timeout.connect(self._refresh_style)

    def setBackground(self, color):
        self._bg = color
        self._refresh_style()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refreshTimer.start()

    def _refresh_style(self):
        size = self.width() // self._w_divisor
        if self._h_divisor:
            size = min(size, self.height() // self._h_divisor)
        size = max(8, size)
        self.setStyleSheet(
            f'background-color: {self._bg}; color: {self._fg}; '
            f'font-size: {size}px; font-weight: light;'
        )


class AspectRatioImageLabel(QLabel):
    """QLabel that scales a pixmap to fit while preserving aspect ratio."""
    def __init__(self):
        super().__init__()
        self._src = QPixmap()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(1, 1)

    def setPixmap(self, pixmap):
        self._src = pixmap
        self._update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update()

    def _update(self):
        if not self._src.isNull():
            scaled = self._src.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            super().setPixmap(scaled)


class SurfaceClassification():
    def __init__(self):
        self.surfaceList = ['Not Grass', 'Grass']
        self.surfaceLatestResults = deque(100*[0], 100)
        self.currFrameClassification = -1
        self.plotStart = 0

    def setupGUI(self, gridLayout, demoTabs, device, menuBar=None, app=None):
        statBox = self.initStatsPane()
        gridLayout.addWidget(statBox, 2, 0, 1, 1)

        demoGroupBox = self.initSurfacePhysicalSetupPane()
        gridLayout.addWidget(demoGroupBox, 3, 0, 1, 1)

        # --- description panel in left pane ---
        surfaceDescStr = (
            "<p><b>Sensor Setup:</b></p>"
            "<p>18cm off the ground with 27 degree tilt off the vertical</p>"
            "<p><b>Model:</b></p>"
            "<p>Sequential model trained on grass and large stone pavers</p>"
            "<p><b>More info:</b></p>"
            "<p>See User Guide in the Radar Toolbox on dev.ti.com</p>"
        )
        self.surfaceDesc = AutoScaleLabel(
            surfaceDescStr, bg='white', fg='black',
            w_divisor=20, h_divisor=8, align=Qt.AlignLeft
        )
        self.surfaceDesc.setOpenExternalLinks(True)
        descBox = QGroupBox('More Information')
        descLayout = QVBoxLayout()
        descLayout.addWidget(self.surfaceDesc)
        descBox.setLayout(descLayout)
        gridLayout.addWidget(descBox, 4, 0, 1, 1)

        self.surfaceTab = QWidget()

        # --- full-width classification/probability boxes ---
        self.surfaceOutput = AutoScaleLabel(
            "<b>Grass Classification</b><br>" + str(self.surfaceList[0]),
            w_divisor=12, h_divisor=3,
        )
        self.surfaceOutputRaw = AutoScaleLabel(
            "<b>Grass Probability</b><br>0.0%",
            w_divisor=12, h_divisor=3,
        )
        classWidget = QWidget()
        classWidget.setMinimumHeight(80)
        classLayout = QHBoxLayout(classWidget)
        classLayout.setContentsMargins(0, 0, 0, 0)
        classLayout.addWidget(self.surfaceOutput)
        classLayout.addWidget(self.surfaceOutputRaw)

        # --- probability over time plot ---
        self.surfaceOutputRange = pg.PlotWidget()
        self.surfaceOutputRange.setBackground((70, 72, 79))
        self.surfaceOutputRange.showGrid(x=True, y=True, alpha=1)
        self.surfaceOutputRange.getAxis('bottom').setPen('w')
        self.surfaceOutputRange.getAxis('left').setPen('w')
        self.surfaceOutputRange.getAxis('right').setStyle(showValues=False)
        self.surfaceOutputRange.hideAxis('top')
        self.surfaceOutputRange.hideAxis('right')
        self.surfaceOutputRange.setXRange(0, 100, padding=0.00)
        self.surfaceOutputRange.setYRange(0, 1, padding=0.00)
        self.surfaceOutputRange.setMouseEnabled(False, False)
        self.surfaceOutputRangeData = pg.PlotCurveItem(pen=pg.mkPen(width=3, color='b'))
        self.surfaceOutputRange.addItem(self.surfaceOutputRangeData)
        self.surfaceOutputRange.getPlotItem().setLabel(
            'bottom', '<p style="font-size: 20px;color: white">Relative Frame # (0 is most recent)</p>')
        self.surfaceOutputRange.getPlotItem().setLabel(
            'left', '<p style="font-size: 20px;color: white">Grass Probability Value</p>')
        self.surfaceOutputRange.getPlotItem().setLabel('right', ' ')
        self.surfaceOutputRange.getPlotItem().setTitle(
            '<p style="font-size: 30px;color: white">Probability Value over Time</p>')

        # --- vertical splitter: classification row on top, plot on bottom ---
        vSplitter = QSplitter(Qt.Vertical)
        vSplitter.addWidget(classWidget)
        vSplitter.addWidget(self.surfaceOutputRange)
        vSplitter.setStretchFactor(0, 1)
        vSplitter.setStretchFactor(1, 3)
        vSplitter.setCollapsible(0, False)

        tabLayout = QVBoxLayout(self.surfaceTab)
        tabLayout.setContentsMargins(0, 0, 0, 0)
        tabLayout.addWidget(vSplitter)

        demoTabs.addTab(self.surfaceTab, 'Surface Classification')

    def initSurfacePhysicalSetupPane(self):
        self.surfaceSetupBox = QGroupBox('Physical Setup')
        self.gestureSetupGrid = QGridLayout()
        self.gestureImgLabel = AspectRatioImageLabel()
        self.gestureImgLabel.setPixmap(QPixmap('./images/surface_setup.png'))
        self.gestureSetupGrid.addWidget(self.gestureImgLabel, 1, 1)
        self.surfaceSetupBox.setLayout(self.gestureSetupGrid)
        return self.surfaceSetupBox

    def initStatsPane(self):
        statBox = QGroupBox('Statistics')
        self.frameNumDisplay = QLabel('Frame: 0')
        self.plotTimeDisplay = QLabel('Plot Time: 0 ms')
        self.numPointsDisplay = QLabel('Points: 0')
        self.statsLayout = QVBoxLayout()
        self.statsLayout.addWidget(self.frameNumDisplay)
        self.statsLayout.addWidget(self.plotTimeDisplay)
        self.statsLayout.addWidget(self.numPointsDisplay)
        statBox.setLayout(self.statsLayout)
        return statBox

    def updateGraph(self, outputDict):
        self.plotStart = int(round(time.time()*1000))

        if ('surfaceClassificationOutput' in outputDict):
            classification = outputDict['surfaceClassificationOutput']
            self.surfaceLatestResults.appendleft(classification)

            currentClassification = np.average(
                list(self.surfaceLatestResults)[0:5], weights=[5, 4, 3, 2, 1])
            self.surfaceOutputRangeData.setData(
                np.arange(0, 100), list(self.surfaceLatestResults))

            if (currentClassification > 0.5):
                self.surfaceOutput.setText(
                    "<b>Grass Classification</b><br>" + str(self.surfaceList[1]))
                self.surfaceOutput.setBackground('green')
                self.surfaceOutputRaw.setBackground('green')
            else:
                self.surfaceOutput.setText(
                    "<b>Grass Classification</b><br>" + str(self.surfaceList[0]))
                self.surfaceOutput.setBackground('#46484f')
                self.surfaceOutputRaw.setBackground('#46484f')

            self.surfaceOutputRaw.setText(
                "<b>Grass Classification Value</b><br>"
                + "{:8.5f}".format(classification * 100) + "%")

        self.graphDone(outputDict)

    def graphDone(self, outputDict):
        plotTime = int(round(time.time()*1000)) - self.plotStart
        self.plotTimeDisplay.setText('Plot Time: ' + str(plotTime) + 'ms')
        self.plotComplete = 1

        if ('frameNum' in outputDict):
            self.frameNumDisplay.setText('Frame: ' + str(outputDict['frameNum']))

        if ('numDetectedPoints' in outputDict):
            self.numPointsDisplay.setText('Points: ' + str(outputDict['numDetectedPoints']))
