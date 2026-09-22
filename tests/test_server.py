"""HTTP 服务端到端测试：启动真实 socket 服务器，用 http.client 调用。"""

from __future__ import annotations

import http.client
import json
import threading
import unittest

from app.server import create_server


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.port = _free_port()
        self.server = create_server("127.0.0.1", self.port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method: str, path: str, body: bytes | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_healthz(self) -> None:
        status, data = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["status"], "ok")

    def test_stitch_success(self) -> None:
        payload = json.dumps(
            {
                "endpoints": [
                    {"id": "a", "left_grain": "g1", "right_grain": "g2"},
                    {"id": "b", "left_grain": "g2", "right_grain": "g1"},
                    {"id": "c", "left_grain": "g1", "right_grain": "g2"},
                    {"id": "d", "left_grain": "g2", "right_grain": "g1"},
                ],
                "candidates": [
                    {"a": "a", "b": "b", "cost": 1},
                    {"a": "c", "b": "d", "cost": 2},
                    {"a": "a", "b": "d", "cost": 4},
                    {"a": "b", "b": "c", "cost": 5},
                ],
            }
        ).encode()
        status, data = self.request("POST", "/api/v1/stitch", payload)
        self.assertEqual(status, 200, data)
        result = json.loads(data)
        self.assertTrue(result["feasible"])
        self.assertEqual(result["total_cost"], 3)
        self.assertEqual(
            [(c["a"], c["b"]) for c in result["stitching"]],
            [("a", "b"), ("c", "d")],
        )
        self.assertEqual(
            {(e["a"], e["b"]) for e in result["ambiguity"]["in_all"]},
            {("a", "b"), ("c", "d")},
        )

    def test_no_feasible_stitching(self) -> None:
        # 两个断端但不给出候选 -> 无完美匹配。
        payload = json.dumps(
            {
                "endpoints": [
                    {"id": "a", "left_grain": "g1", "right_grain": "g2"},
                    {"id": "b", "left_grain": "g2", "right_grain": "g1"},
                ],
                "candidates": [],
            }
        ).encode()
        status, data = self.request("POST", "/api/v1/stitch", payload)
        self.assertEqual(status, 422)
        result = json.loads(data)
        self.assertFalse(result["feasible"])
        self.assertEqual(result["error"]["code"], "no_feasible_stitching")

    def test_validation_error_is_locatable(self) -> None:
        payload = json.dumps(
            {
                "endpoints": [
                    {"id": "a", "left_grain": "g1", "right_grain": "g2"},
                    {"id": "b", "left_grain": "g1", "right_grain": "g2"},
                ],
                "candidates": [{"a": "a", "b": "b", "cost": 0}],
            }
        ).encode()
        status, data = self.request("POST", "/api/v1/stitch", payload)
        self.assertEqual(status, 400)
        result = json.loads(data)
        self.assertEqual(result["error"]["code"], "validation_failed")
        self.assertEqual(result["error"]["details"][0]["location"], "/candidates/0")
        self.assertEqual(
            result["error"]["details"][0]["code"], "candidate_side_mismatch"
        )

    def test_invalid_json(self) -> None:
        status, data = self.request("POST", "/api/v1/stitch", b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(data)["error"]["code"], "invalid_json")

    def test_not_found(self) -> None:
        status, _ = self.request("GET", "/nope")
        self.assertEqual(status, 404)

    def test_repeated_submission_byte_identical(self) -> None:
        payload = json.dumps(
            {
                "endpoints": [
                    {"id": "a", "left_grain": "g1", "right_grain": "g2"},
                    {"id": "b", "left_grain": "g2", "right_grain": "g1"},
                    {"id": "c", "left_grain": "g1", "right_grain": "g2"},
                    {"id": "d", "left_grain": "g2", "right_grain": "g1"},
                ],
                "candidates": [
                    {"a": "d", "b": "a", "cost": 4},  # 故意打乱提交顺序/方向
                    {"a": "b", "b": "c", "cost": 5},
                    {"a": "a", "b": "b", "cost": 1},
                    {"a": "c", "b": "d", "cost": 2},
                ],
            }
        ).encode()
        bodies = []
        for _ in range(5):
            status, data = self.request("POST", "/api/v1/stitch", payload)
            self.assertEqual(status, 200)
            bodies.append(data)
        self.assertEqual(len(set(bodies)), 1)  # 字节等价

        # 候选以不同顺序提交：规范结果仍应一致。
        reordered = json.dumps(
            {
                "endpoints": [
                    {"id": "a", "left_grain": "g1", "right_grain": "g2"},
                    {"id": "b", "left_grain": "g2", "right_grain": "g1"},
                    {"id": "c", "left_grain": "g1", "right_grain": "g2"},
                    {"id": "d", "left_grain": "g2", "right_grain": "g1"},
                ],
                "candidates": [
                    {"a": "a", "b": "b", "cost": 1},
                    {"a": "c", "b": "d", "cost": 2},
                    {"a": "b", "b": "c", "cost": 5},
                    {"a": "d", "b": "a", "cost": 4},
                ],
            }
        ).encode()
        status, data2 = self.request("POST", "/api/v1/stitch", reordered)
        self.assertEqual(status, 200)
        self.assertEqual(data2, bodies[0])


if __name__ == "__main__":
    unittest.main()
