Feature: Set up a Valetudo robot in Home Assistant
  Scenario: A robot that is already set up is discovered again
    Given a robot is already configured
    When the robot announces itself on the network
    Then no additional setup dialog is offered

  Scenario: An unconfigured robot is discovered twice
    Given a discovered robot is waiting for setup
    When the same robot announces itself again
    Then only one setup dialog remains

  Scenario: A discovered robot is set up manually with another URL
    Given a discovered robot is waiting for setup
    When the user adds the same robot manually through a proxy URL
    Then the robot is set up and the discovery dialog disappears

  Scenario: A discovered robot is shown with a readable name
    When a robot announces itself on the network
    Then the discovery card shows its name and model instead of the raw network record

  Scenario: An untrusted certificate needs confirmation
    Given the certificate of the entered HTTPS URL cannot be verified
    When the user submits the connection form
    Then the user is asked to confirm before the certificate check is skipped

  Scenario: A trusted URL turns the certificate check back on
    Given a robot is configured without certificate verification
    When the user reconfigures it with a URL that has a trusted certificate
    Then the certificate is verified again

  Scenario: A setup problem is explained
    Given the robot rejects the credentials, is unreachable, lacks the camera stream or has it disabled
    When the user submits the connection form
    Then the form names the problem and can be submitted again

  Scenario: Rejected credentials are replaced
    Given the robot no longer accepts the stored credentials
    When the user enters new credentials
    Then they are stored and the robot is connected again

  Scenario: Reconfiguring keeps the stored password
    Given a robot is configured with a password
    When the user changes the URL and leaves the password field empty
    Then the stored password is kept and was never shown in the form

  Scenario: Camera options are changed
    When the user changes the snapshot cache, frame rate or quality
    Then the new values are stored for the robot

  Scenario: The password is hidden while typing
    When the user opens a form that asks for credentials
    Then the password field is a masked input
