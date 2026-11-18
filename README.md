# Camera for Valetudo – Home Assistant integration

Shows the live camera image of a [Valetudo](https://valetudo.cloud) robot vacuum
as a camera entity in Home Assistant. Everything stays in your local network.

Valetudo 2026.08.0 added a native camera stream for robots with a front camera.
Home Assistant cannot play that stream directly, because it is MPEG-1 video in
an MPEG-TS container. This integration reads the stream and converts it to
MJPEG with the ffmpeg that Home Assistant already ships.

This is an independent project. It is not affiliated with Valetudo or
Home Assistant.

## Requirements

- Valetudo 2026.08.0 or newer on a robot that offers the camera stream
- The camera stream enabled in the Valetudo web interface
- Home Assistant 2026.10 or newer
- The Marketplace of Home Assistant 2026.11 and newer, or
  [HACS](https://www.hacs.xyz) on older versions, for the installation
  described below

Developed and tested with Valetudo 2026.08.0 and Home Assistant 2026.10.4.

## Installation

[![Open your Home Assistant instance and open this repository.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=vordenken&repository=ha-valetudo-camera&category=integration)

The button opens this repository in your own Home Assistant, in the Marketplace
or in HACS. Download **Camera for Valetudo** there and restart Home Assistant if it
asks you to.

To add it by hand instead, add
`https://github.com/vordenken/ha-valetudo-camera` as a custom repository of the
type **Integration** in the Marketplace or in HACS, then download it.

Without either of them, copy the folder `custom_components/valetudo_camera`
into the `custom_components` folder of your Home Assistant configuration and
restart.

## Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=valetudo_camera)

Robots in the same network are discovered automatically and appear under
**Settings → Devices & services**. You can also add a robot by hand with the
button above or with **Add integration → Camera for Valetudo**.

| Field | Meaning |
| --- | --- |
| Base URL | Address of the Valetudo web interface, for example `http://192.168.1.50`. An HTTPS address works as well. |
| Username, Password | Only needed if you enabled authentication in Valetudo. |

If the certificate of an HTTPS address cannot be verified, for example because
it is self-signed, the integration asks before it connects without verification.

If Valetudo is also connected to Home Assistant through MQTT, the camera is
added to the existing device of the robot. Otherwise the robot gets a device of
its own.

## Options

Open the options of the integration entry to change these values.

| Option | Default | Meaning |
| --- | --- | --- |
| Snapshot cache | 30 seconds | How long a still image is reused before a new one is fetched. `0` fetches a new image every time. |
| MJPEG frame rate | 10 | Frames per second of the live view, from 1 to 20. |
| MJPEG quality | 5 | From 2 (best) to 31 (smallest). |

## Resource usage

Nothing runs while nobody is watching. Every open live view starts one ffmpeg
process on the Home Assistant host. The numbers below were measured with three
minutes of a real 640×480 stream on an Apple M4. Slower hardware needs a larger
share of a core.

| Setting | CPU (one core) | Sent to the browser |
| --- | --- | --- |
| 10 fps, quality 5 (default) | 1.7 % | 1.4 Mbit/s |
| 5 fps, quality 5 | 0.9 % | 0.7 Mbit/s |
| 10 fps, quality 10 | 1.4 % | 0.8 Mbit/s |

Each live view uses 40 to 50 MB of memory and pulls about 1.1 Mbit/s from the
robot.

A dashboard card that shows a still image asks for a new image regularly. Each
image that is not served from the cache opens the stream of the robot for a few
seconds. Raise the snapshot cache if you want the robot to be asked less often.

## Limitations

- Valetudo serves at most four viewers per robot at the same time, including
  its own web interface. A fifth viewer pushes out the oldest one.
- A new still image takes about five seconds.
- If the robot ends the stream, the live view stops. Close and reopen it to
  continue.
- The live view is MJPEG. It has no sound and cannot be recorded with the
  stream features of Home Assistant.

## Security and privacy

- The integration only reads from Valetudo. It never changes settings on the
  robot.
- The camera is as protected as your Valetudo web interface. Keep the robot in
  your local network or behind a VPN and enable authentication in Valetudo.
- Valetudo speaks plain HTTP, so the stream and the credentials travel
  unencrypted inside your network.
- The credentials are stored in the Home Assistant configuration, like those of
  other integrations. They are not part of the diagnostics download.

## Troubleshooting

| Message during setup | What to check |
| --- | --- |
| Could not connect to Valetudo | The address, and whether the robot is switched on. |
| The username or password was rejected | The credentials set in Valetudo. |
| This robot does not support Valetudo duststreaming | The Valetudo version, and whether the robot has a camera that Valetudo supports. |
| Enable duststreaming in the Valetudo interface first | Switch on the camera stream in Valetudo. |

For more detail, enable debug logging for the integration under
**Settings → Devices & services → Camera for Valetudo** and look at the
Home Assistant log.

## Development

The tests need Python 3.14, [uv](https://docs.astral.sh/uv/) and ffmpeg.

```bash
uv venv --python 3.14
uv pip install -r requirements_test.txt
.venv/bin/ruff check custom_components tests
.venv/bin/python -m pytest
```

## License

[GPL-3.0](LICENSE)
