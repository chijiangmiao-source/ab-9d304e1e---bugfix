# 金相断端缝合分析服务（grain-stitch-service）

纯后端 HTTP 分析服务：对抛光缺口轮廓上按顺时针排列的断端，选出**覆盖每个断端恰好一次、
弦线互不相交、总证据代价最小**的缝合方案；在并列最优时给出规范结果，并标明每条候选连接
存在于**全部**最优解、**部分**最优解还是**零个**最优解。

- 仅依赖 Python 3.11 标准库，无第三方运行时依赖；
- 无前端、无任何在线服务调用；
- 提供 Dockerfile 与 Docker Compose、容器健康检查；
- 宿主机端口通过环境变量 `API_HOST_PORT` 配置；
- 同一合法请求重复提交（含候选顺序/方向打乱）得到**字节等价**响应。

## 问题模型与算法

断端按提交顺序视为圆周上顺时针排列的点 `0..n-1`，候选连接为弦。要求合法的非交叉完美匹配：

- 只有两侧晶粒标识**反向对应**的候选可采用：
  `left(a) == right(b)` 且 `right(a) == left(b)`（在请求校验阶段强制）；
- 两条弦的端点沿圆周交错即为相交（`a<c<b<d` 的交错形式），嵌套不相交；
- 最小化候选非负整数代价之和。

求解使用凸多边形非交叉完美匹配的**区间动态规划**：

```
C[s][L] = min_{t 为奇数偏移且存在候选(s,t)}  w(s,t) + C[s+1][t-s-1] + C[t+1][L-t-1]
```

在长度 `2n` 的倍增序列上计算，使跨过数组首尾的圆弧也被统一覆盖。复杂度
O(n³) 时间、O(n²) 空间（`n ≤ 400` 时最坏约 1.5–2.5 秒）。

- **规范解**：重建时每个区间取能达最优代价的最小配对端点，等价于在所有最优解中选取
  “排序后的端点对序列”字典序最小者。
- **歧义分类**：DP 同时用任意精度整数统计每个区间的最优方案数 `W`。含候选弦 `(p,q)` 的
  最优解数为 `W[p+1][内弧] × W[q+1][外弧]`（仅当其代价参与全局最优）：
  - 等于全局最优解数 → `in_all`；
  - 大于 0 且小于全局最优解数 → `in_some`；
  - 否则 → `in_none`。
  方案数为精确整数（最坏可达 Catalan(200)，约 117 位十进制），分类是严格判定。

## 运行

### Docker Compose（推荐）

```bash
# 默认宿主机端口 8080
docker compose up --build

# 自定义宿主机端口
API_HOST_PORT=9090 docker compose up --build
# 或写入 .env： echo 'API_HOST_PORT=9090' > .env
```

容器内监听端口由 `API_PORT` 控制（默认 8080，一般无需修改）。

### 直接运行（本机 Python 3.11）

```bash
API_HOST=0.0.0.0 API_PORT=8080 python3 -m app.server
```

### 健康检查

```bash
curl -s http://localhost:8080/healthz
# {"service":"grain-stitch-service","status":"ok"}
```

Dockerfile 内置 `HEALTHCHECK`，Compose 也声明了等价健康检查。

## API

### `POST /api/v1/stitch`

请求：

| 字段 | 说明 |
| --- | --- |
| `endpoints` | 2–400 个、偶数个，按顺时针排列；每项含 `id`、`left_grain`、`right_grain` |
| `candidates` | 候选连接数组；每项含 `a`、`b`（断端 `id`）、非负整数 `cost` |

`id` / 晶粒标识接受非空字符串或整数；候选方向可任意提交（服务内部规范化）。

成功响应（`200`）：

```json
{
  "feasible": true,
  "total_cost": 4,
  "optimal_solution_count": 2,
  "stitching": [
    {"a": "e0", "a_index": 0, "b": "e1", "b_index": 1, "cost": 3},
    {"a": "e2", "a_index": 2, "b": "e3", "b_index": 3, "cost": 1}
  ],
  "ambiguity": {
    "in_all": [],
    "in_some": [ {"a": "e0", "a_index": 0, "b": "e1", "b_index": 1}, ... ],
    "in_none": []
  }
}
```

- `stitching`：规范缝合（按 `(a_index, b_index)` 排序）；
- `ambiguity.in_all`：存在于**全部**最优解的候选；
- `ambiguity.in_some`：只存在于**部分**最优解的候选（同成本歧义）；
- `ambiguity.in_none`：不属于任何最优解的候选；
- `optimal_solution_count`：最优解精确个数（大整数）。

无解时返回 `422`：

```json
{
  "feasible": false,
  "error": {"code": "no_feasible_stitching", "message": "..."},
  "ambiguity": {"in_all": [], "in_some": [], "in_none": [ ... ]}
}
```

字段或候选矛盾返回 `400`，每个错误都可定位（`location` 为 JSON Pointer）：

```json
{
  "error": {
    "code": "validation_failed",
    "details": [
      {"code": "candidate_side_mismatch",
       "message": "只有两侧晶粒标识反向对应的候选才可采用：...",
       "location": "/candidates/0"}
    ]
  }
}
```

校验项包括：断端数量必须为 2–400 的偶数；`id` 非空且不重复；晶粒标识非空；
`cost` 为非负整数；候选引用必须存在且不可自环；同一对断端不可重复提交候选；
候选两侧必须反向对应；未知字段拒绝。多个矛盾会一次性全部返回。

### 字节等价保证

成功/无解响应均使用规范 JSON 序列化（键排序、紧凑分隔符、UTF-8、固定结尾换行），
输出只取决于断端的**顺时针位置**与候选的**无向代价**，与候选提交顺序、`a/b` 方向无关。

示例：

```bash
curl -s -X POST http://localhost:8080/api/v1/stitch \
  -H 'Content-Type: application/json' \
  --data @examples/request.json | python3 -m json.tool
```

## 测试

```bash
python3 -m unittest discover -s tests -v
```

包含 400 组随机小规模实例与暴力枚举（全部完美匹配 + 交叉过滤）的交叉校验，
覆盖最小代价、精确最优解数、规范缝合字典序与 in_all/in_some/in_none 分类；
另有 n=400 最坏规模压测（完整候选图约 4 万条边、Catalan(200) 歧义场景）与
真实 socket 的 HTTP 端到端测试（健康检查、422、400 定位、重复提交字节等价）。
