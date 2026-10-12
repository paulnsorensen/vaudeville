Feature: Live hook enforcement
  A running daemon applies current rules to a real hook and records each evaluation.

  Scenario: A blocked tool call persists a violation visible in statistics
    Given a live rule at tier "block"
    When the assistant calls a restricted tool
    Then the harness denies the tool call
    And the violation is recorded in statistics

  Scenario: Tier changes take effect without restarting the daemon
    Given a live rule at tier "warn"
    When the assistant calls a restricted tool
    Then the harness warns without denying the tool call
    When I promote the live rule
    And the assistant calls a restricted tool
    Then the harness denies the tool call
    When I disable the live rule
    And the assistant calls a restricted tool
    Then the harness allows the tool call without another evaluation
    And the same daemon served every tool call
