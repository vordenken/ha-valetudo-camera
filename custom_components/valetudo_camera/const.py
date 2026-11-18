"""Constants for the Valetudo Camera integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "valetudo_camera"

CONF_URL: Final = "url"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_SNAPSHOT_CACHE: Final = "snapshot_cache_seconds"
CONF_MJPEG_FPS: Final = "mjpeg_fps"
CONF_MJPEG_QUALITY: Final = "mjpeg_quality"

DEFAULT_SNAPSHOT_CACHE: Final = 30
DEFAULT_MJPEG_FPS: Final = 10
DEFAULT_MJPEG_QUALITY: Final = 5

UPDATE_INTERVAL: Final = timedelta(minutes=1)

# The duststreamer needs a few seconds to spin up when nobody is subscribed.
STREAM_START_TIMEOUT: Final = 30
SNAPSHOT_TIMEOUT: Final = 30

MJPEG_CONTENT_TYPE: Final = "multipart/x-mixed-replace;boundary=ffmpeg"
