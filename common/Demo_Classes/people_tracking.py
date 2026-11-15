# General Library Imports
# PyQt Imports
# Local Imports
# Logger
# # Different methods to color the points 
COLOR_MODE_SNR = 'SNR'
COLOR_MODE_HEIGHT = 'Height'
COLOR_MODE_DOPPLER = 'Doppler'
COLOR_MODE_TRACK = 'Associated Track'

MAX_PERSISTENT_FRAMES = 30

import numpy as np
import time

from PySide2.QtCore import Qt, QThread
import pyqtgraph.opengl as gl
import pyqtgraph as pg
from PySide2.QtWidgets import QGroupBox, QGridLayout, QLabel, QVBoxLayout, QComboBox, QCheckBox, QSlider, QFormLayout, QMenu, QAction, QGroupBox, QMessageBox

from Common_Tabs.plot_3d import Plot3D
from Common_Tabs.plot_1d import Plot1D
from Common_Tabs.camera_overlay import RadarCameraFusionTab
from Demo_Classes.Helper_Classes.fall_detection import *
from Demo_Classes.Helper_Classes.overhead_people_counter import *
from demo_defines import *
from graph_utilities import get_trackColors, eulerRot
from gl_text import GLTextItem

from gui_threads import updateQTTargetThread3D

import logging

log = logging.getLogger(__name__)

