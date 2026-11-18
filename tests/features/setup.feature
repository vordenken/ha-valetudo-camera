Feature: Keep a configured Valetudo robot connected
  Scenario: The robot rejects the stored credentials
    Given a robot is configured
    When Valetudo answers that the credentials are wrong
    Then Home Assistant asks the user for new credentials

  Scenario: The robot is unreachable at startup
    Given a robot is configured
    When the robot does not answer while Home Assistant starts
    Then the setup is retried later instead of failing for good

  Scenario: The robot goes offline
    Given the camera is available
    When the robot stops answering
    Then the camera becomes unavailable and no new credentials are requested

  Scenario: Diagnostics are downloaded
    When the user downloads the diagnostics of a robot
    Then they describe the robot and contain neither its address nor its credentials
