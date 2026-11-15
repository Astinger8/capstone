import time
import numpy as np
import pyqtgraph as pg
import pyqtgraph.opengl as gl

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QCheckBox, QComboBox, QDoubleSpinBox, QPushButton
)

from pyqtgraph.opengl.MeshData import MeshData
from Common_Tabs.plot_3d import Plot3D
from gl_text import GLTextItem
from graph_utilities import get_trackColors, eulerRot
from gui_threads import updateQTTargetThread3D

import logging
log = logging.getLogger(__name__)

COLOR_MODE_SNR     = 'SNR'
COLOR_MODE_HEIGHT  = 'Height'
COLOR_MODE_DOPPLER = 'Doppler'
COLOR_MODE_TRACK   = 'Associated Track'
MAX_PERSISTENT_FRAMES = 30

class _SafeScatterPlotItem(gl.GLScatterPlotItem):
    def paint(self):
        if self.pos is None or len(self.pos) == 0:
            return
        super().paint()

class AreaScanner:
    def __init__(self):
        self._plot3d = Plot3D()
        self.az_fov_deg = 120
        self.plotComplete = 1
        self.plotStart = 0
        self.app = None
        self.maxRange = 6.0  # default; updated by parseBoundaryBox / profileCfg
        self.trackColorMap  = None

    def setupGUI(self, gridLayout, demoTabs, device, menuBar=None, app=None):
        self.app = app

        statBox = self.initStatsPane()
        gridLayout.addWidget(statBox, 2, 0, 1, 1)

        sensorBox = self.initSensorInfoPane()
        gridLayout.addWidget(sensorBox, 3, 0, 1, 1)

        vizBox = self.initVisualizerOptionsPane()
        gridLayout.addWidget(vizBox, 4, 0, 1, 1)

        plotCtrlBox = self.initPlotControlPane()
        gridLayout.addWidget(plotCtrlBox, 5, 0, 1, 1)

        demoTabs.addTab(self._plot3d.plot_3d, 'Area Scanner Plot')

        # initPlot must be called last so all spinboxes exist
        self.initPlot()

    # ------------------------------------------------------------------ #
    #  Sensor Information panel                                            #
    # ------------------------------------------------------------------ #
    def initSensorInfoPane(self):
        box = QGroupBox('Sensor Information')
        layout = QFormLayout()

        self.mountingHeight = QDoubleSpinBox()
        # TODO: confirm realistic upper bound for mounting height (currently 100 m)
        self.mountingHeight.setRange(0.0, 100.0)
        self.mountingHeight.setSingleStep(0.1)
        self.mountingHeight.setValue(0)
        self.mountingHeight.setSuffix(' m')
        layout.addRow('Mounting Height:', self.mountingHeight)

        self.elevTilt = QDoubleSpinBox()
        self.elevTilt.setRange(-90.0, 90.0)
        self.elevTilt.setSingleStep(1.0)
        self.elevTilt.setValue(0.0)
        self.elevTilt.setSuffix(' deg')
        layout.addRow('Elevation Tilt:', self.elevTilt)

        self.mountingHeight.valueChanged.connect(lambda _: self._onSensorPosChanged())
        self.elevTilt.valueChanged.connect(lambda _: self._onSensorPosChanged())

        box.setLayout(layout)
        return box

    def _onSensorPosChanged(self):
        self._plot3d.sensorHeight = self.mountingHeight.value()
        self._plot3d.elev_tilt    = self.elevTilt.value()
        if hasattr(self, 'fovItem'):
            self.drawFOVLines()
        self._redrawZoneGeometry()

    # ------------------------------------------------------------------ #
    #  Visualizer Options panel                                            #
    # ------------------------------------------------------------------ #
    def initVisualizerOptionsPane(self):
        box = QGroupBox('Visualizer Options')
        layout = QFormLayout()

        self.showZones = QCheckBox('Show zone occupancy')
        self.showZones.setChecked(True)
        self.showZones.stateChanged.connect(self.onShowZonesChanged)
        layout.addRow(self.showZones)

        self.critStart = QDoubleSpinBox()
        self.critStart.setRange(0.0, self.maxRange)
        self.critStart.setSingleStep(0.5)
        self.critStart.setValue(0.0)
        self.critStart.setSuffix(' m')
        layout.addRow('Critical Zone Start:', self.critStart)

        self.critEnd = QDoubleSpinBox()
        self.critEnd.setRange(0.0, self.maxRange)
        self.critEnd.setSingleStep(0.5)
        self.critEnd.setValue(1.0)
        self.critEnd.setSuffix(' m')
        layout.addRow('Critical Zone End:', self.critEnd)

        self.warnStart = QDoubleSpinBox()
        self.warnStart.setRange(0.0, self.maxRange)
        self.warnStart.setSingleStep(0.5)
        self.warnStart.setValue(1.0)
        self.warnStart.setSuffix(' m')
        layout.addRow('Warning Zone Start:', self.warnStart)

        self.warnEnd = QDoubleSpinBox()
        self.warnEnd.setRange(0.0, self.maxRange)
        self.warnEnd.setSingleStep(0.5)
        self.warnEnd.setValue(min(4.0, self.maxRange))
        self.warnEnd.setSuffix(' m')
        layout.addRow('Warning Zone End:', self.warnEnd)

        # Enforce critStart ≤ critEnd ≤ warnStart ≤ warnEnd ≤ maxRange
        # Push adjacent end up when a start increases past it
        self.critStart.valueChanged.connect(lambda val: self.critEnd.setValue(max(self.critEnd.value(), val)))
        self.critEnd.valueChanged.connect(lambda val: self.warnStart.setValue(max(self.warnStart.value(), val)))
        self.warnStart.valueChanged.connect(lambda val: self.warnEnd.setValue(max(self.warnEnd.value(), val)))
        # Pull adjacent start down when an end decreases below it
        self.critEnd.valueChanged.connect(lambda val: self.critStart.setValue(min(self.critStart.value(), val)))
        self.warnStart.valueChanged.connect(lambda val: self.critEnd.setValue(min(self.critEnd.value(), val)))
        self.warnEnd.valueChanged.connect(lambda val: self.warnStart.setValue(min(self.warnStart.value(), val)))

        self.projTime = QDoubleSpinBox()
        # TODO: confirm a sensible upper bound for projection time (currently 30 s)
        self.projTime.setRange(0.0, 30.0)
        self.projTime.setSingleStep(0.25)
        self.projTime.setValue(1.0)
        self.projTime.setSuffix(' s')
        layout.addRow('Projection Time:', self.projTime)

        applyBtn = QPushButton('Apply Changes')
        applyBtn.clicked.connect(lambda checked=False: self.drawZoneArcs())
        layout.addRow(applyBtn)

        # Committed values — only updated when Apply Changes is clicked
        self._critStartVal = self.critStart.value()
        self._critEndVal   = self.critEnd.value()
        self._warnStartVal = self.warnStart.value()
        self._warnEndVal   = self.warnEnd.value()

        box.setLayout(layout)
        return box

    def onShowZonesChanged(self, state):
        enabled = bool(state)
        for w in (self.critStart, self.critEnd, self.warnStart, self.warnEnd, self.projTime):
            w.setEnabled(enabled)
        self._redrawZoneGeometry()

    # ------------------------------------------------------------------ #
    #  Plot Controls panel                                               #
    # ------------------------------------------------------------------ #
    def initPlotControlPane(self):
        box = QGroupBox('Plot Controls')
        layout = QFormLayout()

        self.pointColorMode = QComboBox()
        self.pointColorMode.addItems([COLOR_MODE_SNR, COLOR_MODE_HEIGHT,
                                      COLOR_MODE_DOPPLER, COLOR_MODE_TRACK])
        self.pointColorMode.setCurrentText(COLOR_MODE_TRACK)
        layout.addRow('Color Points By:', self.pointColorMode)

        self.persistentFramesInput = QComboBox()
        self.persistentFramesInput.addItems([str(i) for i in range(1, MAX_PERSISTENT_FRAMES + 1)])
        self.persistentFramesInput.setCurrentIndex(self._plot3d.numPersistentFrames - 1)
        self.persistentFramesInput.currentIndexChanged.connect(self.persistentFramesChanged)
        layout.addRow('# of Persistent Frames:', self.persistentFramesInput)

        btnRow = QHBoxLayout()
        for idx, label in enumerate(['X-Y', 'Y-Z', 'X-Z', '3D']):
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked=False, i=idx: self.switchView(i))
            btnRow.addWidget(btn)
        layout.addRow('View:', btnRow)

        box.setLayout(layout)
        return box

    def persistentFramesChanged(self, index):
        self._plot3d.numPersistentFrames = index + 1
        while len(self._plot3d.previousClouds) > self._plot3d.numPersistentFrames:
            self._plot3d.previousClouds.pop(0)

    def switchView(self, idx, maxRange=None, center_z=0):
        self.currentViewIdx = idx
        self._plot3d.switchView(idx, self.maxRange, int(self.mountingHeight.value()))

    # ------------------------------------------------------------------ #
    #  Plot initialisation and FOV / zone arc drawing                      #
    # ------------------------------------------------------------------ #
    def initPlot(self):
        # Guard: if already initialised (user switched away and back), just redraw
        if hasattr(self, 'fovItem'):
            self.drawFOVLines()
            self.drawZoneArcs()
            self.switchView(getattr(self, 'currentViewIdx', 3))
            return

        self.fovItem = gl.GLLinePlotItem(antialias=True)
        self._plot3d.plot_3d.addItem(self.fovItem)

        self.fovCapItem = gl.GLLinePlotItem(antialias=True)
        self._plot3d.plot_3d.addItem(self.fovCapItem)

        # Two GL items per zone: inner arc + outer arc
        self.critInnerArc = gl.GLLinePlotItem(antialias=True)
        self._plot3d.plot_3d.addItem(self.critInnerArc)
        self.critOuterArc = gl.GLLinePlotItem(antialias=True)
        self._plot3d.plot_3d.addItem(self.critOuterArc)

        self.warnInnerArc = gl.GLLinePlotItem(antialias=True)
        self._plot3d.plot_3d.addItem(self.warnInnerArc)
        self.warnOuterArc = gl.GLLinePlotItem(antialias=True)
        self._plot3d.plot_3d.addItem(self.warnOuterArc)

        self.critZoneMesh = gl.GLMeshItem(
            meshdata=self._makeMeshData(self.critStart.value(), self.critEnd.value()),
            color=(1.0, 0.0, 0.0, 0.12), smooth=False, drawEdges=False, glOptions='translucent')
        self._plot3d.plot_3d.addItem(self.critZoneMesh)

        self.warnZoneMesh = gl.GLMeshItem(
            meshdata=self._makeMeshData(self.warnStart.value(), self.warnEnd.value()),
            color=(1.0, 1.0, 0.0, 0.10), smooth=False, drawEdges=False, glOptions='translucent')
        self._plot3d.plot_3d.addItem(self.warnZoneMesh)

        self.dynScatter = _SafeScatterPlotItem(size=8, pxMode=True)
        self._plot3d.plot_3d.addItem(self.dynScatter)

        self.staticScatter = _SafeScatterPlotItem(size=15, pxMode=True)
        self._plot3d.plot_3d.addItem(self.staticScatter)

        self.colorGradient = pg.GradientWidget(orientation='right')
        self.colorGradient.restoreState({
            'ticks': [(1, (255, 0, 0, 255)), (0, (131, 238, 255, 255))],
            'mode': 'hsv',
        })
        self.colorGradient.setVisible(False)

        self.drawFOVLines()
        self.drawZoneArcs()
        self.switchView(3)  # default to 3D isometric

    def _getProjectionColor(self, projPos):
        r = np.sqrt(projPos[0] ** 2 + projPos[1] ** 2)
        if r <= self._critEndVal:
            return (1.0, 0.0, 0.0, 1.0)
        if r <= self._warnEndVal:
            return (1.0, 1.0, 0.0, 1.0)
        return (0.0, 1.0, 0.0, 1.0)

    def _sensorToWorld(self, pts):
        """Transform (N,3) sensor-frame points to world frame.

        Positive elevTilt = sensor tilted downward (boresight toward floor).
        Sensor origin is at (0, 0, mountingHeight) in world frame.
        """
        h = self.mountingHeight.value()
        t = np.radians(self.elevTilt.value())
        ct, st = np.cos(t), np.sin(t)
        # Columns of R are world-frame images of sensor basis vectors:
        #   sensor +X → world +X
        #   sensor +Y (boresight) → world (0, ct, -st)  (tilts toward floor for +tilt)
        #   sensor +Z (up)        → world (0, st,  ct)
        R = np.array([[1,  0,   0 ],
                      [0,  ct,  st],
                      [0, -st,  ct]], dtype=np.float64)
        out = pts.astype(np.float64) @ R.T
        out[:, 2] += h
        return out.astype(np.float32)

    def _makeArc(self, radius):
        az_rad = np.radians(self.az_fov_deg / 2)
        angles = np.linspace(-az_rad, az_rad, 60)
        pts = np.column_stack([
            radius * np.sin(angles),
            radius * np.cos(angles),
            np.zeros(60),
        ]).astype(np.float32)
        return pts

    def _makeMeshData(self, r1, r2):
        az_rad = np.radians(self.az_fov_deg / 2)
        N = 60
        angles = np.linspace(-az_rad, az_rad, N)
        inner = np.column_stack([r1 * np.sin(angles), r1 * np.cos(angles), np.zeros(N)])
        outer = np.column_stack([r2 * np.sin(angles), r2 * np.cos(angles), np.zeros(N)])
        verts = self._sensorToWorld(np.vstack([inner, outer]).astype(np.float32))
        faces = np.array(
            [[i, i + 1, N + i] for i in range(N - 1)] +
            [[i + 1, N + i + 1, N + i] for i in range(N - 1)],
            dtype=np.uint32)
        return MeshData(vertexes=verts, faces=faces)

    def drawFOVLines(self):
        az_rad = np.radians(self.az_fov_deg / 2)
        r = self.maxRange
        gray = (0.7, 0.7, 0.7, 1.0)

        # Build in sensor frame, then lift to world frame via tilt + height
        pts_sensor = np.array([
            [0, 0, 0], [0, r, 0],                                          # boresight
            [0, 0, 0], [ r * np.sin(az_rad), r * np.cos(az_rad), 0],      # left FOV edge
            [0, 0, 0], [-r * np.sin(az_rad), r * np.cos(az_rad), 0],      # right FOV edge
        ], dtype=np.float32)
        self.fovItem.setData(pos=self._sensorToWorld(pts_sensor),
                             color=gray, width=1, mode='lines', antialias=True)

        self.fovCapItem.setData(pos=self._sensorToWorld(self._makeArc(r)),
                                color=gray, width=1, mode='line_strip', antialias=True)

    def drawZoneArcs(self):
        """Called by Apply Changes: commits spinbox values then redraws geometry."""
        if not hasattr(self, 'critInnerArc'):
            return
        self._critStartVal = self.critStart.value()
        self._critEndVal   = self.critEnd.value()
        self._warnStartVal = self.warnStart.value()
        self._warnEndVal   = self.warnEnd.value()
        self._redrawZoneGeometry()

    def _redrawZoneGeometry(self):
        """Redraws zone arcs/meshes using committed values and current sensor pose."""
        if not hasattr(self, 'critInnerArc'):
            return
        red    = (1.0, 0.0, 0.0, 1.0)
        yellow = (1.0, 1.0, 0.0, 1.0)
        visible = self.showZones.isChecked()

        for item, radius, color in [
            (self.critInnerArc, self._critStartVal, red),
            (self.critOuterArc, self._critEndVal,   red),
            (self.warnInnerArc, self._warnStartVal, yellow),
            (self.warnOuterArc, self._warnEndVal,   yellow),
        ]:
            item.setData(pos=self._sensorToWorld(self._makeArc(radius)), color=color,
                         width=2, mode='line_strip', antialias=True)
            item.setVisible(visible)

        if hasattr(self, 'critZoneMesh'):
            self._plot3d.plot_3d.removeItem(self.critZoneMesh)
            self.critZoneMesh = gl.GLMeshItem(
                meshdata=self._makeMeshData(self._critStartVal, self._critEndVal),
                color=(1.0, 0.0, 0.0, 0.12), smooth=False, drawEdges=False, glOptions='translucent')
            self.critZoneMesh.setVisible(visible)
            self._plot3d.plot_3d.addItem(self.critZoneMesh)

            self._plot3d.plot_3d.removeItem(self.warnZoneMesh)
            self.warnZoneMesh = gl.GLMeshItem(
                meshdata=self._makeMeshData(self._warnStartVal, self._warnEndVal),
                color=(1.0, 1.0, 0.0, 0.10), smooth=False, drawEdges=False, glOptions='translucent')
            self.warnZoneMesh.setVisible(visible)
            self._plot3d.plot_3d.addItem(self.warnZoneMesh)

        self._plot3d.plot_3d.update()

    # ------------------------------------------------------------------ #
    #  Config parsing                                                      #
    # ------------------------------------------------------------------ #
    def parseBoundaryBox(self, args):
        if args[0] in ('SceneryParam', 'boundaryBox') and len(args) >= 5:
            farY = float(args[4])
            if farY > 0:
                self.maxRange = farY
                if hasattr(self, 'fovItem'):
                    self.drawFOVLines()
                if hasattr(self, 'critStart'):
                    for sb in (self.critStart, self.critEnd, self.warnStart, self.warnEnd):
                        sb.setMaximum(self.maxRange)

    def parseTrackingCfg(self, args):
        self._plot3d.parseTrackingCfg(args)  # sets self._plot3d.maxTracks
        for _ in range(self._plot3d.maxTracks):
            box = gl.GLLinePlotItem()
            box.setVisible(False)
            self._plot3d.plot_3d.addItem(box)
            self._plot3d.ellipsoids.append(box)
        self.trackColorMap = get_trackColors(self._plot3d.maxTracks)

    def parseSensorPosition(self, args, is_x843):
        self._plot3d.parseSensorPosition(args, is_x843)

    def removeAllBoundBoxes(self):
        self._plot3d.removeAllBoundBoxes()

    # ------------------------------------------------------------------ #
    #  Statistics panel                                                    #
    # ------------------------------------------------------------------ #
    def initStatsPane(self):
        box = QGroupBox('Statistics')
        self.frameNumDisplay       = QLabel('Frame: 0')
        self.plotTimeDisplay       = QLabel('Plot Time: 0 ms')
        self.numDynPointsDisplay   = QLabel('Dynamic Points: 0')
        self.numStaticPtsDisplay   = QLabel('Static Points: 0')
        self.numTracksDisplay      = QLabel('Tracked Objects: 0')

        self.critZoneStatus = QLabel('Critical Zone: CLEAR')
        self.critZoneStatus.setStyleSheet('color: green; font-weight: bold')
        self.warnZoneStatus = QLabel('Warning Zone: CLEAR')
        self.warnZoneStatus.setStyleSheet('color: green; font-weight: bold')

        layout = QVBoxLayout()
        for w in (self.frameNumDisplay, self.plotTimeDisplay,
                  self.numDynPointsDisplay, self.numStaticPtsDisplay,
                  self.numTracksDisplay, self.critZoneStatus, self.warnZoneStatus):
            layout.addWidget(w)
        box.setLayout(layout)
        return box

    def _checkZoneOccupancy(self, pos_world):
        """pos_world: (N,3) world-frame positions. Checks horizontal XY ground range."""
        critOccupied = False
        warnOccupied = False
        critLo, critHi = self._critStartVal, self._critEndVal
        warnLo, warnHi = self._warnStartVal, self._warnEndVal
        for p in pos_world:
            r = np.sqrt(p[0] ** 2 + p[1] ** 2)
            if critLo <= r <= critHi:
                critOccupied = True
            if warnLo <= r <= warnHi:
                warnOccupied = True
        return critOccupied, warnOccupied

    # ------------------------------------------------------------------ #
    #  Frame update                                                        #
    # ------------------------------------------------------------------ #
    def updateGraph(self, outputDict):
        self.plotStart = int(round(time.time() * 1000))

        if 'frameNum' in outputDict:
            self.frameNumDisplay.setText('Frame: ' + str(outputDict['frameNum']))
        numPts = outputDict.get('numDetectedPoints', 0)
        self.numDynPointsDisplay.setText('Dynamic Points: ' + str(numPts))

        # Embed trackIndexes at column 6 so the thread can colour by track association
        trackIdxs = outputDict.get('trackIndexes')
        if numPts > 0 and trackIdxs is not None and len(trackIdxs) >= numPts:
            pc = outputDict['pointCloud']
            if pc.shape[1] < 7:
                padded = np.zeros((pc.shape[0], 7), dtype=np.float32)
                padded[:, :pc.shape[1]] = pc
                padded[:numPts, 6] = trackIdxs[:numPts]
                outputDict['pointCloud'] = padded

        # Accumulate with sensor-frame → world-frame transform
        self._plot3d.updatePointCloud(outputDict)

        # Build cumulative cloud from all persistent frames
        cumulativeCloud = None
        for frame in self._plot3d.previousClouds:
            if len(frame) > 0:
                cumulativeCloud = (frame if cumulativeCloud is None
                                   else np.concatenate((cumulativeCloud, frame), axis=0))

        # Transform tracks to world frame (same pattern as people_tracking.py)
        numTracks = outputDict.get('numDetectedTracks', 0)
        tracks = None
        if numTracks > 0:
            tracks = outputDict['trackData']
            for i in range(numTracks):
                rx, ry, rz = eulerRot(tracks[i, 1], tracks[i, 2], tracks[i, 3],
                                      self._plot3d.elev_tilt, self._plot3d.az_tilt)
                tracks[i, 1] = rx
                tracks[i, 2] = ry
                tracks[i, 3] = rz + self._plot3d.sensorHeight

        if self.plotComplete:
            self.plot_3d_thread = updateQTTargetThread3D(
                cumulativeCloud, tracks, self.dynScatter, self._plot3d,
                numTracks, self._plot3d.ellipsoids, [],
                colorGradient=self.colorGradient,
                pointColorMode=self.pointColorMode.currentText(),
                trackColorMap=self.trackColorMap,
            )
            self.plotComplete = 0
            self.plot_3d_thread.done.connect(lambda: self.graphDone(outputDict))
            self.plot_3d_thread.start()

    def graphDone(self, outputDict):
        # Static scatter
        numStatic = outputDict.get('numStaticDetectedPoints', 0)
        self.numStaticPtsDisplay.setText('Static Points: ' + str(numStatic))
        if numStatic > 0:
            spts_w = self._sensorToWorld(outputDict['staticPoints'][:numStatic, :3].copy())
            self.staticScatter.setData(pos=spts_w, color=(1.0, 0.0, 1.0, 1.0), size=15)
        else:
            self.staticScatter.setData(pos=np.empty((0, 3), np.float32))

        # Clear previous labels and velocity lines
        for lbl in getattr(self, 'trackLabels', []):
            self._plot3d.plot_3d.removeItem(lbl)
        self.trackLabels = []
        for vl in getattr(self, 'velLineItems', []):
            self._plot3d.plot_3d.removeItem(vl)
        self.velLineItems = []

        numTracks = outputDict.get('numDetectedTracks', 0)
        self.numTracksDisplay.setText('Tracked Objects: ' + str(numTracks))

        critOcc, warnOcc = False, False
        if numTracks > 0:
            tracks = outputDict['trackData']   # already world-frame from updateGraph
            pos_w  = tracks[:numTracks, 1:4]
            dt     = self.projTime.value() if hasattr(self, 'projTime') else 2.0
            proj_positions = []
            for i, t in enumerate(tracks[:numTracks]):
                vel_w  = np.array([float(t[4]), float(t[5]), float(t[6])])
                proj_w = pos_w[i] + vel_w * dt
                proj_positions.append(proj_w)

                color = self._getProjectionColor(proj_w)
                seg = np.array([pos_w[i], proj_w], np.float32)
                vl  = gl.GLLinePlotItem(pos=seg, color=color, width=2,
                                        mode='line_strip', antialias=True)
                self._plot3d.plot_3d.addItem(vl)
                self.velLineItems.append(vl)

                tid = int(t[0])
                lbl = GLTextItem(X=float(pos_w[i, 0]) + 0.15, Y=float(pos_w[i, 1]) + 0.15,
                                 Z=float(pos_w[i, 2]) + 0.15, text=str(tid))
                lbl.setGLViewWidget(self._plot3d.plot_3d)
                self._plot3d.plot_3d.addItem(lbl)
                self.trackLabels.append(lbl)

            critOcc, warnOcc = self._checkZoneOccupancy(
                np.vstack([pos_w, np.array(proj_positions, np.float32)])
            )

        self.warnZoneStatus.setText('Warning Zone: ' + ('OCCUPIED' if warnOcc else 'CLEAR'))
        self.warnZoneStatus.setStyleSheet(
            'color: orange; font-weight: bold' if warnOcc else 'color: green; font-weight: bold')
        self.critZoneStatus.setText('Critical Zone: ' + ('OCCUPIED' if critOcc else 'CLEAR'))
        self.critZoneStatus.setStyleSheet(
            'color: red; font-weight: bold' if critOcc else 'color: green; font-weight: bold')

        plotTime = int(round(time.time() * 1000)) - self.plotStart
        self.plotTimeDisplay.setText('Plot Time: ' + str(plotTime) + ' ms')
        self.plotComplete = 1
