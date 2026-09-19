"""
Smoke test for hr_mcp_server tools directly.
"""

from hr_mcp_server import (
    list_available_policies,
    get_policy_document,
    search_policies,
    send_policy_email,
)


def run_tests():
    print("=== 1. Testing list_available_policies ===")
    policies = list_available_policies()
    print("Found policies:", policies)
    assert "leave_policy" in policies
    assert "remote_work_policy" in policies
    assert "health_benefits" in policies
    print("PASSED: list_available_policies\n")

    print("=== 2. Testing search_policies ===")
    result = search_policies("parental leave primary caregiver")
    print("Search Result Snippet:\n", result[:250], "...\n")
    assert "16 weeks" in result
    print("PASSED: search_policies\n")

    print("=== 3. Testing get_policy_document ===")
    doc = get_policy_document("remote_work_policy")
    print("Document length:", len(doc), "chars")
    assert "Core Collaboration Hours" in doc
    print("PASSED: get_policy_document\n")

    print("=== 4. Testing send_policy_email ===")
    email_res = send_policy_email(
        recipient_email="test.employee@acmecorp.internal",
        subject="Your Parental Leave Policy Summary",
        body="Here is the summary of your 16-week parental leave entitlement.",
    )
    print("Email dispatch response:", email_res)
    assert "successfully" in email_res.lower()
    print("PASSED: send_policy_email\n")

    print("ALL TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    run_tests()

