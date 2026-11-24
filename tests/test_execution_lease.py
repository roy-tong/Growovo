import tempfile
import time
import unittest
from unittest import mock

from core import action_gateway, action_runner, growth_runtime


class ExecutionLeaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name

    def state(self):
        return growth_runtime.execution_lane_state(self.data, "acct", "email")

    def test_fence_increments_across_release(self):
        self.assertTrue(growth_runtime.try_acquire_execution_lane(
            self.data, "acct", "email", "A", lease_seconds=30))
        self.assertEqual(1, self.state()["fence"])
        growth_runtime.release_execution_lane(self.data, "acct", "email", "A")
        self.assertTrue(growth_runtime.try_acquire_execution_lane(
            self.data, "acct", "email", "B", lease_seconds=30))
        self.assertEqual("B", self.state()["owner"])
        self.assertEqual(2, self.state()["fence"])

    def test_renewal_keeps_the_same_fence(self):
        growth_runtime.try_acquire_execution_lane(self.data, "acct", "email", "A", lease_seconds=30)
        growth_runtime.try_acquire_execution_lane(self.data, "acct", "email", "A", lease_seconds=30)
        self.assertEqual(1, self.state()["fence"])

    def test_takeover_after_expiry_gets_a_new_fence(self):
        growth_runtime.try_acquire_execution_lane(self.data, "acct", "email", "A", lease_seconds=1)
        time.sleep(1.1)
        self.assertTrue(growth_runtime.try_acquire_execution_lane(
            self.data, "acct", "email", "B", lease_seconds=30))
        self.assertFalse(growth_runtime.try_acquire_execution_lane(
            self.data, "acct", "email", "A", lease_seconds=30))
        self.assertEqual("B", self.state()["owner"])
        self.assertEqual(2, self.state()["fence"])

    def test_ensure_raises_after_takeover(self):
        growth_runtime.try_acquire_execution_lane(self.data, "acct", "email", "A", lease_seconds=1)
        lane = self.state()
        guard = action_gateway.LeaseState(self.data, "acct", "email", "A", lane["fence"])
        guard.ensure()
        time.sleep(1.1)
        growth_runtime.try_acquire_execution_lane(self.data, "acct", "email", "B", lease_seconds=30)
        with self.assertRaises(action_gateway.LeaseLost):
            guard.ensure()
        self.assertTrue(guard.lost)

    def test_heartbeat_marks_state_lost_when_renewal_refused(self):
        guard = action_gateway.LeaseState(self.data, "acct", "email", "A", 1)
        original = action_gateway.LEASE_HEARTBEAT_SECONDS
        action_gateway.LEASE_HEARTBEAT_SECONDS = 0.05
        try:
            with mock.patch.object(growth_runtime, "try_acquire_execution_lane",
                                   return_value=False):
                stop, thread = action_gateway._start_heartbeat(
                    self.data, "acct", "email", "A", state=guard)
                thread.join(timeout=2)
        finally:
            action_gateway.LEASE_HEARTBEAT_SECONDS = original
        self.assertFalse(thread.is_alive())
        self.assertTrue(guard.lost)
        self.assertEqual("lease_taken", guard.reason)

    def test_execute_returns_lease_lost_before_dispatch(self):
        # 获取成功但不落行 → 执行者手里的 fence 与库中状态不一致，等价于已被接管。
        with mock.patch.object(growth_runtime, "try_acquire_execution_lane",
                               return_value=True), \
                mock.patch.object(action_runner, "_dispatch_action") as dispatch:
            result = action_gateway.execute(
                data_dir=self.data, account="acct", channel="email",
                action="message", go=True, text="hello")
        self.assertEqual("lease_lost", result["result"])
        self.assertFalse(result["retryable"])
        dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