class PeopleTracking(Plot3D, Plot1D):
    def __init__(self):
        Plot3D.__init__(self)
        Plot1D.__init__(self)
        self.cameraRadarFusionTab = None  # Created lazily in setupGUI
        self.fallDetection = FallDetection()
        self.tabs = None
        self.cumulativeCloud = None
        self.colorGradient = pg.GradientWidget(orientation='right')
        self.colorGradient.restoreState({'ticks': [ (1, (255, 0, 0, 255)), (0, (131, 238, 255, 255))], 'mode': 'hsv'})
        self.colorGradient.setVisible(False)
        self.maxTracks = int(20) # default to 5 tracks
        self.frameTime = int(55) # default : 55 ms
        self.trackColorMap = get_trackColors(self.maxTracks)
        self.regionBoundaryLines = []
        self.regionOutlines = []
        self.regionFilled = []

    def revertSensorPositionTransforms(self, pointCloud):
        """
        Reverts the sensorPosition transformations (rotation and height offset)
        applied to the point cloud for 3D plot display.

        The camera overlay needs the original point cloud coordinates without
        sensorPosition offsets, so this function undoes:
        1. Height offset (sensorHeight)
        2. Euler rotation (azimuth and elevation tilts)

        Args:
            pointCloud: The transformed point cloud from 3D plot

        Returns:
            Point cloud with sensorPosition transforms reverted
        """
        if pointCloud is None or len(pointCloud) == 0:
            return pointCloud

        # Make a deep copy to avoid modifying the original
        revertedCloud = np.copy(pointCloud)

        # Revert height offset if it was applied
        if self.sensorHeight != 0:
            revertedCloud[:, 2] = revertedCloud[:, 2] - self.sensorHeight

        # Revert Euler rotation if it was applied
        # Apply inverse rotation (negate angles for inverse transform)
        if self.elev_tilt != 0 or self.az_tilt != 0:
            for i in range(len(revertedCloud)):
                # Apply inverse Euler rotation (negative angles)
                rotX, rotY, rotZ = eulerRot(
                    revertedCloud[i, 0],
                    revertedCloud[i, 1],
                    revertedCloud[i, 2],
                    -self.elev_tilt,  # Negative for inverse
                    -self.az_tilt     # Negative for inverse
                )
                revertedCloud[i, 0] = rotX
                revertedCloud[i, 1] = rotY
                revertedCloud[i, 2] = rotZ

        return revertedCloud


    def setupGUI(self, gridLayout, demoTabs, device, menuBar=None, app=None):
        # Init setup pane on left hand side
        statBox = self.initStatsPane()
        gridLayout.addWidget(statBox,2,0,1,1)

        demoGroupBox = self.initPlotControlPane()
        gridLayout.addWidget(demoGroupBox,3,0,1,1)
        demoTabs.addTab(self.plot_3d, '3D Plot')

        fallDetectionOptionsBox = self.initFallDetectPane()
        gridLayout.addWidget(fallDetectionOptionsBox, 4,0,1,1)

        demoTabs.addTab(self.rangePlot, 'Range Plot')

        self.addCameraTabIfSupported(demoTabs)

        self.device = device
        self.tabs = demoTabs

        self.occupancy_window = None

        if app.core.demo == "3D People Tracking":
            self.initMenuOptions(menuBar, app)
            self.initRegionBoundaries()            

    # create regions for overhead occupancy counter
    def initRegionBoundaries(self):
        # Default x range (will be updated when boundary box is parsed)
        self.region_x_min = -3
        self.region_x_max = 3

        self.createRegionRectangles()

    def createRegionRectangles(self):
        # Clear existing rectangles if any
        for rect in self.regionBoundaryLines:
            self.plot_3d.removeItem(rect)
        self.regionBoundaryLines.clear()
        self.regionOutlines.clear()
        self.regionFilled.clear()

        regions = [
            {'y_start': -2.5, 'y_end': -1, 'color': (0.0, 1.0, 0.0, 0.5), 'line_color': (0, 255, 0, 255)}, 
            {'y_start': -1, 'y_end': 1, 'color': (1.0, 0.0, 0.0, 0.5), 'line_color': (255, 0, 0, 255)}, 
            {'y_start': 1, 'y_end': 2.5, 'color': (0.0, 1.0, 0.0, 0.5), 'line_color': (0, 255, 0, 255)} 
        ]

        z_ground = 0.01  # Slightly above ground to be visible

        for region in regions:
            x_min = self.region_x_min
            x_max = self.region_x_max
            y_start = region['y_start']
            y_end = region['y_end']

            outline_pts = np.array([
                [x_min, y_start, z_ground],
                [x_max, y_start, z_ground],
                [x_max, y_end, z_ground],
                [x_min, y_end, z_ground],
                [x_min, y_start, z_ground]
            ])
            outline = gl.GLLinePlotItem(pos=outline_pts, color=region['line_color'], width=2, antialias=True)
            outline.setVisible(False)
            self.plot_3d.addItem(outline)
            self.regionOutlines.append(outline)

            # Two triangles to form a rectangle
            verts = np.array([
                [[x_min, y_start, z_ground], [x_max, y_start, z_ground], [x_max, y_end, z_ground]],
                [[x_min, y_start, z_ground], [x_max, y_end, z_ground], [x_min, y_end, z_ground]]
            ])

            colors = np.array([
                region['color'],
                region['color'],
                region['color'],
                region['color'],
                region['color'],
                region['color']
            ])

            # Create mesh with semi-transparent color
            mesh = gl.GLMeshItem(
                vertexes=verts,
                vertexColors=colors,
                smooth=False,
                drawEdges=False,
                drawFaces=True,
                glOptions='translucent'
            )
            mesh.setVisible(False)
            self.plot_3d.addItem(mesh)
            self.regionFilled.append(mesh)

        self.regionBoundaryLines = self.regionOutlines + self.regionFilled

    def initMenuOptions(self, menuBar, app):
        self.customMenuOptions = QMenu("&People Tracking Settings", app)

        # Create the Overhead Occupancy Counter submenu
        self.overheadOccupancyMenu = QMenu("&Overhead Occupancy Counter", app)

        self.overheadOccupancyCounterAction = QAction("&Enable Overhead Occupancy Counter", app)
        self.show_entry_action = QAction("&Show Entry Count", app)
        self.show_exit_action = QAction("&Show Exit Count", app)
        self.show_occupancy_action = QAction("&Show Occupancy Count", app)
        self.clear_counts_action = QAction("&Clear Counts", app)

        self.overheadOccupancyCounterAction.setCheckable(True)
        self.overheadOccupancyCounterAction.setChecked(False)
        self.overheadOccupancyCounterAction.triggered.connect(self.onEnableOverheadOccupancyCounter)

        self.show_entry_action.setCheckable(True)
        self.show_entry_action.setChecked(False)
        self.show_entry_action.setEnabled(False)
        self.show_entry_action.triggered.connect(self.onChangeOccupancyCounterDisplay)

        self.show_exit_action.setCheckable(True)
        self.show_exit_action.setChecked(False)
        self.show_exit_action.setEnabled(False)
        self.show_exit_action.triggered.connect(self.onChangeOccupancyCounterDisplay)

        self.show_occupancy_action.setCheckable(True)
        self.show_occupancy_action.setChecked(False)
        self.show_occupancy_action.setEnabled(False)
        self.show_occupancy_action.triggered.connect(self.onChangeOccupancyCounterDisplay)

        self.clear_counts_action.setEnabled(False)
        self.clear_counts_action.triggered.connect(self.onClearCounts)

        self.overheadOccupancyMenu.addAction(self.overheadOccupancyCounterAction)
        self.overheadOccupancyMenu.addSeparator()
        self.overheadOccupancyMenu.addAction(self.show_entry_action)
        self.overheadOccupancyMenu.addAction(self.show_exit_action)
        self.overheadOccupancyMenu.addAction(self.show_occupancy_action)
        self.overheadOccupancyMenu.addAction(self.clear_counts_action)

        self.customMenuOptions.addMenu(self.overheadOccupancyMenu)

        menuBar.addMenu(self.customMenuOptions)

    def addCameraTabIfSupported(self, demoTabs):
        demo_name = DEMOS_SUPPORTING_CAMERA.get(self.__class__.__name__, '')
        if demo_name:
            if self.cameraRadarFusionTab is None:
                self.cameraRadarFusionTab = RadarCameraFusionTab()
            demoTabs.addTab(self.cameraRadarFusionTab, 'Radar+Camera Fusion')

    def updateGraph(self, outputDict):
        self.plotStart = int(round(time.time()*1000))
        self.updatePointCloud(outputDict)
        self.cumulativeCloud = None
        self.cumulativeCloudLocalCoords = None

        # End the radarless camera capture option once the data from the radar starts flowing in
        if (self.cameraRadarFusionTab is not None and
            self.cameraRadarFusionTab._fully_initialized and
            self.cameraRadarFusionTab.cameraTimer is not None):
            self.cameraRadarFusionTab.endRadarlessCameraCapture()


        # Track indexes on 6843 are delayed a frame. So, delay showing the current points by 1 frame for 6843
        if ('frameNum' in outputDict and outputDict['frameNum'] > 1 and len(self.previousClouds[:-1]) > 0 and DEVICE_DEMO_DICT[self.device]["isxWRx843"]):
            # For all the previous point clouds (except the most recent, whose tracks are being computed mid-frame)
            for frame in range(len(self.previousClouds[:-1])):
                # if it's not empty
                if(len(self.previousClouds[frame]) > 0):
                    # if it's the first member, assign it equal
                    if(self.cumulativeCloud is None):
                        self.cumulativeCloud = self.previousClouds[frame]
                        self.cumulativeCloudLocalCoords = self.previousCloudsLocalCoords[frame]
                    # if it's not the first member, concatenate it
                    else:
                        self.cumulativeCloud = np.concatenate((self.cumulativeCloud, self.previousClouds[frame]),axis=0)
                        self.cumulativeCloudLocalCoords = np.concatenate((self.cumulativeCloudLocalCoords, self.previousCloudsLocalCoords[frame]),axis=0)
        elif (len(self.previousClouds) > 0):
            # For all the previous point clouds, including the current frame's
            for frame in range(len(self.previousClouds[:])):
                # if it's not empty
                if(len(self.previousClouds[frame]) > 0):
                    # if it's the first member, assign it equal
                    if(self.cumulativeCloud is None):
                        self.cumulativeCloud = self.previousClouds[frame]
                        self.cumulativeCloudLocalCoords = self.previousCloudsLocalCoords[frame]
                    # if it's not the first member, concatenate it
                    else:
                        self.cumulativeCloud = np.concatenate((self.cumulativeCloud, self.previousClouds[frame]),axis=0)
                        self.cumulativeCloudLocalCoords = np.concatenate((self.cumulativeCloudLocalCoords, self.previousCloudsLocalCoords[frame]),axis=0)
        # Camera Data
        if self.cameraRadarFusionTab is not None:
            self.cameraRadarFusionTab.updateCameraViewWithRadarData(
                self.cumulativeCloud,
                outputDict,
                self.revertSensorPositionTransforms
            )

        if ('numDetectedPoints' in outputDict):
            self.numPointsDisplay.setText('Points: '+ str(outputDict['numDetectedPoints']))
        if ('numDetectedTracks' in outputDict):
            self.numTargetsDisplay.setText('Targets: '+ str(outputDict['numDetectedTracks']))

        # Tracks
        for cstr in self.coordStr:
            cstr.setVisible(False)

        # Plot
        if (self.tabs.currentWidget() == self.plot_3d):
            if ('trackData' in outputDict):
                tracks = outputDict['trackData']
                for i in range(outputDict['numDetectedTracks']):
                    rotX, rotY, rotZ = eulerRot(tracks[i,1], tracks[i,2], tracks[i,3], self.elev_tilt, self.az_tilt)
                    tracks[i,1] = rotX
                    tracks[i,2] = rotY
                    tracks[i,3] = rotZ
                    tracks[i,3] = tracks[i,3] + self.sensorHeight

                # If there are heights to display
                if ('heightData' in outputDict):
                    if (len(outputDict['heightData']) != len(outputDict['trackData'])):
                        log.warning("WARNING: number of heights does not match number of tracks")

                    # For each height heights for current tracks
                    for height in outputDict['heightData']:
                        # Find track with correct TID
                        for track in outputDict['trackData']:
                            # Found correct track
                            if (int(track[0]) == int(height[0])):
                                tid = int(height[0])
                                # Check if tid is within bounds of coordStr list
                                if tid >= len(self.coordStr):
                                    log.warning(f"Track ID {tid} exceeds coordStr list size {len(self.coordStr)}, skipping height display")
                                    break
                                height_str = 'tid : ' + str(height[0]) + ', height : ' + str(round(height[1], 2)) + ' m'
                                # If this track was computed to have fallen, display it on the screen
                                if(self.displayFallDet.checkState() == 2):
                                    # Compute the fall detection results for each object
                                    fallDetectionDisplayResults = self.fallDetection.step(outputDict['heightData'], outputDict['trackData'])
                                    if (fallDetectionDisplayResults[tid] > 0):
                                        height_str = height_str + " FALL DETECTED"
                                self.coordStr[tid].setText(height_str)
                                self.coordStr[tid].setX(track[1])
                                self.coordStr[tid].setY(track[2])
                                self.coordStr[tid].setZ(track[3])
                                # Only show text if the checkbox is enabled
                                self.coordStr[tid].setVisible(self.display3DText.isChecked())
                                break
                
                # handles the overhead occupancy counting feature for ceiling-mounted radar sensors, tracking people entering/exiting a monitored zone.
                if hasattr(self, 'overheadOccupancyCounterAction') and self.overheadOccupancyCounterAction.isChecked() and self.occupancy_window is not None:
                    # Track which regions have active tracks
                    regions_with_tracks = [False, False, False]  # [Region1, Region2, Region3]

                    if tracks is not None and len(tracks) > 0:
                        for track in tracks:
                            t_id = int(track[0])

                            if t_id < len(self.target_region_hist):
                                self.in_count_total, self.out_count_total, self.target_region_hist = self.occupancy_window.update_people_count(self.target_region_hist, t_id, track[2], self.in_count_total, self.out_count_total)

                                 # Use the region that was already computed in target_region_hist                                                                                                                 
                                region = self.target_region_hist[t_id]['region']
                                if region == self.occupancy_window.REGION_1:
                                    regions_with_tracks[0] = True
                                elif region == self.occupancy_window.REGION_2:
                                    regions_with_tracks[1] = True
                                elif region == self.occupancy_window.REGION_3:
                                    regions_with_tracks[2] = True
                            else:
                                log.warning(f'Track ID {t_id} exceeds target_region_hist size {len(self.target_region_hist)}, skipping')

                    # Update region visualization - show filled if tracks present, outline if empty
                    if len(self.regionOutlines) == 3 and len(self.regionFilled) == 3:
                        for i in range(3):
                            if regions_with_tracks[i]:
                                self.regionOutlines[i].setVisible(False)
                                self.regionFilled[i].setVisible(True)
                            else:
                                self.regionOutlines[i].setVisible(True)
                                self.regionFilled[i].setVisible(False)

                    entryCount = None
                    exitCount = None
                    occupantCount = None

                    if self.show_entry_action.isChecked():
                        entryCount = self.in_count_total

                    if self.show_exit_action.isChecked():
                        exitCount = self.out_count_total

                    if self.show_occupancy_action.isChecked():
                        occupantCount = self.in_count_total - self.out_count_total

                    self.occupancy_window.updateCounts(entryCount, exitCount, occupantCount)

            else:
                tracks = None
            
            if (self.plotComplete):
                self.plotStart = int(round(time.time()*1000))
                self.plot_3d_thread = updateQTTargetThread3D(self.cumulativeCloud, tracks, self.scatter, self.plot_3d, 0, self.ellipsoids, "", colorGradient=self.colorGradient, pointColorMode=self.pointColorMode.currentText(), trackColorMap=self.trackColorMap)
                self.plotComplete = 0
                self.plot_3d_thread.done.connect(lambda: self.graphDone(outputDict))
                self.plot_3d_thread.start(priority=QThread.HighPriority)
        elif (self.tabs.currentWidget() == self.rangePlot):
            self.update1DGraph(outputDict)
            self.graphDone(outputDict)
        elif (self.cameraRadarFusionTab is not None and
              self.cameraRadarFusionTab._fully_initialized and
              self.tabs.currentWidget() == self.cameraRadarFusionTab.canvas.native):
            self.graphDone(outputDict)

        if ('frameNum' in outputDict):
            self.frameNumDisplay.setText('Frame: ' + str(outputDict['frameNum']))

    def graphDone(self, outputDict):
        if ('frameNum' in outputDict):
            self.frameNumDisplay.setText('Frame: ' + str(outputDict['frameNum']))

        if ('powerData' in outputDict):
            powerData = outputDict['powerData']
            self.updatePowerNumbers(powerData)

        plotTime = int(round(time.time()*1000)) - self.plotStart
        self.plotTimeDisplay.setText('Plot Time: ' + str(plotTime) + 'ms')
        self.plotComplete = 1

    def updatePowerNumbers(self, powerData):
        if powerData['power1v2'] == 65535:
            self.avgPower.setText('Average Power: N/A')
        else:
            powerStr = str((powerData['power1v2'] \
                + powerData['power1v2RF'] + powerData['power1v8'] + powerData['power3v3']) * 0.1)
            self.avgPower.setText('Average Power: ' + powerStr[:5] + ' mW')

    def initStatsPane(self):
        statBox = QGroupBox('Statistics')
        self.frameNumDisplay = QLabel('Frame: 0')
        self.plotTimeDisplay = QLabel('Plot Time: 0 ms')
        self.numPointsDisplay = QLabel('Points: 0')
        self.numTargetsDisplay = QLabel('Targets: 0')
        self.avgPower = QLabel('Average Power: 0 mw')
        self.statsLayout = QVBoxLayout()
        self.statsLayout.addWidget(self.frameNumDisplay)
        self.statsLayout.addWidget(self.plotTimeDisplay)
        self.statsLayout.addWidget(self.numPointsDisplay)
        self.statsLayout.addWidget(self.numTargetsDisplay)
        self.statsLayout.addWidget(self.avgPower)
        statBox.setLayout(self.statsLayout)
        return statBox

    def initPlotControlPane(self):
        plotControlBox = QGroupBox('Plot Controls')
        self.display3DText = QCheckBox('Show Track Labels')
        self.display3DText.setChecked(False)  # Disabled by default
        self.pointColorMode = QComboBox()
        self.pointColorMode.addItems([COLOR_MODE_SNR, COLOR_MODE_HEIGHT, COLOR_MODE_DOPPLER, COLOR_MODE_TRACK])

        self.displayFallDet = QCheckBox('Detect Falls')
        self.snapTo2D = QCheckBox('Snap to 2D')
        self.displayFallDet.stateChanged.connect(self.fallDetDisplayChanged)
        self.persistentFramesInput = QComboBox()
        self.persistentFramesInput.addItems([str(i) for i in range(1, MAX_PERSISTENT_FRAMES + 1)])
        self.persistentFramesInput.setCurrentIndex(self.numPersistentFrames - 1)
        self.persistentFramesInput.currentIndexChanged.connect(self.persistentFramesChanged)
        plotControlLayout = QFormLayout()
        plotControlLayout.addRow("Show Track Labels:", self.display3DText)
        plotControlLayout.addRow("Color Points By:",self.pointColorMode)
        plotControlLayout.addRow("Enable Fall Detection", self.displayFallDet)
        plotControlLayout.addRow("# of Persistent Frames",self.persistentFramesInput)
        plotControlLayout.addRow(self.snapTo2D)
        plotControlBox.setLayout(plotControlLayout)

        return plotControlBox

    def persistentFramesChanged(self, index):
        self.numPersistentFrames = index + 1

    def fallDetDisplayChanged(self, state):
        if state:
            self.fallDetectionOptionsBox.setVisible(True)
        else:
            self.fallDetectionOptionsBox.setVisible(False)

    def updateFallDetectionSensitivity(self):
        self.fallDetection.setFallSensitivity(((self.fallDetSlider.value() / self.fallDetSlider.maximum()) * 0.4) + 0.4) # Range from 0.4 to 0.8

    def initFallDetectPane(self):
        self.fallDetectionOptionsBox = QGroupBox('Fall Detection Sensitivity')
        self.fallDetLayout = QGridLayout()
        self.fallDetSlider = FallDetectionSliderClass(Qt.Horizontal)
        self.fallDetSlider.setTracking(True)
        self.fallDetSlider.setTickPosition(QSlider.TicksBothSides)
        self.fallDetSlider.setTickInterval(10)
        self.fallDetSlider.setRange(0, 100)
        self.fallDetSlider.setSliderPosition(50)
        self.fallDetSlider.valueChanged.connect(self.updateFallDetectionSensitivity)
        self.lessSensitiveLabel = QLabel("Less Sensitive")
        self.fallDetLayout.addWidget(self.lessSensitiveLabel,0,0,1,1)
        self.moreSensitiveLabel = QLabel("More Sensitive")
        self.fallDetLayout.addWidget(self.moreSensitiveLabel,0,10,1,1)
        self.fallDetLayout.addWidget(self.fallDetSlider,1,0,1,11)
        self.fallDetectionOptionsBox.setLayout(self.fallDetLayout)
        if(self.displayFallDet.checkState() == 2):
            self.fallDetectionOptionsBox.setVisible(True)
        else:
            self.fallDetectionOptionsBox.setVisible(False)

        return self.fallDetectionOptionsBox

    def parseBoundaryBox(self, args):
        super().parseBoundaryBox(args)

        # Update region rectangles with boundary box X limits
        if len(self.boundaryBoxViz) > 0:
            bbox = self.boundaryBoxViz[0]
            self.region_x_min = bbox['minX']
            self.region_x_max = bbox['maxX']

            self.createRegionRectangles()

            # If occupancy counter is enabled, make sure outlines are visible
            if hasattr(self, 'overheadOccupancyCounterAction') and self.overheadOccupancyCounterAction.isChecked():
                for outline in self.regionOutlines:
                    outline.setVisible(True)
                for filled in self.regionFilled:
                    filled.setVisible(False)

    def parseTrackingCfg(self, args):
        self.maxTracks = int(args[4])
        # Parse frameTime from trackingCfg only when CLI format with 7 params is used
        if len(args) == 8:
            self.frameTime = int(args[7])
        else:
            self.frameTime = None
        self.updateNumTracksBuffer() # Update the max number of tracks based off the config file
        self.trackColorMap = get_trackColors(self.maxTracks)
        for m in range(self.maxTracks):
            # Add track gui object
            mesh = gl.GLLinePlotItem()
            mesh.setVisible(False)
            self.plot_3d.addItem(mesh)
            self.ellipsoids.append(mesh)
            # Add track coordinate string
            text = GLTextItem()
            text.setGLViewWidget(self.plot_3d)
            text.setVisible(False)
            self.plot_3d.addItem(text)
            self.coordStr.append(text)
            # Add track classifier label string
            classifierText = GLTextItem()
            classifierText.setGLViewWidget(self.plot_3d)
            classifierText.setVisible(False)
            self.plot_3d.addItem(classifierText)
            self.classifierStr.append(classifierText)

    def updateNumTracksBuffer(self):
        # Update max number of tracks and frame period for Fall Detection
        if self.frameTime is not None:
            self.fallDetection = FallDetection(self.maxTracks, self.frameTime)
        else:
            self.fallDetection = FallDetection(self.maxTracks)

    def onEnableOverheadOccupancyCounter(self):
        # only want to allow this option for overhead mounted scenarios
        if self.overheadOccupancyCounterAction.isChecked():
            if 45 <= self.elev_tilt <= 135:
                self.in_count_total = 0
                self.out_count_total = 0

                if self.occupancy_window is None:
                    self.occupancy_window = OccupancyWindow()

                self.target_region_hist = [
                    {
                        'region': self.occupancy_window.UNKNOWN_REGION,
                        'prevRegion': self.occupancy_window.UNKNOWN_REGION,
                        'state': self.occupancy_window.UNKNOWN_STATE,
                        'prevState': self.occupancy_window.UNKNOWN_STATE
                    } for _ in range(self.maxTracks)
                ]

                self.show_entry_action.setEnabled(True)
                self.show_exit_action.setEnabled(True)
                self.show_occupancy_action.setEnabled(True)
                self.clear_counts_action.setEnabled(True)

                self.show_entry_action.setChecked(True)
                self.show_exit_action.setChecked(True)
                self.show_occupancy_action.setChecked(True)

                self.occupancy_window.updateCounts(0, 0, 0)
                self.occupancy_window.show()

                # Show the region outlines in the 3D plot 
                for outline in self.regionOutlines:
                    outline.setVisible(True)
                for filled in self.regionFilled:
                    filled.setVisible(False)

            else:
                self.overheadOccupancyCounterAction.setChecked(False)
                # Show a warning popup
                msg = QMessageBox()
                msg.setIcon(QMessageBox.Warning)
                msg.setWindowTitle("Occupancy Counter")
                msg.setText("Occupancy counter is only supported for ceiling mount.\nElevation tilt angle must be between 45 and 135 degrees.\nPlease select a cfg that meets these requirements.")
                msg.setStandardButtons(QMessageBox.Ok)
                msg.exec_()
        else:
            self.show_entry_action.setEnabled(False)
            self.show_exit_action.setEnabled(False)
            self.show_occupancy_action.setEnabled(False)
            self.clear_counts_action.setEnabled(False)

            self.show_entry_action.setChecked(False)
            self.show_exit_action.setChecked(False)
            self.show_occupancy_action.setChecked(False)

            self.occupancy_window.hide()

            # Hide both region outlines and filled versions in the 3D plot
            for outline in self.regionOutlines:
                outline.setVisible(False)
            for filled in self.regionFilled:
                filled.setVisible(False)

    def onChangeOccupancyCounterDisplay(self):
        entryCount = None
        exitCount = None
        occupantCount = None

        if self.show_entry_action.isChecked():
            entryCount = self.in_count_total

        if self.show_exit_action.isChecked():
            exitCount = self.out_count_total

        if self.show_occupancy_action.isChecked():
            occupantCount = self.in_count_total - self.out_count_total

        self.occupancy_window.updateCounts(entryCount, exitCount, occupantCount)

    def onClearCounts(self):
        # Reset the entry, exit, and occupant counts
        self.in_count_total = 0
        self.out_count_total = 0

        # Reset the target region history
        self.target_region_hist = [
            {
                'region': self.occupancy_window.UNKNOWN_REGION,
                'prevRegion': self.occupancy_window.UNKNOWN_REGION,
                'state': self.occupancy_window.UNKNOWN_STATE,
                'prevState': self.occupancy_window.UNKNOWN_STATE
            } for _ in range(self.maxTracks)
        ]

        # Update the display with the reset counts
        entryCount = None
        exitCount = None
        occupantCount = None

        if self.show_entry_action.isChecked():
            entryCount = 0

        if self.show_exit_action.isChecked():
            exitCount = 0

        if self.show_occupancy_action.isChecked():
            occupantCount = 0

        self.occupancy_window.updateCounts(entryCount, exitCount, occupantCount)
