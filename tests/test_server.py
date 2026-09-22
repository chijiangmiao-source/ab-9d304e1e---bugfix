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

    def test_five_optimal_solutions_forced_edge_classification(self) -> None:
        # 多重同优实例：5 个最低代价非交叉缝合；e0-e7 跨过首尾且是 e0 的
        # 唯一候选，必须在全部最优解中，其余 9 条候选只在部分最优解中。
        payload_obj = {
            "endpoints": [
                {"id": "e0", "left_grain": "G1", "right_grain": "G2"},
                {"id": "e1", "left_grain": "G2", "right_grain": "G1"},
                {"id": "e2", "left_grain": "G1", "right_grain": "G2"},
                {"id": "e3", "left_grain": "G2", "right_grain": "G1"},
                {"id": "e4", "left_grain": "G1", "right_grain": "G2"},
                {"id": "e5", "left_grain": "G2", "right_grain": "G1"},
                {"id": "e6", "left_grain": "G1", "right_grain": "G2"},
                {"id": "e7", "left_grain": "G2", "right_grain": "G1"},
            ],
            "candidates": [
                {"a": "e0", "b": "e7", "cost": 0},
                {"a": "e1", "b": "e2", "cost": 0},
                {"a": "e1", "b": "e4", "cost": 0},
                {"a": "e1", "b": "e6", "cost": 0},
                {"a": "e2", "b": "e3", "cost": 0},
                {"a": "e2", "b": "e5", "cost": 0},
                {"a": "e3", "b": "e4", "cost": 0},
                {"a": "e3", "b": "e6", "cost": 0},
                {"a": "e4", "b": "e5", "cost": 0},
                {"a": "e5", "b": "e6", "cost": 0},
            ],
        }
        status, data = self.request(
            "POST", "/api/v1/stitch", json.dumps(payload_obj).encode()
        )
        self.assertEqual(status, 200, data)
        result = json.loads(data)
        self.assertTrue(result["feasible"])
        self.assertEqual(result["total_cost"], 0)
        self.assertEqual(result["optimal_solution_count"], 5)
        self.assertEqual(
            [(c["a"], c["b"]) for c in result["stitching"]],
            [("e0", "e7"), ("e1", "e2"), ("e3", "e4"), ("e5", "e6")],
        )
        self.assertEqual(
            [(e["a"], e["b"]) for e in result["ambiguity"]["in_all"]],
            [("e0", "e7")],
        )
        self.assertEqual(
            {(e["a"], e["b"]) for e in result["ambiguity"]["in_some"]},
            {
                ("e1", "e2"), ("e1", "e4"), ("e1", "e6"),
                ("e2", "e3"), ("e2", "e5"),
                ("e3", "e4"), ("e3", "e6"),
                ("e4", "e5"), ("e5", "e6"),
            },
        )
        self.assertEqual(result["ambiguity"]["in_none"], [])

        # 候选顺序整体反转，并交换每条候选的 a/b 方向：
        # 两次响应必须字节等价，且解数仍为 5。
        swapped = {
            "endpoints": payload_obj["endpoints"],
            "candidates": [
                {"a": c["b"], "b": c["a"], "cost": c["cost"]}
                for c in reversed(payload_obj["candidates"])
            ],
        }
        status2, data2 = self.request(
            "POST", "/api/v1/stitch", json.dumps(swapped).encode()
        )
        self.assertEqual(status2, 200, data2)
        self.assertEqual(json.loads(data2)["optimal_solution_count"], 5)
        self.assertEqual(data2, data)

    def test_two_way_tie_ambiguity_buckets(self) -> None:
        # 两解并列：[(0,1),(2,3)] 与 [(0,3),(1,2)]，代价均为 2；
        # 四条候选都只在部分最优解中，in_all/in_none 均为空。
        payload = json.dumps(
            {
                "endpoints": [
                    {"id": "e0", "left_grain": "x", "right_grain": "y"},
                    {"id": "e1", "left_grain": "y", "right_grain": "x"},
                    {"id": "e2", "left_grain": "x", "right_grain": "y"},
                    {"id": "e3", "left_grain": "y", "right_grain": "x"},
                ],
                "candidates": [
                    {"a": "e0", "b": "e1", "cost": 1},
                    {"a": "e1", "b": "e2", "cost": 2},
                    {"a": "e2", "b": "e3", "cost": 1},
                    {"a": "e0", "b": "e3", "cost": 0},
                ],
            }
        ).encode()
        status, data = self.request("POST", "/api/v1/stitch", payload)
        self.assertEqual(status, 200, data)
        result = json.loads(data)
        self.assertEqual(result["optimal_solution_count"], 2)
        self.assertEqual(result["total_cost"], 2)
        self.assertEqual(
            [(c["a"], c["b"]) for c in result["stitching"]],
            [("e0", "e1"), ("e2", "e3")],
        )
        self.assertEqual(result["ambiguity"]["in_all"], [])
        self.assertEqual(result["ambiguity"]["in_none"], [])
        self.assertEqual(
            {(e["a"], e["b"]) for e in result["ambiguity"]["in_some"]},
            {("e0", "e1"), ("e1", "e2"), ("e2", "e3"), ("e0", "e3")},
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
