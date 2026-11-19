"""Test-only helpers for suites that exercise X send mechanics.

G-14 makes the platform-permission gate a real execution boundary: the CDP
adapter can never pass it, by design. Tests that verify *other* safety layers
(idempotency, reserve/claim, readback, identity) satisfy the gate explicitly
here instead of weakening it in production code. The gate itself is covered by
``tests/test_platform_compliance.py``.
"""
from unittest import mock

from core import platform_compliance


def bypass_x_compliance(case):
    """Patch both permission routes while testing independent safety layers.

    The production adapter still chooses autonomous versus user-visible CDP.
    Suites for identity, idempotency and readback explicitly isolate that choice;
    both real gates remain covered by ``test_platform_compliance.py``.
    """
    autonomous = mock.patch.object(
        platform_compliance, "require_autonomous_write",
        return_value={"approved_by": "test-fixture",
                      "route": "official_api", "transport": "api"})
    autonomous.start()
    case.addCleanup(autonomous.stop)
    visible = mock.patch.object(
        platform_compliance, "require_user_visible_cdp",
        return_value={"route": "user_visible_cdp", "transport": "cdp",
                      "authorization": "test-fixture-isolates-other-gates"})
    visible.start()
    case.addCleanup(visible.stop)
    return autonomous
