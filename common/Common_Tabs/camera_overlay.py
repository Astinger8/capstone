# General Library Imports
# PyQt Imports
# Local Imports
# Logger
# # Different methods to color the points
import numpy as np
import time
import math
import threading
from PySide2.QtCore import QTimer, Qt
from PySide2.QtWidgets import QGroupBox, QGridLayout, QLabel, QWidget, QVBoxLayout, QTabWidget, QComboBox, QCheckBox, QSlider, QFormLayout
from graph_utilities import get_trackColors, eulerRot
from cv2 import VideoCapture, VideoWriter, VideoWriter_fourcc, cvtColor, COLOR_BGR2RGB, COLOR_RGBA2BGR, CAP_PROP_FRAME_WIDTH, CAP_PROP_FRAME_HEIGHT, CAP_DSHOW
try:
    from cv2 import setLogLevel
except ImportError:
    from cv2.utils.logging import setLogLevel

import vispy.scene
from vispy.scene import visuals
from vispy.visuals.filters import Alpha
from vispy.visuals.transforms import STTransform
from vispy.io import read_png
from vispy.color import Color

import logging

# Global Variables
COLOR_MODE_SNR = 'SNR'
COLOR_MODE_HEIGHT = 'Height'
COLOR_MODE_DOPPLER = 'Doppler'
COLOR_MODE_TRACK = 'Associated Track'

# Enable Tracker
ENABLE_RADAR_TRACKING = 1
NUMBER_OF_TRACKS = 10

log = logging.getLogger(__name__)

