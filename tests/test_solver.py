"""求解器测试：小规模实例用枚举全部非交叉完美匹配的暴力程序交叉验证。"""

from __future__ import annotations

import itertools
import random
import unittest

from app.solver import solve
from app.validation import Candidate, Endpoint, parse_request


def brute_force(n: int, weights: dict[tuple[int, int], int]):
    """枚举圆上所有完美匹配并过滤交叉，规模限制在 n<=10。

    返回 (min_cost 或 None, optimal_matchings:set[frozenset])。
    """
    all_matchings: list[frozenset] = []

    def enum(seq: list[int], pairs: frozenset) -> None:
        if not seq:
            all_matchings.append(pairs)
            return
        first = seq[0]
        for j in range(1, len(seq)):
            other = seq[j]
            edge = (min(first, other), max(first, other))
            if edge not in weights:
                continue
            rest = seq[1:j] + seq[j + 1 :]
            enum(rest, pairs | {edge})

    def noncrossing(matching: frozenset) -> bool:
        for (a, b), (c, d) in itertools.combinations(matching, 2):
            if a < c < b < d or c < a < d < b:
                return False
        return True

    enum(list(range(n)), frozenset())
    feasible = [m for m in all_matchings if noncrossing(m)]
    if not feasible:
        return None, set()
    best = min(sum(weights[e] for e in m) for m in feasible)
    optimal = {m for m in feasible if sum(weights[e] for e in m) == best}
    return best, optimal


def canonical_from_set(matching: frozenset) -> tuple[tuple[int, int], ...]:
    return tuple(sorted(tuple(sorted(e)) for e in matching))


def make_typed_endpoints(spec: list[tuple[str, str]]) -> list[Endpoint]:
    """按 (left_grain, right_grain) 序列构造断端。"""
    return [
        Endpoint(i, f"e{i}", left, right)
        for i, (left, right) in enumerate(spec)
    ]


