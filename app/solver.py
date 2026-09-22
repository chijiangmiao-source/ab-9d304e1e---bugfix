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

    # 倍增序列长度 2n：下标是圆周位置，顶点为 pos % n。这样任意候选弦
    # 两侧的两段圆弧（包括跨过数组首尾的外侧弧）都是表内的连续区间。
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
        last_start = 2 * n - length
        for s in range(last_start + 1):
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
                # 方案数用任意精度整数精确累计，不做截断。
                if best is None or value < best:
                    best = value
                    count = (
                        ways[s + 1][t - s - 1]
                        * ways[t + 1][end - t - 1]
                    )
                elif value == best:
                    count += (
                        ways[s + 1][t - s - 1]
                        * ways[t + 1][end - t - 1]
                    )
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

    # 每条候选弦 (p,q)（p<q）把圆周切成内侧弧 [p+1, q) 与跨过首尾的外侧弧
    # [q+1, n+p)（倍增序列下标）。给定该弦后两段弧相互独立，因此：
    #
    #     含该弦的最优解数 = W[p+1][内弧长] × W[q+1][外弧长]
    #
    # 前提是该弦代价与两弧各自的最小代价之和等于全局最优（否则该弦不属于
    # 任何最优解）。与全局最优解数比较即得严格分类：
    #   等于 0            -> in_none
    #   等于全局最优解数  -> in_all
    #   其余              -> in_some
    in_all: list[AmbiguityEntry] = []
    in_some: list[AmbiguityEntry] = []
    in_none: list[AmbiguityEntry] = []

    for cand in candidates:
        p, q = sorted((id_to_index[cand.a], id_to_index[cand.b]))
        entry = AmbiguityEntry(p, q, endpoints[p].id, endpoints[q].id)
        inner_length = q - p - 1
        outer_length = n + p - q - 1
        containing = 0
        inner_cost = cost[p + 1][inner_length]
        outer_cost = cost[q + 1][outer_length]
        if (
            inner_cost is not None
            and outer_cost is not None
            and cand.cost + inner_cost + outer_cost == total_cost
        ):
            containing = ways[p + 1][inner_length] * ways[q + 1][outer_length]
        if containing == 0:
            in_none.append(entry)
        elif containing == total_ways:
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
