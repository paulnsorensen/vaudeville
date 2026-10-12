Feature: Persistent rule administration
  Rule changes persist between invocations.
  User rules retain priority over project rules.

  Scenario: Promote the user rule without changing a same-named project rule
    Given a user rule named "review" at tier "log"
    And a project rule named "review" at tier "shadow"
    When I promote the rule named "review"
    Then the rule named "review" has tier "warn" in a later invocation
    And the project rule remains unchanged

  Scenario: Restore the prior tier after disabling a rule
    Given a user rule named "review" at tier "warn"
    When I disable the rule named "review"
    Then the rule named "review" has tier "disabled" in a later invocation
    When I enable the rule named "review"
    Then the rule named "review" has tier "warn" in a later invocation

  Scenario: Reject deletion without confirmation and preserve all rules
    Given a user rule named "review" at tier "warn"
    And a project rule named "review" at tier "shadow"
    And the current rule state is recorded
    When I request deletion of the rule named "review" without confirmation
    Then deletion fails with a confirmation error
    And the current rule state is unchanged
    And the project rule remains unchanged
