Feature: Register a Valetudo camera in Home Assistant
  Scenario: An available robot exposes a camera entity
    Given a configured robot with duststreaming enabled and installed
    When Home Assistant sets up the integration
    Then the camera entity is available with an authenticated image URL

  Scenario: The robot cannot stream
    Given duststreaming is disabled in Valetudo or the streamer is not installed
    When Home Assistant sets up the integration
    Then the camera entity is unavailable

  Scenario: Still images are cached
    Given a still image was fetched less than the cache time ago
    When another still image is requested
    Then the cached image is returned without opening the robot's stream

  Scenario: The robot is already known through MQTT
    Given Valetudo's MQTT discovery created a device for the robot
    When Home Assistant sets up the integration
    Then the camera is added to that device and the device is left unchanged

  Scenario: The robot is not known through MQTT
    When Home Assistant sets up the integration
    Then the camera gets a device of its own named after the robot model
