"""凸多边形非交叉完美匹配求解器。

断端按提交顺序视为顺时针排列在一个圆周（抛光缺口轮廓）上，候选连接为弦。
要求：

* 每个断端恰好被一条弦覆盖（完美匹配）；
* 弦与弦互不相交（非交叉）；
* 仅允许两侧晶粒标识反向对应的候选（已在校验层保证）；
* 总证据代价最小；
* 并列最优时给出规范结果，并标注每条候选存在于全部/部分/零个最优解。

算法
----

经典区间动态规划：设 ``C[s][L]`` 为倍增序列上连续 ``L`` 个顶点（偶数个）
内部非交叉完美匹配的最小代价，``W[s][L]`` 为取得该代价的方案数。
区间最左端 ``s`` 必与某个奇数偏移位置 ``t`` 相连，弦把区间切成两段独立子区间：

    C[s][L] = min over t of w(s,t) + C[s+1][t-s-1] + C[t+1][s+L-t-1]

在长度为 ``2n+1`` 的倍增序列上计算，可以直接表示跨过数组首尾、沿圆周外侧的弧，
从而用同一套线性区间 DP 统计任意一条候选弦两侧圆弧上的方案数。

方案数使用 Python 任意精度整数精确统计（``n <= 400`` 时非交叉完美匹配数上限为
Catalan(200)，约 117 位十进制），因此 "存在于全部最优解" 是严格判定而非抽样估计。
"""

from __future__ import annotations

from dataclasses import dataclass

from .validation import Candidate, Endpoint


@dataclass(frozen=True)
class Connection:
    a_index: int
    b_index: int
    a_id: str
    b_id: str
    cost: int


@dataclass(frozen=True)
class AmbiguityEntry:
    a_index: int
    b_index: int
    a_id: str
    b_id: str


@dataclass(frozen=True)
class SolveResult:
    feasible: bool
    total_cost: int | None
    stitching: tuple[Connection, ...]
    in_all: tuple[AmbiguityEntry, ...]
    in_some: tuple[AmbiguityEntry, ...]
    in_none: tuple[AmbiguityEntry, ...]
    optimal_solution_count: int


