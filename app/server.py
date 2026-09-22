"""纯标准库 HTTP 服务。

路由：

* ``GET  /healthz`` 健康检查；
* ``POST /api/v1/stitch`` 提交断端与候选，返回最低代价缝合及歧义分类。

响应体使用固定的紧凑 JSON 规范序列化（键排序、无多余空白），
保证同一合法请求重复提交得到字节等价响应。
"""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .solver import solve
from .validation import ValidationError, parse_request

SERVICE_NAME = "grain-stitch-service"
MAX_BODY_BYTES = 8 * 1024 * 1024


def canonical_dumps(payload: Any) -> bytes:
    """规范 JSON 序列化：键排序、紧凑分隔符，保证字节级稳定。"""
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _connection_json(conn: Any) -> dict[str, Any]:
    return {
        "a": conn.a_id,
        "b": conn.b_id,
        "a_index": conn.a_index,
        "b_index": conn.b_index,
        "cost": conn.cost,
    }


def _entry_json(entry: Any) -> dict[str, Any]:
    return {
        "a": entry.a_id,
        "b": entry.b_id,
        "a_index": entry.a_index,
        "b_index": entry.b_index,
    }


def build_success_response(result: Any) -> dict[str, Any]:
    return {
        "feasible": True,
        "total_cost": result.total_cost,
        "optimal_solution_count": result.optimal_solution_count,
        "stitching": [_connection_json(c) for c in result.stitching],
        "ambiguity": {
            "in_all": [_entry_json(e) for e in result.in_all],
            "in_some": [_entry_json(e) for e in result.in_some],
            "in_none": [_entry_json(e) for e in result.in_none],
        },
    }


class StitchHandler(BaseHTTPRequestHandler):
    server_version = "GrainStitch/1.0"

    def _write_json(self, status: HTTPStatus, payload: Any) -> None:
        body = canonical_dumps(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # 保持访问日志单行、简洁。
        import sys

        sys.stderr.write(
            "%s - - %s\n" % (self.address_string(), fmt % args)
        )

    def do_GET(self) -> None:  # noqa: N802 - http.server 接口命名
        if self.path.split("?", 1)[0] == "/healthz":
            self._write_json(
                HTTPStatus.OK,
                {"status": "ok", "service": SERVICE_NAME},
            )
            return
        if self.path.split("?", 1)[0] == "/":
            self._write_json(
                HTTPStatus.OK,
                {
                    "service": SERVICE_NAME,
                    "endpoints": {
                        "health": "GET /healthz",
                        "stitch": "POST /api/v1/stitch",
                    },
                },
            )
            return
        self._write_json(
            HTTPStatus.NOT_FOUND,
            {
                "error": {
                    "code": "not_found",
                    "message": f"路径 {self.path!r} 不存在",
                }
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/v1/stitch":
            self._write_json(
                HTTPStatus.NOT_FOUND,
                {
                    "error": {
                        "code": "not_found",
                        "message": f"路径 {self.path!r} 不存在",
                    }
                },
            )
            return

        length_header = self.headers.get("Content-Length")
        if length_header is None:
            self._write_json(
                HTTPStatus.LENGTH_REQUIRED,
                {
                    "error": {
                        "code": "missing_content_length",
                        "message": "必须提供 Content-Length 头",
                    }
                },
            )
            return
        try:
            length = int(length_header)
        except ValueError:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": {
                        "code": "invalid_content_length",
                        "message": "Content-Length 必须是非负整数",
                    }
                },
            )
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._write_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {
                    "error": {
                        "code": "body_too_large",
                        "message": f"请求体不得超过 {MAX_BODY_BYTES} 字节",
                    }
                },
            )
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": {
                        "code": "invalid_json",
                        "message": f"请求体不是合法的 UTF-8 JSON：{exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)}",
                        "location": "/",
                    }
                },
            )
            return

        try:
            request = parse_request(payload)
        except ValidationError as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "validation_failed", "details": exc.details}},
            )
            return

        result = solve(request.endpoints, request.candidates)
        if not result.feasible:
            self._write_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {
                    "feasible": False,
                    "error": {
                        "code": "no_feasible_stitching",
                        "message": (
                            "不存在覆盖每个断端恰好一次且弦线互不相交的缝合："
                            "候选图中没有合法的非交叉完美匹配"
                        ),
                    },
                    "ambiguity": {
                        "in_all": [],
                        "in_some": [],
                        "in_none": [_entry_json(e) for e in result.in_none],
                    },
                },
            )
            return

        self._write_json(HTTPStatus.OK, build_success_response(result))


def create_server(host: str, port: int) -> ThreadingHTTPServer:
    # daemon_threads：关闭进程时不等待存量连接。
    server = ThreadingHTTPServer((host, port), StitchHandler)
    server.daemon_threads = True
    return server


def main() -> None:
    host = os.environ.get("API_HOST", "0.0.0.0")
    port_raw = os.environ.get("API_PORT", "8080")
    try:
        port = int(port_raw)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise SystemExit(f"API_PORT 环境变量必须是 1-65535 的整数，当前为 {port_raw!r}")

    server = create_server(host, port)
    print(f"{SERVICE_NAME} listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
