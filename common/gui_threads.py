# General Library Imports
import numpy as np

# PyQt imports
from PySide2.QtCore import QThread, Signal
import pyqtgraph as pg
import pyqtgraph.opengl as gl

# Local Imports
from gui_parser import UARTParser
from gui_common import *
from graph_utilities import *

# Logger
import logging
log = logging.getLogger(__name__)


# Expected minimums and maximums to bound the range of colors used for coloring points
SNR_EXPECTED_MIN = 5
SNR_EXPECTED_MAX = 40
SNR_EXPECTED_RANGE = SNR_EXPECTED_MAX - SNR_EXPECTED_MIN
DOPPLER_EXPECTED_MIN = -30
DOPPLER_EXPECTED_MAX = 30
DOPPLER_EXPECTED_RANGE = DOPPLER_EXPECTED_MAX - DOPPLER_EXPECTED_MIN

# Different methods to color the points 
COLOR_MODE_SNR = 'SNR'
COLOR_MODE_HEIGHT = 'Height'
COLOR_MODE_DOPPLER = 'Doppler'
COLOR_MODE_TRACK = 'Associated Track'
COLOR_MODE_3DPT = 'MajAndMin'

# Magic Numbers for Target Index TLV
TRACK_INDEX_WEAK_SNR = 253 # Point not associated, SNR too weak
TRACK_INDEX_BOUNDS = 254 # Point not associated, located outside boundary of interest
TRACK_INDEX_NOISE = 255 # Point not associated, considered as noise

MAJOR_MOTION = 0
MINOR_MOTION = 1

FAM_STATE_UNKNOWN = 0
FAM_STATE_P_HUMAN = 1
FAM_STATE_C_HUMAN = 2

class parseUartThread(QThread):
    fin = Signal(dict)

    def __init__(self, uParser, demo):
            QThread.__init__(self)
            self.parser = uParser
            self.demo = demo

    def run(self):
        if(self.parser.parserType == "SingleCOMPort"):
            outputDict = self.parser.readAndParseUartSingleCOMPort()
        else:
            outputDict = self.parser.readAndParseUartDoubleCOMPort(self.demo)
        self.fin.emit(outputDict)

    def stop(self):
        self.terminate()

class sendCommandThread(QThread):
    done = Signal()
    def __init__(self, uParser, command):
            QThread.__init__(self)
            self.parser = uParser
            self.command = command

    def run(self):
        self.parser.sendLine(self.command)
        self.done.emit()

