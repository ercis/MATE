##### GENERAL #####
WINDOWS_SYSTEM = False

# Encoding method used during preprocessing: "winsim" or "vdd"
ENCODING_TYPE = "winsim"

##### DATA CONFIG #####
N_WINDOWS = 200           # Number of sliding windows for WINSIM encoding
DRIFT_TYPES = ["sudden", "gradual", "incremental", "recurring"]
DISTANCE_MEASURE = "cos"  # Options: "fro", "nuc", "inf", "l2", "cos", "earth"
COLOR = "color"

# Bounding-box resize settings for sudden drifts
RESIZE_SUDDEN_BBOX = True
RESIZE_VALUE = 5

##### VDD CONFIG #####
# Only required when ENCODING_TYPE = "vdd"
SUB_L = 100
SLI_BY = 50
CP_ALL = True
MINERFUL_SCRIPTS_DIR = ""  # Path to MINERful distribution directory

##### IMAGE / MODEL CONFIG #####
IMAGE_SIZE = (256, 256)
TARGETSIZE = 256
N_CLASSES = len(DRIFT_TYPES)
SCALE_MAX = 2.0
SCALE_MIN = 0.1
WIDTH, HEIGHT = IMAGE_SIZE