class CameraHardware:
    """Singleton managing physical camera hardware across all demos"""
    _instance = None
    _lock = threading.Lock()

    def __init__(self):

        # cv2 log level command to suppress warnings
        setLogLevel(0)
        
        self.videoCaptureObj = None
        self.videoWriterObj = None

        self.xResolution = int(640)
        self.yResolution = int(480)

        # Where the points are seen in the screen
        self.frameTop = None
        self.frameBottom = None
        self.frameLeft = None
        self.frameRight = None

        # Radar Location offset from Camera
        self.radar_camera_x_offset = 0
        self.radar_camera_y_offset = 0
        self.radar_camera_z_offset = 0

        # Extrinsic rotation (radians)
        self.radar_camera_pitch = 0  # alpha - rotation about X axis
        self.radar_camera_yaw = 0    # beta - rotation about Y axis
        self.radar_camera_roll = 0   # gamma - rotation about Z axis

        # Lens distortion coefficients
        self.distortion_k = [0, 0, 0, 0, 0, 0]  # k1-k6 radial
        self.distortion_p = [0, 0]               # p1-p2 tangential
        self.distortion_s = [0, 0, 0, 0]         # s1-s4 thin prism

        # Cached matrices (built when parameters change)
        self.extrinsic_matrix = None
        self.intrinsic_matrix = None

        # Camera parameters
        self.xResolution = None
        self.yResolution = None
        self.xFoV = 60  # Dummy placeholder value
        self.yFoV = 60  # Dummy placeholder value
        self.XTransformParameter = None  # Equal to the X resolution * tan(FoV)
        self.YTransformParameter = None  # Equal to the Y resolution * tan(FoV)

        self.fourcc = None
        self.frameRate = 10  # default frame rate is 10 Hz

        # Index of the camera that's in use
        self.videoCaptureIdx = 0
        # Path where video files will be saved
        self.videoFilePath = ""
        self.videoRecordingEnabled = False

        # Set to 1 when the camera is turned on and ready to go
        self.onState = 0

    @classmethod
    def get_instance(cls):
        """Thread-safe singleton access"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # Set the location of the video file that will be saved in the computer
    def setFilePath(self, filePath):
        self.videoFilePath = filePath

    def isOn(self):
        return self.onState

    # Turns on the video capture. Sets default values for the camera
    def turnVideoOn(self):
        logging.info(f"Opening camera index {self.videoCaptureIdx} with DirectShow backend")
        self.videoCaptureObj = VideoCapture(self.videoCaptureIdx, CAP_DSHOW)

        # Verify the camera opened successfully
        if not self.videoCaptureObj.isOpened():
            logging.error(f"Failed to open camera index {self.videoCaptureIdx}")
            self.videoCaptureObj = None
            return False

        # Test if we can actually read from the camera
        ret, frame = self.videoCaptureObj.read()
        if not ret:
            logging.error(f"Camera {self.videoCaptureIdx} opened but cannot read frames")
            self.videoCaptureObj.release()
            self.videoCaptureObj = None
            return False

        logging.info(f"Successfully opened camera {self.videoCaptureIdx}")
        self.fourcc = VideoWriter_fourcc(*'mp4v')
        # Gets default values for the resolution and FoV by default values
        [self.xResolution, self.yResolution, self.xFoV, self.yFoV] = self.checkDefaultResolutionAndFoV()
        # Sets default values for the resolution and FoV by default values
        self.setResolutionAndFoV(self.xResolution, self.yResolution, self.xFoV, self.yFoV)
        self.onState = 1
        return True

    # Disables the camera and closes the hardware
    def turnVideoOff(self):
        if self.isOn():
            self.videoCaptureObj.release()
        self.stopRawRecording()
        self.fourcc = None
        self.videoCaptureObj = None
        self.onState = 0

    def startRawRecording(self, path):
        """Start recording raw camera footage (no overlay) to a video file"""
        if not self.isOn():
            logging.warning("Cannot start raw recording — camera is not on")
            return False
        self.stopRawRecording()
        self.videoFilePath = path
        self.videoWriterObj = VideoWriter(path, self.fourcc, self.frameRate,
                                          (int(self.xResolution), int(self.yResolution)))
        if not self.videoWriterObj.isOpened():
            logging.error(f"Failed to open raw video writer at {path}")
            self.videoWriterObj = None
            return False
        self.videoRecordingEnabled = True
        logging.info(f"Started raw camera recording to {path}")
        return True

    def stopRawRecording(self):
        """Stop recording raw camera footage"""
        self.videoRecordingEnabled = False
        if self.videoWriterObj is not None:
            self.videoWriterObj.release()
            self.videoWriterObj = None
            logging.info(f"Stopped raw camera recording")

    # Sets the camera parameters
    def setResolutionAndFoV(self, XRes, YRes, XFoV, YFoV):
        self.xResolution = XRes
        self.yResolution = YRes
        self.xFoV = XFoV
        self.yFoV = YFoV
        self.XTransformParameter = (0.5 * XRes) / np.tan((XFoV / 2) * 3.14159 / 180.0)
        self.YTransformParameter = (0.5 * YRes) / np.tan((YFoV / 2) * 3.14159 / 180.0)

        self.frameTop = self.yResolution/2
        self.frameBottom = -self.yResolution/2
        self.frameLeft = -self.xResolution/2
        self.frameRight = self.xResolution/2

    # Gets the camera parameters that are currently set
    def getResolutionAndFoV(self):
        return [self.xResolution, self.yResolution, self.xFoV, self.yFoV]

    # Gets the location of the radar relative to the camera
    def getRadarCameraOffsets(self):
        return [self.radar_camera_x_offset, self.radar_camera_y_offset, self.radar_camera_z_offset]

    # Sets the location of the radar relative to the camera
    def setRadarCameraOffsets(self, x_o, y_o, z_o):
        self.radar_camera_x_offset = x_o
        self.radar_camera_y_offset = y_o
        self.radar_camera_z_offset = z_o
        self._buildExtrinsicMatrix()

    # Builds the 4x3 extrinsic matrix (rotation + translation) for row-vector math
    def _buildExtrinsicMatrix(self):
        alpha = self.radar_camera_pitch
        beta = self.radar_camera_yaw
        gamma = self.radar_camera_roll

        # Rotation about X axis (pitch)
        rotation_pitch = np.array([
            [1, 0, 0],
            [0, np.cos(alpha), -np.sin(alpha)],
            [0, np.sin(alpha), np.cos(alpha)]
        ])

        # Rotation about Y axis (yaw)
        rotation_yaw = np.array([
            [np.cos(beta), 0, np.sin(beta)],
            [0, 1, 0],
            [-np.sin(beta), 0, np.cos(beta)]
        ])

        # Rotation about Z axis (roll)
        rotation_roll = np.array([
            [np.cos(gamma), -np.sin(gamma), 0],
            [np.sin(gamma), np.cos(gamma), 0],
            [0, 0, 1]
        ])

        # Combined rotation: yaw * pitch * roll (for row-vector multiplication)
        rotation = np.matmul(rotation_yaw, np.matmul(rotation_pitch, rotation_roll))

        # Translation vector in camera coordinates
        # Radar offsets: x=right, y=forward, z=up
        # Camera coords: x=right, y=down, z=forward
        # Mapping: cam_x = radar_x, cam_y = radar_z, cam_z = radar_y
        translation = np.array([[self.radar_camera_x_offset, self.radar_camera_z_offset, self.radar_camera_y_offset]])

        # 4x3 matrix: [rotation (3x3); translation (1x3)]
        self.extrinsic_matrix = np.append(rotation, translation, axis=0)

    # Asks the camera what the resolution and field of view are in the camera hardware
    def checkDefaultResolutionAndFoV(self):
        xres = int(round(self.videoCaptureObj.get(CAP_PROP_FRAME_WIDTH)))   # float `width`
        yres = int(round(self.videoCaptureObj.get(CAP_PROP_FRAME_HEIGHT)))  # float `height`
        xfov = 60  # Placeholder default value
        yfov = 60  # Placeholder default value
        return [xres, yres, xfov, yfov]

    # Changes the source of the video from one camera to another
    def changeVideoSource(self, idx):
        self.turnVideoOff()
        self.videoCaptureIdx = idx
        success = self.turnVideoOn()
        if not success:
            logging.error(f"Failed to open camera index {idx}")
        return success

    # Get all the video cameras available to the system
    def getAvailableVideoCameras(self):
        # checks the first 3 indexes.
        index = 0
        arr = []
        i = 3
        while i > 0:
            try:
                cap = VideoCapture(index, CAP_DSHOW)
                if cap.read()[0]:
                    arr.append(index)
                    cap.release()
                index += 1
                i -= 1
            except:
                index += 1
                i -= 1
                pass
        return arr


# Class for the tab that shows the camera + radar fusion screen
class RadarCameraFusionTab(QWidget):
    def __init__(self):
        super().__init__()

        # Lightweight initialization - no heavy resources yet
        self._fully_initialized = False
        self.camera_hardware = None  # Lazy - will be set to singleton on first show
        self.canvas = None  # Lazy - VisPy canvas
        self.view = None  # Lazy
        self.scatterVispy = None  # Lazy
        self.image = None  # Lazy
        self.axis = None  # Lazy
        self.radarTrack = None  # Lazy
        self.trackBoxes = None  # Lazy
        self.classificationLabel = None  # Lazy

        # Create placeholder widget to show before init
        layout = QVBoxLayout(self)
        self.placeholder_label = QLabel("Camera view will initialize when you enable the camera and switch to this tab...")
        self.placeholder_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.placeholder_label)

        self.cameraPointCloud = None
        self.cameraTimer = None
        self._last_capture_time = 0.0   # for throttling double reads
        self._cached_transform_res = (None, None)  # for STTransform caching

        self.otherTrackColor = (1.0, 1.0, 1.0, 1.0)  # White for non-closest tracks

        self.trackBoxBaseWidth = 60   # Base width in pixels
        self.trackBoxBaseHeight = 100  # Base height in pixels

        # Overlay video recording
        self.overlayVideoWriter = None
        self.overlayVideoEnabled = False
        self.overlayVideoPath = None

        # togglePointsColorByDoppler boolean
        self.togglePointsColorByDopplerBool = False

    def _lazy_init_graphics(self):
        """Initialize heavy VisPy canvas and camera resources"""
        if self._fully_initialized:
            return

        log.info("Initializing camera visualization graphics...")

        # Get singleton camera hardware
        self.camera_hardware = CameraHardware.get_instance()

        # Create VisPy canvas (HEAVY - ~50MB, OpenGL context)
        self.canvas = vispy.scene.SceneCanvas(keys='interactive')
        self.view = self.canvas.central_widget.add_view()
        self.scatterVispy = visuals.Markers(alpha=1)

        # add some dummy data
        samplPC = np.empty([10, 3])
        samplPC[:, :] = 0
        self.scatterVispy.set_data(samplPC, edge_width=0, size=0, symbol='o')
        self.scatterVispy.order = 0  # Draw point cloud on top of camera image
        self.view.add(self.scatterVispy)

        # Setup camera type and default zoom distance
        self.view.camera = 'panzoom'
        self.view.camera.aspect = 1
        half_w = (self.camera_hardware.xResolution or 640) / 2
        half_h = (self.camera_hardware.yResolution or 480) / 2
        self.view.camera.set_range(x=(-half_w, half_w), y=(half_h, -half_h))

        # Create visual cubes that are invisible, which become visible once assigned to a trackid 
        self.radarTrack = [visuals.Box(0.5, 1.0, 0.5, vertex_colors=None, color="red", edge_color="black", parent=None)
                            for i in range(NUMBER_OF_TRACKS)]
        for i in range(NUMBER_OF_TRACKS):
            self.radarTrack[i].attach(Alpha(0.3))

        self.image = visuals.Image(data=None, parent=self.view.scene)
        self.image.transform = STTransform(translate=(-320, -240, 10))  # Push camera image to back (higher z = further)
        self.image.order = -1  # Draw camera image behind everything else
        self.axis = visuals.XYZAxis(parent=self.view.scene)
        self.axis.order = 3  # Draw axis on top of everything

        # Create Line visuals for track bounding boxes (2D rectangles)
        self.trackBoxes = []
        for i in range(NUMBER_OF_TRACKS):
            line = visuals.Line(parent=self.view.scene, color='white', width=2)
            line.order = 1  # Draw on top of image
            line.visible = False
            self.trackBoxes.append(line)

        # Create Text visual for classification label (only for closest track)
        self.classificationLabel = visuals.Text(
            text='',
            color='white',
            font_size=14,
            bold=True,
            parent=self.view.scene
        )
        self.classificationLabel.order = 2  # Draw on top of boxes
        self.classificationLabel.visible = False

        # Replace placeholder with canvas
        self.layout().removeWidget(self.placeholder_label)
        self.placeholder_label.deleteLater()
        self.layout().addWidget(self.canvas.native)

        self._fully_initialized = True
        log.info("Camera visualization graphics initialized successfully")

    def showEvent(self, event):
        """Qt event fired when tab becomes visible"""
        super().showEvent(event)
        if not self._fully_initialized and CameraHardware.get_instance().isOn():
            # Defer initialization to avoid blocking UI thread
            log.info("Tab shown and camera is on, triggering lazy initialization...")
            QTimer.singleShot(10, self._lazy_init_graphics)

    def getPointCloudInCameraCoords(self, pointCloud):
        if not self._fully_initialized:
            return

        if pointCloud is None or len(pointCloud) == 0:
            self.cameraPointCloud = None
            return

        # Deep copy input
        self.cameraPointCloud = np.array(pointCloud)

        # Transform from radar coordinates to camera-aligned coordinates
        # Radar: X=right, Y=forward(range), Z=up(height)
        # Camera: X=right, Y=down, Z=forward(depth)
        # Mapping: cam_X = radar_X, cam_Y = -radar_Z, cam_Z = radar_Y
        camera_aligned = np.zeros((len(pointCloud), 3))
        camera_aligned[:, 0] = pointCloud[:, 0]   # radar_X → cam_X
        camera_aligned[:, 1] = -pointCloud[:, 2]  # radar_Z → -cam_Y (height, flip for down=positive)
        camera_aligned[:, 2] = pointCloud[:, 1]   # radar_Y → cam_Z (range → depth)

        # Add homogeneous coordinate for matrix operations
        camera_aligned_h = np.hstack([camera_aligned, np.ones((len(camera_aligned), 1))])

        # Apply extrinsic transform (rotation + translation)
        if self.camera_hardware.extrinsic_matrix is not None:
            camera_points = camera_aligned_h @ self.camera_hardware.extrinsic_matrix
        else:
            # Fallback: translation only (offsets remapped to camera coordinates)
            camera_points = camera_aligned.copy()
            camera_points[:, 0] += self.camera_hardware.radar_camera_x_offset  # X offset
            camera_points[:, 1] += self.camera_hardware.radar_camera_z_offset  # Z offset → camera Y
            camera_points[:, 2] += self.camera_hardware.radar_camera_y_offset  # Y offset → camera Z

        # Perspective division (normalize by depth)
        depth = camera_points[:, 2].copy()
        depth[depth < np.finfo(float).eps] = np.finfo(float).eps
        normalized = camera_points[:, :2] / depth[:, np.newaxis]

        # Apply lens distortion
        distorted = self._applyDistortion(normalized)

        # Apply intrinsic projection
        if self.camera_hardware.intrinsic_matrix is not None:
            distorted_h = np.hstack([distorted, np.ones((len(distorted), 1))])
            projected = distorted_h @ self.camera_hardware.intrinsic_matrix
            cam_x = projected[:, 0]
            cam_y = projected[:, 1]
        else:
            # Fallback: use FoV-derived parameters
            fx = self.camera_hardware.XTransformParameter
            fy = self.camera_hardware.YTransformParameter
            cx = self.camera_hardware.xResolution / 2
            cy = self.camera_hardware.yResolution / 2
            cam_x = distorted[:, 0] * fx + cx
            cam_y = distorted[:, 1] * fy + cy

        # Convert to VisPy center-origin coordinates
        half_w = self.camera_hardware.xResolution / 2
        half_h = self.camera_hardware.yResolution / 2
        self.cameraPointCloud[:, 0] = cam_x - half_w
        self.cameraPointCloud[:, 1] = cam_y - half_h
        self.cameraPointCloud[:, 2] = depth  # Store depth for size scaling

        # Clamp to frame boundaries
        self.cameraPointCloud[:, 0] = np.clip(self.cameraPointCloud[:, 0],
                                              self.camera_hardware.frameLeft,
                                              self.camera_hardware.frameRight)
        self.cameraPointCloud[:, 1] = np.clip(self.cameraPointCloud[:, 1],
                                              self.camera_hardware.frameBottom,
                                              self.camera_hardware.frameTop)

    def togglePointsColorByDoppler(self):
        self.togglePointsColorByDopplerBool = not self.togglePointsColorByDopplerBool

    def _applyDistortion(self, points):
        """Apply lens distortion to normalized image points"""
        k = self.camera_hardware.distortion_k
        p = self.camera_hardware.distortion_p
        s = self.camera_hardware.distortion_s

        if all(v == 0 for v in k + p + s):
            return points

        x, y = points[:, 0], points[:, 1]
        r2 = x * x + y * y
        r4, r6 = r2 * r2, r2 * r2 * r2

        # Radial distortion
        radial = (1 + k[0] * r2 + k[1] * r4 + k[2] * r6) / (1 + k[3] * r2 + k[4] * r4 + k[5] * r6)
        # Tangential distortion
        tang_x = 2 * p[0] * x * y + p[1] * (r2 + 2 * x * x)
        tang_y = 2 * p[1] * x * y + p[0] * (r2 + 2 * y * y)
        # Thin prism distortion
        prism_x = s[0] * r2 + s[1] * r4
        prism_y = s[2] * r2 + s[3] * r4

        result = np.zeros_like(points)
        result[:, 0] = x * radial + tang_x + prism_x
        result[:, 1] = y * radial + tang_y + prism_y
        return result

    def convertPCtoCameraCoords(self):
        if not self._fully_initialized:
            return

        if self.cameraPointCloud is None:
            return

        toPlot = self.cameraPointCloud[:, 0:3].copy()
        doppler = self.cameraPointCloud[:, 3]

        if self.togglePointsColorByDopplerBool:
            # Vectorized doppler color: moving-away = red, moving-toward = blue
            abs_d = np.abs(doppler)
            R = np.where(doppler <= 0, 1.0, 0.0)
            B = np.where(doppler > 0, 1.0, 0.0)
            G = np.clip(0.75 - 1.5 * abs_d / (abs_d + 1.0), 0.0, None)
            pointColors = np.column_stack([R, G, B]).astype(np.float32)
        else:
            # Color points as white
            pointColors = np.column_stack([1, 1, 1]).astype(np.float32)

        # Vectorized size scaling (depth stored in z before we overwrite it)
        pointSizes = np.clip(20.0 / toPlot[:, 2], 0.1, 40.0).astype(np.float32)
        toPlot[:, 2] = -1  # Place point cloud in front of camera image (lower z = closer)

        self.scatterVispy.set_data(pos=toPlot, edge_width=0, face_color=pointColors, size=pointSizes, symbol='o')

    def captureVideoFrame(self):
        if not self._fully_initialized:
            return

        # Throttle reads to the camera's frame rate so the radarless timer and
        # the radar update path don't both read + upload a texture in the same frame.
        now = time.monotonic()
        min_interval = 0.9 / max(self.camera_hardware.frameRate, 1)
        if now - self._last_capture_time < min_interval:
            return
        self._last_capture_time = now

        if self.camera_hardware.videoCaptureObj is not None:
            ret, camframe = self.camera_hardware.videoCaptureObj.read()
            if ret:
                if self.camera_hardware.videoRecordingEnabled:
                    self.camera_hardware.videoWriterObj.write(camframe)  # Write original BGR frame
                # Convert BGR→RGB for VisPy display
                self.image.set_data(cvtColor(camframe, COLOR_BGR2RGB))
                # Only rebuild STTransform when resolution actually changes
                res = (self.camera_hardware.xResolution, self.camera_hardware.yResolution)
                if res != self._cached_transform_res:
                    self.image.transform = STTransform(translate=(-res[0] / 2, -res[1] / 2, 10))
                    self._cached_transform_res = res

                # canvas.update() schedules one non-blockingly.
                self.canvas.update()
            else:
                log.error("Video Frame Dropped")

    def startRadarlessCameraCapture(self):
        if not self._fully_initialized:
            return

        if self.cameraTimer is not None:
            self.endRadarlessCameraCapture()
        self.cameraTimer = QTimer()
        self.cameraTimer.setSingleShot(False)
        self.cameraTimer.timeout.connect(self.captureVideoFrame)
        self.cameraTimer.start(100)  # 100 msec frame rate

    def endRadarlessCameraCapture(self):
        if self.cameraTimer is not None:
            self.cameraTimer.stop()
            self.cameraTimer = None

    def findClosestTrack(self, tracks, numTracks):
        """Find the track closest to the sensor origin (0,0,0)"""
        if tracks is None or numTracks == 0:
            return None

        minDistance = float('inf')
        closestTrackId = None

        for i in range(numTracks):
            tid = int(tracks[i, 0])
            x = tracks[i, 1]
            y = tracks[i, 2]
            z = tracks[i, 3]
            distance = math.sqrt(x * x + y * y + z * z)
            if distance < minDistance:
                minDistance = distance
                closestTrackId = tid

        return closestTrackId

    def getTrackCameraCoords(self, x, y, z):
        """Convert 3D track position to 2D camera coordinates using full projection pipeline"""
        # Transform from radar coordinates to camera-aligned coordinates
        # Radar: X=right, Y=forward(range), Z=up(height)
        # Camera: X=right, Y=down, Z=forward(depth)
        camera_aligned = np.array([[x, -z, y]])  # [radar_X, -radar_Z, radar_Y]

        # Add homogeneous coordinate
        camera_aligned_h = np.hstack([camera_aligned, np.ones((1, 1))])

        # Apply extrinsic transform (rotation + translation)
        if self.camera_hardware.extrinsic_matrix is not None:
            camera_point = camera_aligned_h @ self.camera_hardware.extrinsic_matrix
        else:
            # Fallback: translation only (offsets remapped to camera coordinates)
            camera_point = camera_aligned.copy()
            camera_point[:, 0] += self.camera_hardware.radar_camera_x_offset  # X offset
            camera_point[:, 1] += self.camera_hardware.radar_camera_z_offset  # Z offset → camera Y
            camera_point[:, 2] += self.camera_hardware.radar_camera_y_offset  # Y offset → camera Z

        # Perspective division (normalize by depth)
        depth = camera_point[0, 2]
        if depth < np.finfo(float).eps:
            depth = np.finfo(float).eps
        normalized = camera_point[:, :2] / depth

        # Apply lens distortion
        distorted = self._applyDistortion(normalized)

        # Apply intrinsic projection
        if self.camera_hardware.intrinsic_matrix is not None:
            distorted_h = np.hstack([distorted, np.ones((1, 1))])
            projected = distorted_h @ self.camera_hardware.intrinsic_matrix
            cam_x = projected[0, 0]
            cam_y = projected[0, 1]
        else:
            # Fallback: use FoV-derived parameters
            fx = self.camera_hardware.XTransformParameter
            fy = self.camera_hardware.YTransformParameter
            cx = self.camera_hardware.xResolution / 2
            cy = self.camera_hardware.yResolution / 2
            cam_x = distorted[0, 0] * fx + cx
            cam_y = distorted[0, 1] * fy + cy

        # Convert to VisPy center-origin coordinates
        half_w = self.camera_hardware.xResolution / 2
        half_h = self.camera_hardware.yResolution / 2
        camX = cam_x - half_w
        camY = cam_y - half_h

        # Clamp to frame boundaries
        camX = np.clip(camX, self.camera_hardware.frameLeft, self.camera_hardware.frameRight)
        camY = np.clip(camY, self.camera_hardware.frameBottom, self.camera_hardware.frameTop)

        return camX, camY, depth

    def get2DBoxCoords(self, centerX, centerY, range_val):
        """Generate 2D rectangle coordinates for a track bounding box"""
        # Scale box size inversely with range (closer = bigger)
        scale = 4.0 / (range_val + 0.5)
        scale = np.clip(scale, 0.3, 3.0)

        halfWidth = self.trackBoxBaseWidth * scale
        halfHeight = self.trackBoxBaseHeight * scale

        # Create rectangle vertices (closed loop for Line visual)
        # Order: bottom-left -> bottom-right -> top-right -> top-left -> bottom-left
        left = centerX - halfWidth
        right = centerX + halfWidth
        top = centerY - halfHeight  # Note: Y is inverted in camera coords
        bottom = centerY + halfHeight

        # Use z=-2 to draw in front of the image (which is at z=10) and point cloud (z=-1)
        # Lower z values are closer to camera
        vertices = np.array([
            [left, bottom, -2],
            [right, bottom, -2],
            [right, top, -2],
            [left, top, -2],
            [left, bottom, -2]  # Close the loop
        ], dtype=np.float32)

        return vertices, bottom  # Return bottom Y for label placement

    def updateTracks(self, tracks, numTracks, classification=None):
        """Update track visualization in camera view

        Args:
            tracks: Track data array with shape (numTracks, N) where columns are:
                    [tid, x, y, z, ...]
            numTracks: Number of detected tracks
            classification: Classification index for closest track (0=STANDING, 1=SITTING, 2=MOVING)
                           If None, tracks are drawn in white without classification labels
        """
        if not self._fully_initialized:
            return

        # Hide all track boxes first
        for box in self.trackBoxes:
            box.visible = False
        self.classificationLabel.visible = False

        if tracks is None or numTracks == 0:
            return

        # Find closest track (for classification display)
        closestTrackId = self.findClosestTrack(tracks, numTracks)

        # Draw each track using raw coordinates (no filtering)
        # Use loop index i for trackBoxes array access, not track ID
        for i in range(min(numTracks, NUMBER_OF_TRACKS)):
            tid = int(tracks[i, 0])
            x = tracks[i, 1]
            y = tracks[i, 2]
            z = tracks[i, 3]

            # Convert to camera coordinates
            camX, camY, range_val = self.getTrackCameraCoords(x, y, z)

            # Get 2D box coordinates
            boxCoords, bottomY = self.get2DBoxCoords(camX, camY, range_val)

            # Determine color based on whether this is the closest track
            if tid == closestTrackId:
                if classification is not None:
                    color = self.classificationColors.get(classification, self.otherTrackColor)

                    # Update classification label for closest track
                    classText = CLASS_NAMES[classification] if classification < len(CLASS_NAMES) else ''
                    self.classificationLabel.text = classText
                    self.classificationLabel.pos = (camX, bottomY + 15, -3)  # Below the box, in front of everything (lower z = closer)
                    self.classificationLabel.color = color
                    self.classificationLabel.visible = True
                else:
                    color = self.otherTrackColor
            else:
                color = self.otherTrackColor

            # Update track box visual using loop index i, not track ID
            self.trackBoxes[i].set_data(pos=boxCoords, color=color)
            self.trackBoxes[i].visible = True

    def startOverlayRecording(self, output_path):
        """Start recording the camera overlay visualization to video file

        Args:
            output_path: Full path where the overlay video should be saved
        """
        if not self._fully_initialized:
            log.warning("Cannot start overlay recording - graphics not initialized")
            return False

        if self.overlayVideoWriter is not None:
            log.warning("Overlay recording already in progress")
            return False

        try:
            # Get camera hardware to determine frame rate
            camera_hw = CameraHardware.get_instance()
            frame_rate = camera_hw.frameRate if camera_hw.frameRate > 0 else 10

            # Get canvas dimensions
            width = self.canvas.size[0]
            height = self.canvas.size[1]

            # Create video writer
            fourcc = VideoWriter_fourcc(*'mp4v')
            self.overlayVideoWriter = VideoWriter(output_path, fourcc, frame_rate, (width, height))

            if not self.overlayVideoWriter.isOpened():
                log.error(f"Failed to open overlay video writer at {output_path}")
                self.overlayVideoWriter = None
                return False

            self.overlayVideoEnabled = True
            self.overlayVideoPath = output_path
            log.info(f"Started overlay recording to {output_path} at {frame_rate} fps ({width}x{height})")
            return True

        except Exception as e:
            log.error(f"Error starting overlay recording: {e}")
            self.overlayVideoWriter = None
            return False

    def stopOverlayRecording(self):
        """Stop recording the camera overlay visualization"""
        if self.overlayVideoWriter is not None:
            try:
                self.overlayVideoWriter.release()
                log.info(f"Stopped overlay recording to {self.overlayVideoPath}")
            except Exception as e:
                log.error(f"Error stopping overlay recording: {e}")
            finally:
                self.overlayVideoWriter = None
                self.overlayVideoEnabled = False
                self.overlayVideoPath = None

    def captureOverlayFrame(self):
        """Capture the current VisPy canvas frame and write to overlay video"""
        if not self._fully_initialized or not self.overlayVideoEnabled or self.overlayVideoWriter is None:
            return

        try:
            # canvas.render() forces its own render cycle — calling canvas.update() +
            # app.process_events() first causes re-entrant event processing from within
            # the radar update callback and produces stuttering.
            frame = self.canvas.render()
            self.overlayVideoWriter.write(cvtColor(frame, COLOR_RGBA2BGR))
        except Exception as e:
            log.error(f"Error capturing overlay frame: {e}")

    def updateCameraViewWithRadarData(self, cumulativeCloud, outputDict, revertTransformFunc=None):
        """Update camera view with radar point cloud and track data

        This method encapsulates the complete update cycle for camera+radar fusion:
        - Projects radar point cloud onto camera view
        - Captures camera frame
        - Updates track visualization
        - Records overlay frame (if recording enabled)

        Args:
            cumulativeCloud: Point cloud in radar coordinates (may be transformed for 3D display).
                            Can be None if no point cloud is available.
            outputDict: Dictionary containing radar output data with keys:
                       - 'trackData': numpy array of track information (optional)
                       - 'numDetectedTracks': number of detected tracks (optional)
            revertTransformFunc: Optional callable to revert sensor position transforms.
                                The point cloud may have been rotated/shifted for 3D display,
                                but camera overlay needs original coordinates.
                                Should accept point cloud array and return transformed array.

        Example:
            self.cameraRadarFusionTab.updateCameraViewWithRadarData(
                self.cumulativeCloud,
                outputDict,
                self.revertSensorPositionTransforms
            )
        """
        camera_hw = CameraHardware.get_instance()

        # Early exit if not ready
        if not self._fully_initialized or not camera_hw.isOn():
            return

        # Process and display point cloud if available
        if cumulativeCloud is not None:
            # Revert any display transformations to get original radar coordinates
            cameraPointCloud = revertTransformFunc(cumulativeCloud) if revertTransformFunc else cumulativeCloud
            self.getPointCloudInCameraCoords(cameraPointCloud)
            self.convertPCtoCameraCoords()

        # Capture current camera frame
        self.captureVideoFrame()

        # Update track visualization
        if 'trackData' in outputDict:
            tracksLocalCoords = np.array(outputDict['trackData'])
            numTracks = outputDict.get('numDetectedTracks', 0)
            self.updateTracks(tracksLocalCoords, numTracks, None)
        else:
            self.updateTracks(None, 0, None)

        # Capture overlay frame for recording (if enabled)
        self.captureOverlayFrame()