class BruteForceComparisonTests(unittest.TestCase):
    def test_random_against_brute_force(self) -> None:
        rng = random.Random(20260919)
        for trial in range(400):
            n = 2 * rng.randint(1, 5)  # 2..10
            # 先随机生成两侧晶粒标识，再据此推导合法（反向对应）候选边集合。
            pool = ["a", "b", "c"]
            spec = [(rng.choice(pool), rng.choice(pool)) for _ in range(n)]
            compatible_edges = [
                (u, v)
                for u in range(n)
                for v in range(u + 1, n)
                if spec[u][0] == spec[v][1] and spec[u][1] == spec[v][0]
            ]
            rng.shuffle(compatible_edges)
            chosen_edges = compatible_edges[: rng.randint(0, len(compatible_edges))]
            weights = {e: rng.randint(0, 5) for e in chosen_edges}  # 大量并列
            endpoints = make_typed_endpoints(spec)
            candidates = [
                Candidate(k, f"e{u}", f"e{v}", c)
                for k, ((u, v), c) in enumerate(weights.items())
            ]
            result = solve(endpoints, candidates)
            best, optimal = brute_force(n, weights)

            if best is None:
                self.assertFalse(result.feasible, f"trial {trial}: 误判为有解")
                self.assertEqual(result.optimal_solution_count, 0)
                continue

            self.assertTrue(result.feasible, f"trial {trial}: 误判无解 {weights}")
            self.assertEqual(result.total_cost, best)
            self.assertEqual(result.optimal_solution_count, len(optimal))

            # 规范缝合必须是全部最优解中排序端点对序列字典序最小者。
            expected_canon = min(canonical_from_set(m) for m in optimal)
            got_canon = tuple((c.a_index, c.b_index) for c in result.stitching)
            self.assertEqual(got_canon, expected_canon, f"trial {trial}")

            in_all_edges = frozenset.intersection(*optimal)
            in_some_edges = frozenset.union(*optimal) - in_all_edges
            self.assertEqual(
                {(e.a_index, e.b_index) for e in result.in_all}, set(in_all_edges)
            )
            self.assertEqual(
                {(e.a_index, e.b_index) for e in result.in_some}, set(in_some_edges)
            )
            self.assertEqual(
                {(e.a_index, e.b_index) for e in result.in_none},
                set(weights) - set(frozenset.union(*optimal)),
            )

    def test_wrap_around_tie(self) -> None:
        # 4 个断端。两种不交叉匹配代价并列：
        #   (0,1)+(2,3) 与 (0,3)+(1,2)（后者在圆周上跨过首尾，线性视角为嵌套，不相交）。
        # 0、2 同侧晶粒 (x,y)，1、3 为反向 (y,x)，使四条候选全部合法。
        spec = [("x", "y"), ("y", "x"), ("x", "y"), ("y", "x")]
        weights = {(0, 1): 1, (1, 2): 2, (2, 3): 1, (0, 3): 0}
        endpoints = make_typed_endpoints(spec)
        candidates = [
            Candidate(k, f"e{u}", f"e{v}", c)
            for k, ((u, v), c) in enumerate(weights.items())
        ]
        result = solve(endpoints, candidates)
        self.assertTrue(result.feasible)
        self.assertEqual(result.total_cost, 2)
        self.assertEqual(result.optimal_solution_count, 2)
        self.assertEqual(result.in_all, ())
        self.assertEqual(
            {(e.a_index, e.b_index) for e in result.in_some},
            {(0, 1), (2, 3), (1, 2), (0, 3)},
        )
        # 规范解取字典序：[(0,1),(2,3)] 先于 [(0,3),(1,2)]
        self.assertEqual(
            [(c.a_index, c.b_index) for c in result.stitching],
            [(0, 1), (2, 3)],
        )

    def test_unique_solution_all_edges_in_all(self) -> None:
        spec = [("x", "y"), ("y", "x"), ("x", "y"), ("y", "x")]
        weights = {(0, 1): 0, (2, 3): 0, (0, 3): 5, (1, 2): 5}
        endpoints = make_typed_endpoints(spec)
        candidates = [
            Candidate(k, f"e{u}", f"e{v}", c)
            for k, ((u, v), c) in enumerate(weights.items())
        ]
        result = solve(endpoints, candidates)
        self.assertEqual(result.total_cost, 0)
        self.assertEqual(result.optimal_solution_count, 1)
        self.assertEqual(
            {(e.a_index, e.b_index) for e in result.in_all}, {(0, 1), (2, 3)}
        )
        self.assertEqual(result.in_some, ())
        self.assertEqual(
            {(e.a_index, e.b_index) for e in result.in_none}, {(0, 3), (1, 2)}
        )

    def test_infeasible_odd_isolation(self) -> None:
        # 6 个断端，0 只能与 2 相连，导致顶点 1 被孤立在奇数区域。
        # 标签：(X,Y) 与 (Y,X) 反向对应；只提交其中部分候选。
        spec = [
            ("X", "Y"),  # 0
            ("X", "Y"),  # 1
            ("Y", "X"),  # 2
            ("Y", "X"),  # 3
            ("X", "Y"),  # 4
            ("Y", "X"),  # 5
        ]
        weights = {(0, 2): 1, (1, 3): 1, (3, 4): 1, (4, 5): 1, (0, 5): 1}
        endpoints = make_typed_endpoints(spec)
        candidates = [
            Candidate(k, f"e{u}", f"e{v}", c)
            for k, ((u, v), c) in enumerate(weights.items())
        ]
        result = solve(endpoints, candidates)
        self.assertFalse(result.feasible)
        self.assertIsNone(result.total_cost)


