"""Regression tests for quota deletion and peer recovery."""

import ast
import asyncio
import copy
from pathlib import Path
import unittest


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class FakeDB:
    def __init__(self, peer, events):
        self.peer = peer
        self.events = events

    async def get_peer(self, public_key):
        self.events.append("get_peer")
        return self.peer

    async def set_disabled(self, public_key, disabled):
        self.events.append("set_disabled")
        self.peer["disabled"] = int(disabled)

    async def delete_quota(self, public_key):
        self.events.append("delete_quota")


class FakeAWG:
    def __init__(self, events, fail=False):
        self.events = events
        self.fail = fail

    async def enable_peer(self, public_key, allowed_ips):
        self.events.append("enable_peer")
        if self.fail:
            raise RuntimeError("Simulated AWG failure")


async def fake_json_body(request):
    return request


def fake_required_key(body):
    return body["public_key"]


def load_quota_delete_handler():
    """Extract the actual API handler without starting the FastAPI app."""
    source = ast.parse(Path("app/main.py").read_text())

    matches = [
        node
        for node in ast.walk(source)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "api_quotas_delete"
    ]
    if len(matches) != 1:
        raise AssertionError("Expected exactly one quota delete handler")

    handler = copy.deepcopy(matches[0])
    handler.decorator_list = []
    handler.returns = None
    for arg in handler.args.args:
        arg.annotation = None

    module = ast.fix_missing_locations(
        ast.Module(body=[handler], type_ignores=[])
    )

    namespace = {
        "collector": type("FakeCollector", (), {
            "_lock": asyncio.Lock(),
        })(),
        "HTTPException": HTTPException,
        "_json_body": fake_json_body,
        "_required_key": fake_required_key,
    }
    exec(compile(module, "<quota-handler-test>", "exec"), namespace)
    return namespace["api_quotas_delete"]


class QuotaDeleteTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.handler = staticmethod(load_quota_delete_handler())

    def setUp(self):
        self.events = []
        self.peer = {
            "disabled": 1,
            "disabled_by": "quota",
            "allowed_ips": "10.99.0.2/32",
        }

    async def invoke(self, peer=None, *, fail=False):
        self.handler.__globals__["db"] = FakeDB(peer, self.events)
        self.handler.__globals__["awg"] = FakeAWG(
            self.events, fail=fail
        )
        return await self.handler(
            {"public_key": "test-public-key"},
            None,
        )

    async def test_quota_disabled_peer_restored(self):
        await self.invoke(self.peer)
        self.assertEqual(self.events, [
            "get_peer",
            "enable_peer",
            "set_disabled",
            "delete_quota",
        ])
        self.assertEqual(self.peer["disabled"], 0)

    async def test_manual_disable_preserved(self):
        self.peer["disabled_by"] = "manual"
        await self.invoke(self.peer)
        self.assertEqual(
            self.events, ["get_peer", "delete_quota"]
        )
        self.assertEqual(self.peer["disabled"], 1)

    async def test_active_peer_not_reconfigured(self):
        self.peer["disabled"] = 0
        await self.invoke(self.peer)
        self.assertEqual(
            self.events, ["get_peer", "delete_quota"]
        )

    async def test_restore_failure_preserves_quota(self):
        with self.assertRaises(RuntimeError):
            await self.invoke(self.peer, fail=True)
        self.assertEqual(
            self.events, ["get_peer", "enable_peer"]
        )

    async def test_unknown_peer_does_not_trigger_enable(self):
        await self.invoke(None)
        self.assertEqual(
            self.events, ["get_peer", "delete_quota"]
        )

    async def test_missing_allowed_ips_preserves_quota(self):
        self.peer["allowed_ips"] = ""
        with self.assertRaises(HTTPException) as cm:
            await self.invoke(self.peer)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(self.events, ["get_peer"])

    async def test_none_allowed_ips_preserves_quota(self):
        self.peer["allowed_ips"] = None
        with self.assertRaises(HTTPException) as cm:
            await self.invoke(self.peer)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(self.events, ["get_peer"])


    async def test_delete_waits_for_collector_lock(self):
        """Quota deletion must not race with background enforcement."""
        lock = self.handler.__globals__["collector"]._lock
        self.assertFalse(lock.locked())

        await lock.acquire()
        task = None
        try:
            task = asyncio.create_task(self.invoke(self.peer))

            # Give the handler an opportunity to run.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            self.assertFalse(task.done())
            self.assertEqual(
                self.events, [],
                "Quota handler performed operations while collector lock was held",
            )
        finally:
            lock.release()

        await asyncio.wait_for(task, timeout=2)

        self.assertEqual(self.events, [
            "get_peer",
            "enable_peer",
            "set_disabled",
            "delete_quota",
        ])


if __name__ == "__main__":
    unittest.main()