def solve(endpoints: list[Endpoint], candidates: list[Candidate]) -> SolveResult:
    n = len(endpoints)

    id_to_index = {ep.id: i for i, ep in enumerate(endpoints)}

    # w[u * n + v] 为候选代价，-1 表示不存在候选（代价本身非负）。
    # 一维数组在 CPython 内层循环里比嵌套 list 略快。
    w = [-1] * (n * n)
    for cand in candidates:
        u = id_to_index[cand.a]
        v = id_to_index[cand.b]
        if u > v:
            u, v = v, u
        w[u * n + v] = cand.cost

    # 在长度 2n 的倍增序列上计算：起点 s 取 0..2n-L，使跨过数组首尾的圆弧
    # 也能作为一个普通线性区间被统计（用于候选弦“外侧弧”的方案数）。
    size = 2 * n + 1
    # cost[s][L]：None 表示无法完美匹配；L 仅偶数时有值。
    cost: list[list[int | None]] = [[None] * (n + 1) for _ in range(size)]
    ways: list[list[int]] = [[0] * (n + 1) for _ in range(size)]
    for s in range(size):
        cost[s][0] = 0
        ways[s][0] = 1

    # 按区间长度递增；只处理偶数长度。
    def edge_cost(s: int, t: int) -> int:
        """倍增序列位置 s、t 之间候选的规范代价，-1 表示无候选。"""
        u, v = s % n, t % n
        return w[u * n + v] if u < v else w[v * n + u]

    for length in range(2, n + 1, 2):
        for s in range(2 * n - length + 1):
            end = s + length
            best: int | None = None
            count = 0
            # t 与 s 的偏移必须为奇数，切出的两段才各含偶数顶点。
            for t in range(s + 1, end, 2):
                c = edge_cost(s, t)
                if c < 0:
                    continue
                c1 = cost[s + 1][t - s - 1]
                c2 = cost[t + 1][end - t - 1]
                if c1 is None or c2 is None:
                    continue
                value = c + c1 + c2
                branch = ways[s + 1][t - s - 1] * ways[t + 1][end - t - 1]
                if best is None or value < best:
                    best = value
                    count = branch
                elif value == best:
                    # 精确方案数（任意精度整数），不得截断：多重同优时
                    # optimal_solution_count 与逐连接分类都依赖完整计数。
                    count += branch
            cost[s][length] = best
            ways[s][length] = count

    total_cost = cost[0][n]
    total_ways = ways[0][n]
    if total_cost is None:
        in_none: list[AmbiguityEntry] = []
        for cand in candidates:
            p, q = sorted((id_to_index[cand.a], id_to_index[cand.b]))
            in_none.append(AmbiguityEntry(p, q, endpoints[p].id, endpoints[q].id))
        in_none.sort(key=lambda e: (e.a_index, e.b_index))
        return SolveResult(
            feasible=False,
            total_cost=None,
            stitching=(),
            in_all=(),
            in_some=(),
            in_none=tuple(in_none),
            optimal_solution_count=0,
        )

    # 规范解重建：每个区间选择能取得最优代价的最小 t，
    # 等价于在所有最优解中取“排序后端点对序列”字典序最小者。
    stitching: list[Connection] = []

    def build(s: int, length: int) -> None:
        if length == 0:
            return
        end = s + length
        chosen: int | None = None
        for t in range(s + 1, end, 2):
            c = edge_cost(s, t)
            if c < 0:
                continue
            c1 = cost[s + 1][t - s - 1]
            c2 = cost[t + 1][end - t - 1]
            if c1 is None or c2 is None:
                continue
            if c + c1 + c2 == cost[s][length]:
                chosen = t
                break
        assert chosen is not None
        t = chosen
        u, v = sorted((s % n, t % n))
        stitching.append(
            Connection(
                u,
                v,
                endpoints[u].id,
                endpoints[v].id,
                w[u * n + v],
            )
        )
        build(s + 1, t - s - 1)
        build(t + 1, end - t - 1)

    build(0, n)
    stitching.sort(key=lambda conn: (conn.a_index, conn.b_index))

    # 每条候选弦 (p,q)（p<q）把圆周切成两段互不相交的圆弧：
    #   内侧线性区间 [p+1, q-1]，长度 q-p-1；
    #   跨过数组首尾的外侧弧 [q+1..n-1, 0..p-1]，
    #   在倍增序列上就是起点 q+1、长度 n-(q-p)-1 的普通区间。
    # 含该弦的最优匹配数 = W[p+1][内侧长度] × W[q+1][外侧长度]，
    # 且要求该弦代价加两段最优代价恰好等于全局最优代价。
    in_all: list[AmbiguityEntry] = []
    in_some: list[AmbiguityEntry] = []
    in_none: list[AmbiguityEntry] = []

    for cand in candidates:
        p, q = sorted((id_to_index[cand.a], id_to_index[cand.b]))
        entry = AmbiguityEntry(p, q, endpoints[p].id, endpoints[q].id)
        inner_len = q - p - 1
        outer_len = n - (q - p) - 1
        c_inner = cost[p + 1][inner_len]
        c_outer = cost[q + 1][outer_len]
        edge_ways = 0
        if c_inner is not None and c_outer is not None:
            edge_cost_value = w[p * n + q]
            if edge_cost_value + c_inner + c_outer == total_cost:
                edge_ways = ways[p + 1][inner_len] * ways[q + 1][outer_len]
        if edge_ways == 0:
            in_none.append(entry)
        elif edge_ways == total_ways:
            in_all.append(entry)
        else:
            in_some.append(entry)

    in_all.sort(key=lambda e: (e.a_index, e.b_index))
    in_some.sort(key=lambda e: (e.a_index, e.b_index))
    in_none.sort(key=lambda e: (e.a_index, e.b_index))

    return SolveResult(
        feasible=True,
        total_cost=total_cost,
        stitching=tuple(stitching),
        in_all=tuple(in_all),
        in_some=tuple(in_some),
        in_none=tuple(in_none),
        optimal_solution_count=total_ways,
    )
