"""请求解析与校验。

所有校验错误都携带 JSON Pointer 风格的 ``location``，便于调用方定位字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ValidationError(Exception):
    """请求校验失败，details 为可定位错误列表。"""

    def __init__(self, details: list[dict[str, str]]):
        self.details = details
        super().__init__("; ".join(d["message"] for d in details))


@dataclass(frozen=True)
class Endpoint:
    index: int
    id: str
    left_grain: str
    right_grain: str


@dataclass(frozen=True)
class Candidate:
    index: int
    a: str
    b: str
    cost: int


@dataclass(frozen=True)
class StitchRequest:
    endpoints: list[Endpoint]
    candidates: list[Candidate]


_ALLOWED_TOP_KEYS = {"endpoints", "candidates"}
_ALLOWED_ENDPOINT_KEYS = {"id", "left_grain", "right_grain"}
_ALLOWED_CANDIDATE_KEYS = {"a", "b", "cost"}

_MAX_ENDPOINTS = 400
_MIN_ENDPOINTS = 2


def _identifier(value: Any) -> str | None:
    """端点/晶粒标识接受非空字符串或非布尔整数，统一为字符串。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value if value != "" else None
    if isinstance(value, int):
        return str(value)
    return None


def _nonneg_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _err(errors: list[dict[str, str]], code: str, message: str, location: str) -> None:
    errors.append({"code": code, "message": message, "location": location})