class ValidationTests(unittest.TestCase):
    def good_payload(self) -> dict:
        return {
            "endpoints": [
                {"id": "a", "left_grain": "g1", "right_grain": "g2"},
                {"id": "b", "left_grain": "g2", "right_grain": "g1"},
            ],
            "candidates": [{"a": "a", "b": "b", "cost": 3}],
        }

    def test_good_request(self) -> None:
        req = parse_request(self.good_payload())
        self.assertEqual(len(req.endpoints), 2)
        self.assertEqual(req.candidates[0].cost, 3)

    def test_orientation_must_be_reversed(self) -> None:
        payload = self.good_payload()
        payload["endpoints"][1] = {
            "id": "b",
            "left_grain": "g1",
            "right_grain": "g2",
        }  # 同向而非反向
        with self.assertRaises(Exception) as ctx:
            parse_request(payload)
        codes = {d["code"] for d in ctx.exception.details}
        self.assertIn("candidate_side_mismatch", codes)
        mismatch = [
            d for d in ctx.exception.details if d["code"] == "candidate_side_mismatch"
        ][0]
        self.assertEqual(mismatch["location"], "/candidates/0")

    def test_multiple_locatable_errors(self) -> None:
        payload = {
            "endpoints": [
                {"id": "a", "left_grain": "g1", "right_grain": "g2"},
                {"id": "a", "left_grain": "g2", "right_grain": "g1"},
                {"id": "c", "left_grain": "g3"},
            ],
            "candidates": [
                {"a": "a", "b": "zzz", "cost": -1},
                {"a": "a", "b": "a", "cost": 0},
            ],
        }
        with self.assertRaises(Exception) as ctx:
            parse_request(payload)
        locations = {d["location"] for d in ctx.exception.details}
        self.assertIn("/endpoints/1/id", locations)  # 重复 id
        self.assertIn("/endpoints/2/right_grain", locations)  # 缺字段
        self.assertIn("/endpoints", locations)  # 奇数个
        self.assertIn("/candidates/0/b", locations)  # 未知引用
        self.assertIn("/candidates/0/cost", locations)  # 负代价
        self.assertIn("/candidates/1", locations)  # 自环

    def test_duplicate_candidate_rejected(self) -> None:
        payload = self.good_payload()
        payload["candidates"].append({"a": "b", "b": "a", "cost": 3})
        with self.assertRaises(Exception) as ctx:
            parse_request(payload)
        self.assertIn("duplicate_candidate", {d["code"] for d in ctx.exception.details})

    def test_cost_must_be_integer(self) -> None:
        payload = self.good_payload()
        payload["candidates"][0]["cost"] = 1.5
        with self.assertRaises(Exception) as ctx:
            parse_request(payload)
        self.assertIn("invalid_cost", {d["code"] for d in ctx.exception.details})

    def test_unknown_field(self) -> None:
        payload = self.good_payload()
        payload["endpoints"][0]["color"] = "blue"
        with self.assertRaises(Exception) as ctx:
            parse_request(payload)
        self.assertIn("unknown_field", {d["code"] for d in ctx.exception.details})

    def test_boundary_counts(self) -> None:
        def payload_with(n: int) -> dict:
            return {
                "endpoints": [
                    {"id": f"e{i}", "left_grain": f"l{i}", "right_grain": f"r{i}"}
                    for i in range(n)
                ],
                "candidates": [],
            }

        parse_request(payload_with(2))
        parse_request(payload_with(400))
        for bad in (0, 1, 3, 402):
            with self.assertRaises(Exception) as ctx:
                parse_request(payload_with(bad))
            self.assertIn(
                "invalid_endpoint_count",
                {d["code"] for d in ctx.exception.details},
            )

    def test_integer_ids_accepted(self) -> None:
        payload = {
            "endpoints": [
                {"id": 1, "left_grain": "g1", "right_grain": "g2"},
                {"id": 2, "left_grain": "g2", "right_grain": "g1"},
            ],
            "candidates": [{"a": 1, "b": 2, "cost": 0}],
        }
        req = parse_request(payload)
        self.assertEqual(req.endpoints[0].id, "1")


if __name__ == "__main__":
    unittest.main()
