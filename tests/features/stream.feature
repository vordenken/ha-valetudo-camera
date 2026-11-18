Feature: Transcode the Valetudo video stream
  The integration converts MPEG-TS to JPEG for snapshots and live viewers.

  Scenario: Capture a snapshot from a stream that remains open
    Given the robot sends MPEG-TS frames and keeps its connection open
    When the integration captures a snapshot
    Then it returns a JPEG without waiting for the robot to close the connection

  Scenario: Serve a live MJPEG stream
    Given the robot sends MPEG-TS frames
    When a viewer requests the live stream
    Then the response contains multipart JPEG frames

  Scenario: A snapshot times out before a frame arrives
    Given the robot keeps a stream open without sending frames
    When the snapshot timeout expires
    Then the integration reports the timeout and releases the stream

  Scenario: The robot ends a live stream
    Given a viewer is watching the live stream
    When the robot closes the stream connection
    Then the viewer's stream ends without an error

  Scenario: The robot ends the stream before a snapshot frame arrives
    When the robot closes the stream connection before sending a frame
    Then the integration reports that the robot closed the stream

  Scenario: A live stream times out before a frame arrives
    Given the robot keeps a stream open without sending frames
    When the live stream startup timeout expires
    Then the integration reports the timeout and releases the stream