class updateQTTargetThread3D(QThread):
    done = Signal()

    def __init__(self, pointCloud, targets, scatter, pcplot, numTargets, ellipsoids, coords, demo=None, colorGradient=None, classifierOut=[], zRange=[-3, 3], pointColorMode="", drawTracks=True, trackColorMap=None, pointBounds={'enabled': False}, clusterLocs = None, famDataToPlot=None):
        QThread.__init__(self)
        self.pointCloud = pointCloud
        self.targets = targets
        self.scatter = scatter
        self.clusterLocs = clusterLocs
        self.pcplot = pcplot
        self.colorArray = ('r','g','b','w')
        self.numTargets = numTargets
        self.ellipsoids = ellipsoids
        self.coordStr = coords
        self.classifierOut = classifierOut
        self.zRange = zRange
        self.colorGradient = colorGradient
        self.pointColorMode = pointColorMode
        self.drawTracks = drawTracks
        self.trackColorMap = trackColorMap
        self.pointBounds = pointBounds
        self.demo = demo

        # FAM-specific attributes
        self.famDataToPlot = famDataToPlot

        # This ignores divide by 0 errors when calculating the log2
        np.seterr(divide = 'ignore')

    def setTrackIndices(self, Indices):
        self.trackIndices = Indices

    def drawFamCentroid(self, centroid, state, ellipsoid):
        x, y, z = centroid[0], centroid[1], centroid[2]

        if state == FAM_STATE_UNKNOWN:
            color = pg.glColor('k')  # Black - unknown
        elif state == FAM_STATE_P_HUMAN:
            color = pg.glColor('c')  # Cyan - possible human
            text = 'p human'
        elif state == FAM_STATE_C_HUMAN:
            color = pg.glColor('g')  # Green - confirmed human
        else:
            color = pg.glColor('k')  # Black - default

        mesh = getBoxLinesCoords(x, y, z)
        ellipsoid.setData(pos=mesh, color=color, width=3, antialias=True, mode='lines')
        ellipsoid.setVisible(True)

    def drawCordonedZone(self, centroid, meshItem, radius):
        x, y, z = centroid[0], centroid[1], centroid[2]

        meshdata = getFilledCircleMesh(x, y, z, radius)

        # Set the mesh with yellow color and transparency
        meshItem.setMeshData(meshdata=meshdata)
        meshItem.setColor((1, 1, 0, 0.3))  # Yellow with 30% opacity (RGBA)
        meshItem.setGLOptions('translucent')
        meshItem.setVisible(True)

    def drawTrack(self, track, trackColor):

        # Get necessary track data
        tid = int(track[0])
        x = track[1]
        y = track[2]
        z = track[3]

        # If a hard coded config enables tracker, make sure tracks are still initialized for visualization
        if tid >= len(self.ellipsoids):
            log.warning(f"Track ID {tid} exceeds ellipsoids list size {len(self.ellipsoids)}, extending list")
            log.warning(f"If device was ran with hard-coded config, ignore this warning")
            log.warning(f"If device was ran with an uploaded .cfg, ensure trackingCfg's numTracks parameter is correct")
            # Create new track visualization objects for missing indices
            for _ in range(len(self.ellipsoids), tid + 1):
                mesh = gl.GLLinePlotItem()
                mesh.setVisible(False)
                self.pcplot.addItem(mesh)
                self.ellipsoids.append(mesh)

        track = self.ellipsoids[tid]
        mesh = getBoxLinesCoords(x,y,z)
        track.setData(pos=mesh,color=trackColor,width=2,antialias=True,mode='lines')
        track.setVisible(True)

    def drawTrackDynamic(self, track, trackColor):
        # Get necessary track data
        tid = int(track[0])
        # Addressing sideways EVM for Pose Estimation
        z = track[1]
        y = track[2]
        x = track[3]
        track = self.ellipsoids[tid]
        mesh = getBoxLinesCoordsDynamic(x,y,z)
        track.setData(pos=mesh,color=trackColor,width=2,antialias=True,mode='lines')
        track.setVisible(True)

    # Return transparent color if pointBounds is enabled and point is outside pointBounds
    # Otherwise, color the point depending on which color mode we are in    
    def getPointColors(self, i):
        if (self.pointBounds['enabled']) :
            xyz_coords = self.pointCloud[i,0:3]
            if (   xyz_coords[0] < self.pointBounds['minX']
                or xyz_coords[0] > self.pointBounds['maxX']
                or xyz_coords[1] < self.pointBounds['minY']
                or xyz_coords[1] > self.pointBounds['maxY']
                or xyz_coords[2] < self.pointBounds['minZ']
                or xyz_coords[2] > self.pointBounds['maxZ']
                ) :
                return pg.glColor((0,0,0,0))

        # Color the points by their SNR
        if (self.pointColorMode == COLOR_MODE_SNR):
            snr = self.pointCloud[i,4]
            # SNR value is out of expected bounds, make it white
            if (snr < SNR_EXPECTED_MIN) or (snr > SNR_EXPECTED_MAX):
                return pg.glColor('w')
            else:
                return pg.glColor(self.colorGradient.getColor((snr-SNR_EXPECTED_MIN)/SNR_EXPECTED_RANGE))

        # Color the points by their Height
        elif (self.pointColorMode == COLOR_MODE_HEIGHT):
            zs = self.pointCloud[i, 2]

            # Points outside expected z range, make it white
            if (zs < self.zRange[0]) or (zs > self.zRange[1]):
                return pg.glColor('w')
            else:
                colorRange = self.zRange[1]+abs(self.zRange[0]) 
                zs = self.zRange[1] - zs 
                return pg.glColor(self.colorGradient.getColor(abs(zs/colorRange)))

        # Color Points by their doppler
        elif(self.pointColorMode == COLOR_MODE_DOPPLER):
            doppler = self.pointCloud[i,3]
            # Doppler value is out of expected bounds, make it white
            if (doppler < DOPPLER_EXPECTED_MIN) or (doppler > DOPPLER_EXPECTED_MAX):
                return pg.glColor('w')
            else:
                return pg.glColor(self.colorGradient.getColor((doppler-DOPPLER_EXPECTED_MIN)/DOPPLER_EXPECTED_RANGE))
                
        # Color the points by their associate track
        elif (self.pointColorMode == COLOR_MODE_TRACK):
            if(self.demo == "DEMO_3DPT_6844"):
                try:
                    self.trackIndices[i]
                except:
                    return pg.glColor('w')

                trackIndex = int(self.trackIndices[i])
            else:
                trackIndex = int(self.pointCloud[i, 6])

            # trackIndex of 253, 254, or 255 indicates a point isn't associated to a track, so check for those magic numbers here
            if (trackIndex == TRACK_INDEX_WEAK_SNR or trackIndex == TRACK_INDEX_BOUNDS or trackIndex == TRACK_INDEX_NOISE):
                return pg.glColor('w')
            else:
                # Catch any errors that may occur if track or point index go out of bounds
                try:
                    return self.trackColorMap[trackIndex]
                except Exception as e:
                    log.error(e)
                    return pg.glColor('w')
        # Color the points by their associate track
        elif (self.pointColorMode == COLOR_MODE_3DPT):
            if(self.demo == "DEMO_3DPT_6844"):
                pointType = self.pointCloud[i, 7]
                
                if(pointType == MAJOR_MOTION):
                    return pg.glColor(9, 218, 12, 200)
                elif(pointType == MINOR_MOTION):
                    return pg.glColor(226, 12, 12, 200)
            else:
                pointType = int(self.pointCloud[i, 3])
                # trackIndex of 253, 254, or 255 indicates a point isn't associated to a track, so check for those magic numbers here
                if (pointType == MAJOR_MOTION):
                    return pg.glColor('w')
                elif (pointType == MINOR_MOTION):
                    return pg.glColor('w')


        # Unknown Color Option, make all points green
        else:
            return pg.glColor('g')

    def run(self):

        if (self.ellipsoids is not None):
            # Clear all previous targets
            for e in self.ellipsoids:
                if (e.visible()):
                    e.hide()

        if self.famDataToPlot is not None:
            # Clear FAM ellipsoids
            if self.famDataToPlot["ellipsoids"] is not None:
                for e in self.famDataToPlot["ellipsoids"]:
                    if e.visible():
                        e.hide()

            # Clear FAM cordon areas
            if self.famDataToPlot["cordonedMeshes"] is not None:
                for m in self.famDataToPlot["cordonedMeshes"]:
                    if m.visible():
                        m.hide()

        try:
            # Create a list of just X, Y, Z values to be plotted
            if(self.pointCloud is not None):
                toPlot = self.pointCloud[:, 0:3]

                # Determine the size of each point based on its SNR
                with np.errstate(divide='ignore'):
                    size = np.log2(self.pointCloud[:, 4])
                
                if self.pointColorMode == COLOR_MODE_3DPT:
                    size = 10

                if self.demo == "DEMO_3DPT_6844":
                    if self.pointColorMode == COLOR_MODE_TRACK:
                        size = 10
                    elif self.pointColorMode == COLOR_MODE_3DPT:
                        size = np.zeros(self.pointCloud.shape[0])

                # Each color is an array of 4 values, so we need an numPoints*4 size 2d array to hold these values
                pointColors = np.zeros((self.pointCloud.shape[0], 4))

                # Set the color/size of each point
                for i in range(self.pointCloud.shape[0]):
                    pointColors[i] = self.getPointColors(i)
                    if self.demo == "DEMO_3DPT_6844" and self.pointColorMode == COLOR_MODE_3DPT:
                        size[i] =  14 if (self.pointCloud[i, 7] == MAJOR_MOTION) else 8

                # Add clusters to the list of points to plot, make them large and red to distinguish from point cloud
                if(self.clusterLocs is not None):
                    toPlot = np.concatenate((toPlot, self.clusterLocs), axis = 0)
                    clusterColor = np.tile(np.array([1,0,0,1]), (len(self.clusterLocs),1))
                    clusterSize = np.tile(np.array([8]), len(self.clusterLocs))
                    pointColors = np.concatenate((pointColors, clusterColor), axis = 0)
                    size = np.concatenate((size, clusterSize), axis = 0)
                # Plot the points
                self.scatter.setData(pos=toPlot, color=pointColors, size=size)
                # Make the points visible 
                self.scatter.setVisible(True)
            elif (self.pointCloud is None and self.famDataToPlot is None):
                # Make the points invisible if none are detected.
                self.scatter.setVisible(False)
        except Exception as e:
            log.error(f"Unable to draw point cloud, ignoring and continuing execution... {str(e)}")

        # Graph the targets
        try:
            if (self.drawTracks):
                if (self.targets is not None):
                    for track in self.targets:
                        trackID = int(track[0])
                        trackColor = self.trackColorMap[trackID]
                        if (self.demo == "point_cloud_classification"):
                            self.drawTrackDynamic(track,trackColor)
                        else:
                            self.drawTrack(track,trackColor)
        except Exception as e:
            log.error(f"Unable to draw all tracks: {str(e)}")
            import traceback
            log.error(traceback.format_exc())

        try:
            # Plot the False Alarm Mitigation Outputs
            if self.famDataToPlot is not None:
                if (self.famDataToPlot["centroids"] is not None and
                    self.famDataToPlot["states"] is not None and
                    self.famDataToPlot["ellipsoids"] is not None):

                    numCentroids = min(len(self.famDataToPlot["centroids"]),
                                     len(self.famDataToPlot["states"]),
                                     len(self.famDataToPlot["ellipsoids"]))

                    ellipsoidIdx = 0
                    meshIdx = 0

                    for i in range(numCentroids):
                        state = self.famDataToPlot["states"][i]

                        if (self.famDataToPlot["showCordonedAreas"]):
                            if meshIdx < len(self.famDataToPlot["cordonedMeshes"]):
                                self.drawCordonedZone(self.famDataToPlot["centroids"][i],
                                                    self.famDataToPlot["cordonedMeshes"][meshIdx],
                                                    radius=1.2)
                                meshIdx += 1
                        elif (self.famDataToPlot["showCentroids"]):
                            if ellipsoidIdx < len(self.famDataToPlot["ellipsoids"]):
                                self.drawFamCentroid(self.famDataToPlot["centroids"][i], state, self.famDataToPlot["ellipsoids"][i])
                                ellipsoidIdx += 1

        except Exception as e:
            log.error(f"Unable to draw FAM centroids: {str(e)}")
            import traceback
            log.error(traceback.format_exc())

        self.done.emit()

    def stop(self):
        self.terminate()
