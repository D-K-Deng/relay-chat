"""Exercise automatic waits without sleeping through a real rate-limit window."""
import unittest
from unittest.mock import Mock, patch

from relay.rate_limits import Recovery


class RateWait(unittest.TestCase):
    def setUp(self):
        self.stop = Mock()
        self.stop.is_set.return_value = False
        self.stop.wait.return_value = False
        self.emit = Mock()
        self.recovery = Recovery()

    def wait(self, seconds):
        with patch("relay.rate_limits.random.uniform", return_value=.1):
            return self.recovery.wait({"rate_limit": {"retry_after": seconds}}, self.stop, self.emit, partial=True)

    def test_retries_past_three_and_honors_server_minimum(self):
        self.assertTrue(self.wait(.169))
        self.assertTrue(self.wait(None))
        self.assertTrue(self.wait(6))
        self.assertTrue(self.wait(.523))
        self.assertEqual([call.args[0] for call in self.stop.wait.call_args_list], [1.1, 2.1, 6.1, 8.1])
        self.assertEqual(self.emit.call_args_list[0].kwargs, {"seconds": 1.1, "waited": 0, "attempt": 1, "partial": True})

    def test_long_server_cooldown_keeps_connection_alive_and_can_be_stopped(self):
        self.assertTrue(self.wait(120))
        self.assertAlmostEqual(sum(call.args[0] for call in self.stop.wait.call_args_list), 120.1)
        self.assertIn("ping", [call.args[0] for call in self.emit.call_args_list])
        self.stop.wait.return_value = True
        self.emit.reset_mock()
        self.assertFalse(self.wait(3))
        self.assertEqual([call.args[0] for call in self.emit.call_args_list], ["rate_wait"])

    def test_disabled_wait_returns_the_failure_immediately(self):
        self.recovery = Recovery(enabled=False)
        self.assertFalse(self.wait(.169))
        self.emit.assert_not_called()
        self.stop.wait.assert_not_called()
