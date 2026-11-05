"""Regression tests for restoring AmneziaWG peers."""

import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.awg import AwgClient


PUBLIC_KEY = base64.b64encode(b"P" * 32).decode()
PSK = base64.b64encode(b"S" * 32).decode()
IPS = "10.99.0.2/32"


class PeerRestoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AwgClient(SimpleNamespace(
            awg_mode="docker_exec",
            awg_container="test-awg",
            awg_conf_path="/test/awg0.conf",
            awg_interface="awg0",
        ))
        self.client._read_file = AsyncMock()
        self.runner = patch("app.awg._run", new_callable=AsyncMock)
        self.run_command = self.runner.start()
        self.addCleanup(self.runner.stop)

    def config(self, *, psk=PSK, ips=IPS, duplicate=False):
        section = (
            "[Peer]\n"
            f"PublicKey = {PUBLIC_KEY}\n"
            f"AllowedIPs = {ips}\n"
        )
        if psk is not None:
            section += f"PresharedKey = {psk}\n"
        return "[Interface]\nAddress = 10.99.0.1/24\n" + (
            section * (2 if duplicate else 1)
        )

    async def test_restore_with_psk(self):
        self.client._read_file.return_value = self.config()

        await self.client.enable_peer(PUBLIC_KEY, IPS)

        self.run_command.assert_awaited_once()
        args, kwargs = self.run_command.await_args
        command = args[0]

        self.assertEqual(command[:4], [
            "docker", "exec", "-i", "test-awg"
        ])
        self.assertEqual(command[4:9], [
            "awg", "set", "awg0", "peer", PUBLIC_KEY
        ])
        self.assertIn("preshared-key", command)
        self.assertIn("/dev/stdin", command)
        self.assertNotIn(PSK, command)
        self.assertEqual(kwargs["input_data"], (PSK + "\n").encode())

    async def test_restore_without_psk(self):
        self.client._read_file.return_value = self.config(psk=None)

        await self.client.enable_peer(PUBLIC_KEY, IPS)

        args, kwargs = self.run_command.await_args
        self.assertNotIn("preshared-key", args[0])
        self.assertIsNone(kwargs["input_data"])

    async def test_reject_wrong_allowed_ips(self):
        self.client._read_file.return_value = self.config()

        with self.assertRaisesRegex(RuntimeError, "AllowedIPs mismatch"):
            await self.client.enable_peer(PUBLIC_KEY, "10.99.0.3/32")

        self.run_command.assert_not_awaited()

    async def test_reject_duplicate_peer(self):
        self.client._read_file.return_value = self.config(duplicate=True)

        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            await self.client.enable_peer(PUBLIC_KEY, IPS)

        self.run_command.assert_not_awaited()

    async def test_reject_invalid_psk(self):
        self.client._read_file.return_value = self.config(psk="invalid!")

        with self.assertRaisesRegex(RuntimeError, "Invalid peer PSK"):
            await self.client.enable_peer(PUBLIC_KEY, IPS)

        self.run_command.assert_not_awaited()

    async def test_reject_missing_config(self):
        self.client._read_file.return_value = ""

        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            await self.client.enable_peer(PUBLIC_KEY, IPS)

        self.run_command.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