def parse_request(payload: Any) -> StitchRequest:
    """把已解析的 JSON 负载校验并转换为 StitchRequest。"""
    errors: list[dict[str, str]] = []

    if not isinstance(payload, dict):
        raise ValidationError(
            [{"code": "invalid_body", "message": "请求体必须是 JSON 对象", "location": "/"}]
        )

    for key in payload:
        if key not in _ALLOWED_TOP_KEYS:
            _err(
                errors,
                "unknown_field",
                f"存在不支持的字段 {key!r}，允许的字段为 endpoints、candidates",
                f"/{key}",
            )

    raw_endpoints = payload.get("endpoints")
    raw_candidates = payload.get("candidates")

    if "endpoints" not in payload:
        _err(errors, "missing_field", "缺少 endpoints 字段", "/endpoints")
    if "candidates" not in payload:
        _err(errors, "missing_field", "缺少 candidates 字段", "/candidates")

    endpoints: list[Endpoint] = []
    id_to_index: dict[str, int] = {}

    if isinstance(raw_endpoints, list):
        count = len(raw_endpoints)
        if count < _MIN_ENDPOINTS or count > _MAX_ENDPOINTS or count % 2 != 0:
            _err(
                errors,
                "invalid_endpoint_count",
                f"断端数量必须是 {_MIN_ENDPOINTS} 至 {_MAX_ENDPOINTS} 之间的偶数，当前为 {count}",
                "/endpoints",
            )

        for i, raw in enumerate(raw_endpoints):
            loc = f"/endpoints/{i}"
            if not isinstance(raw, dict):
                _err(errors, "invalid_type", "断端必须是对象", loc)
                continue
            for key in raw:
                if key not in _ALLOWED_ENDPOINT_KEYS:
                    _err(
                        errors,
                        "unknown_field",
                        f"断端存在不支持的字段 {key!r}",
                        f"{loc}/{key}",
                    )

            ep_id: str | None = None
            left: str | None = None
            right: str | None = None

            if "id" not in raw:
                _err(errors, "missing_field", "断端缺少 id 字段", f"{loc}/id")
            else:
                ep_id = _identifier(raw["id"])
                if ep_id is None:
                    _err(
                        errors,
                        "invalid_identifier",
                        "断端 id 必须是非空字符串或非布尔整数",
                        f"{loc}/id",
                    )
                elif ep_id in id_to_index:
                    _err(
                        errors,
                        "duplicate_endpoint_id",
                        f"断端 id {ep_id!r} 重复（首次出现于 endpoints/{id_to_index[ep_id]}）",
                        f"{loc}/id",
                    )
                else:
                    id_to_index[ep_id] = i

            if "left_grain" not in raw:
                _err(errors, "missing_field", "断端缺少 left_grain 字段", f"{loc}/left_grain")
            else:
                left = _identifier(raw["left_grain"])
                if left is None:
                    _err(
                        errors,
                        "invalid_identifier",
                        "left_grain 必须是非空字符串或非布尔整数",
                        f"{loc}/left_grain",
                    )

            if "right_grain" not in raw:
                _err(errors, "missing_field", "断端缺少 right_grain 字段", f"{loc}/right_grain")
            else:
                right = _identifier(raw["right_grain"])
                if right is None:
                    _err(
                        errors,
                        "invalid_identifier",
                        "right_grain 必须是非空字符串或非布尔整数",
                        f"{loc}/right_grain",
                    )

            if ep_id is not None and left is not None and right is not None:
                endpoints.append(Endpoint(i, ep_id, left, right))
    elif "endpoints" in payload:
        _err(errors, "invalid_type", "endpoints 必须是数组", "/endpoints")

    candidates: list[Candidate] = []
    if isinstance(raw_candidates, list) and isinstance(raw_endpoints, list) and endpoints:
        endpoint_by_id = {ep.id: ep for ep in endpoints}
        seen_pairs: dict[tuple[str, str], int] = {}

        for i, raw in enumerate(raw_candidates):
            loc = f"/candidates/{i}"
            if not isinstance(raw, dict):
                _err(errors, "invalid_type", "候选连接必须是对象", loc)
                continue
            for key in raw:
                if key not in _ALLOWED_CANDIDATE_KEYS:
                    _err(
                        errors,
                        "unknown_field",
                        f"候选连接存在不支持的字段 {key!r}",
                        f"{loc}/{key}",
                    )

            a_id: str | None = None
            b_id: str | None = None

            if "a" not in raw:
                _err(errors, "missing_field", "候选连接缺少 a 字段", f"{loc}/a")
            else:
                a_id = _identifier(raw["a"])
                if a_id is None:
                    _err(
                        errors,
                        "invalid_identifier",
                        "候选连接端点必须是非空字符串或非布尔整数",
                        f"{loc}/a",
                    )
                elif a_id not in endpoint_by_id:
                    _err(
                        errors,
                        "unknown_endpoint_reference",
                        f"候选连接引用了不存在的断端 id {a_id!r}",
                        f"{loc}/a",
                    )

            if "b" not in raw:
                _err(errors, "missing_field", "候选连接缺少 b 字段", f"{loc}/b")
            else:
                b_id = _identifier(raw["b"])
                if b_id is None:
                    _err(
                        errors,
                        "invalid_identifier",
                        "候选连接端点必须是非空字符串或非布尔整数",
                        f"{loc}/b",
                    )
                elif b_id not in endpoint_by_id:
                    _err(
                        errors,
                        "unknown_endpoint_reference",
                        f"候选连接引用了不存在的断端 id {b_id!r}",
                        f"{loc}/b",
                    )

            cost: int | None = None
            if "cost" not in raw:
                _err(errors, "missing_field", "候选连接缺少 cost 字段", f"{loc}/cost")
            else:
                cost = _nonneg_int(raw["cost"])
                if cost is None:
                    _err(errors, "invalid_cost", "cost 必须是非负整数", f"{loc}/cost")

            valid_refs = (
                a_id is not None
                and b_id is not None
                and a_id in endpoint_by_id
                and b_id in endpoint_by_id
            )

            if valid_refs and a_id == b_id:
                _err(errors, "self_loop", "候选连接的两个断端不能相同", loc)

            if valid_refs and a_id != b_id:
                pair_key = tuple(sorted((a_id, b_id)))
                if pair_key in seen_pairs:
                    _err(
                        errors,
                        "duplicate_candidate",
                        f"同一对断端已在 candidates/{seen_pairs[pair_key]} 提交过，"
                        "重复候选（即使代价相同）属于矛盾数据",
                        loc,
                    )
                else:
                    seen_pairs[pair_key] = i

                ep_a = endpoint_by_id[a_id]
                ep_b = endpoint_by_id[b_id]
                reversed_match = (
                    ep_a.left_grain == ep_b.right_grain
                    and ep_a.right_grain == ep_b.left_grain
                )
                if not reversed_match:
                    _err(
                        errors,
                        "candidate_side_mismatch",
                        "只有两侧晶粒标识反向对应的候选才可采用：需要 "
                        "left(a)==right(b) 且 right(a)==left(b)，"
                        f"实际为 ({ep_a.left_grain!r},{ep_a.right_grain!r}) 与 "
                        f"({ep_b.left_grain!r},{ep_b.right_grain!r})",
                        loc,
                    )
                elif cost is not None:
                    candidates.append(Candidate(i, a_id, b_id, cost))
    elif "candidates" in payload and not isinstance(raw_candidates, list):
        _err(errors, "invalid_type", "candidates 必须是数组", "/candidates")

    if errors:
        raise ValidationError(errors)

    return StitchRequest(endpoints=endpoints, candidates=candidates)
