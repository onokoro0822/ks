#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""施設発生集中交通量計算のローカルWebアプリ。"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
import threading
from typing import Any
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import calculate_facility_trips as calculations
from age_distribution import build_age_distribution
from llm_facility_dialogue import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_TIMEOUT,
    QUESTIONS,
    new_state,
    process_dialogue_turn,
)
from scenario_distributions import generate_distributions


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
MAX_REQUEST_BYTES = 64 * 1024


def serialize_state(state: dict[str, Any]) -> dict[str, Any]:
    """ブラウザ表示用に状態を複製する。"""
    return json.loads(json.dumps(state, ensure_ascii=False))


def serialize_result(result: Any) -> dict[str, Any]:
    return {
        "name": result.name,
        "area_m2": result.area_m2,
        "area_ha": result.area_ha,
        "category": result.category,
        "base_rate": result.base_rate,
        "alpha1": result.alpha1,
        "alpha2": result.alpha2,
        "raw_rate": result.raw_rate,
        "applied_rate": result.applied_rate,
        "trip_ends": result.trip_ends,
    }


def csv_bytes(
    results: list[Any],
    total: float,
    adjusted_total: float | None,
    age_distribution: dict[str, Any] | None = None,
    web_research: dict[str, Any] | None = None,
) -> bytes:
    """計算結果をExcelで開けるUTF-8 BOM付きCSVへ変換する。"""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "用途",
        "延床面積_m2",
        "延床面積_ha",
        "区分",
        "基準原単位_人TE_ha日",
        "alpha1",
        "alpha2",
        "原単位_丸め前",
        "適用原単位_人TE_ha日",
        "発生集中交通量_人TE日",
    ])
    for result in results:
        writer.writerow([
            result.name,
            result.area_m2,
            result.area_ha,
            result.category,
            result.base_rate,
            result.alpha1,
            result.alpha2,
            result.raw_rate,
            result.applied_rate,
            result.trip_ends,
        ])
    writer.writerow([])
    writer.writerow(["単純合計_人TE日", total])
    if adjusted_total is not None:
        writer.writerow(["内々交通低減後合計_人TE日", adjusted_total])
    if age_distribution is not None:
        writer.writerow([])
        writer.writerow(["年齢性別別推定到着人トリップ", age_distribution["unit"]])
        writer.writerow([
            "年齢階級",
            "性別",
            *[item["name"] for item in age_distribution["facilities"]],
            "合計",
        ])
        facility_groups = {
            item["key"]: {
                group["age"]: {
                    gender["key"]: gender["count"]
                    for gender in group["genders"]
                }
                for group in item["groups"]
            }
            for item in age_distribution["facilities"]
        }
        for combined in age_distribution["combined_groups"]:
            age = combined["age"]
            for gender in combined["genders"]:
                writer.writerow([
                    age,
                    gender["name"],
                    *[
                        facility_groups[item["key"]][age][gender["key"]]
                        for item in age_distribution["facilities"]
                    ],
                    gender["count"],
                ])
        writer.writerow([
            "推定到着量合計",
            "全性別",
            *[
                item["estimated_arrivals"]
                for item in age_distribution["facilities"]
            ],
            age_distribution["estimated_arrivals_total"],
        ])
        writer.writerow(["参照JSON", age_distribution["reference_file"]])
        writer.writerow(["注記", age_distribution["note"]])
    if web_research and web_research.get("sources"):
        writer.writerow([])
        writer.writerow(["Web調査出典"])
        writer.writerow(["タイトル", "URL"])
        for source in web_research["sources"]:
            writer.writerow([source.get("title", ""), source.get("url", "")])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


class SessionStore:
    """対話状態とAPIキーをプロセス内だけに保持する。"""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def create(self, api_key: str) -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        session = {
            "id": session_id,
            "api_key": api_key,
            "state": new_state(),
            "history": [
                {
                    "role": "assistant",
                    "content": QUESTIONS["facility_types"],
                }
            ],
            "status": "active",
            "results": None,
            "total": None,
            "adjusted_total": None,
            "age_distribution": None,
            "lock": threading.RLock(),
        }
        with self._lock:
            self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError("対話セッションが見つかりません。新しい計算を開始してください。")
        return session


class FacilityWebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        configured_api_key: str,
        model: str,
        timeout: float,
    ) -> None:
        super().__init__(server_address, FacilityRequestHandler)
        self.configured_api_key = configured_api_key
        self.model = model
        self.timeout = timeout
        self.sessions = SessionStore()


class FacilityRequestHandler(BaseHTTPRequestHandler):
    server: FacilityWebServer

    def log_message(self, format: str, *args: Any) -> None:
        # APIキーや利用者の発話はログへ出さない。
        print(f"{self.address_string()} - {format % args}")

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        disposition: str = "",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _read_json(self) -> dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Lengthが不正です。") from exc
        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            raise ValueError("リクエストサイズが不正です。")
        raw = self.rfile.read(content_length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON形式のリクエストが必要です。") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSONオブジェクトが必要です。")
        return payload

    def _static(self, filename: str, content_type: str) -> None:
        path = STATIC_DIR / filename
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self._error(HTTPStatus.NOT_FOUND, "ファイルが見つかりません。")
            return
        self._send_bytes(HTTPStatus.OK, body, content_type)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._static("index.html", "text/html; charset=utf-8")
            return
        if path == "/app.js":
            self._static("app.js", "text/javascript; charset=utf-8")
            return
        if path == "/style.css":
            self._static("style.css", "text/css; charset=utf-8")
            return
        if path == "/api/config":
            self._send_json(
                HTTPStatus.OK,
                {
                    "api_key_configured": bool(self.server.configured_api_key),
                    "model": self.server.model,
                    "web_search_enabled": True,
                },
            )
            return
        if path == "/api/scenarios":
            try:
                payload = generate_distributions()
            except (FileNotFoundError, ValueError, KeyError, RuntimeError) as exc:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(HTTPStatus.OK, payload)
            return
        prefix = "/api/sessions/"
        suffix = "/result.csv"
        if path.startswith(prefix) and path.endswith(suffix):
            session_id = path[len(prefix) : -len(suffix)]
            self._download_csv(session_id)
            return
        self._error(HTTPStatus.NOT_FOUND, "ページが見つかりません。")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if path == "/api/sessions":
            self._create_session(payload)
            return
        prefix = "/api/sessions/"
        suffix = "/messages"
        if path.startswith(prefix) and path.endswith(suffix):
            session_id = path[len(prefix) : -len(suffix)]
            self._add_message(session_id, payload)
            return
        self._error(HTTPStatus.NOT_FOUND, "APIが見つかりません。")

    def _create_session(self, payload: dict[str, Any]) -> None:
        supplied_key = str(payload.get("api_key") or "").strip()
        api_key = self.server.configured_api_key or supplied_key
        if not api_key:
            self._error(HTTPStatus.BAD_REQUEST, "Gemini APIキーを入力してください。")
            return
        if len(api_key) > 512:
            self._error(HTTPStatus.BAD_REQUEST, "APIキーが長すぎます。")
            return
        session = self.server.sessions.create(api_key)
        self._send_json(
            HTTPStatus.CREATED,
            {
                "id": session["id"],
                "status": session["status"],
                "reply": QUESTIONS["facility_types"],
                "state": serialize_state(session["state"]),
            },
        )

    def _add_message(self, session_id: str, payload: dict[str, Any]) -> None:
        try:
            session = self.server.sessions.get(session_id)
        except KeyError as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc))
            return
        message = str(payload.get("message") or "").strip()
        if not message:
            self._error(HTTPStatus.BAD_REQUEST, "メッセージを入力してください。")
            return
        lock = session["lock"]
        with lock:
            if session["status"] != "active":
                self._error(HTTPStatus.CONFLICT, "この計算は終了しています。新しい計算を開始してください。")
                return
            try:
                turn = process_dialogue_turn(
                    calculations,
                    session["state"],
                    session["history"],
                    message,
                    api_key=session["api_key"],
                    model=self.server.model,
                    timeout=self.server.timeout,
                )
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:
                self._error(HTTPStatus.BAD_GATEWAY, f"LLMとの通信に失敗しました: {exc}")
                return

            response: dict[str, Any] = {
                "status": turn["status"],
                "reply": turn["reply"],
                "state": serialize_state(session["state"]),
            }
            if turn["status"] == "complete":
                session["status"] = "complete"
                session["results"] = turn["results"]
                session["total"] = turn["total"]
                session["adjusted_total"] = turn["adjusted_total"]
                session["age_distribution"] = build_age_distribution(turn["results"])
                response["result"] = {
                    "items": [serialize_result(item) for item in turn["results"]],
                    "total": turn["total"],
                    "adjusted_total": turn["adjusted_total"],
                    "age_distribution": session["age_distribution"],
                    "csv_url": f"/api/sessions/{session_id}/result.csv",
                }
            elif turn["status"] == "cancelled":
                session["status"] = "cancelled"
            self._send_json(HTTPStatus.OK, response)

    def _download_csv(self, session_id: str) -> None:
        try:
            session = self.server.sessions.get(session_id)
        except KeyError as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc))
            return
        lock = session["lock"]
        with lock:
            if session["status"] != "complete" or session["results"] is None:
                self._error(HTTPStatus.CONFLICT, "計算完了後にCSVをダウンロードできます。")
                return
            body = csv_bytes(
                session["results"],
                session["total"],
                session["adjusted_total"],
                session["age_distribution"],
                session["state"].get("web_research"),
            )
        self._send_bytes(
            HTTPStatus.OK,
            body,
            "text/csv; charset=utf-8",
            disposition='attachment; filename="facility_trip_estimation_result.csv"',
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="施設交通量計算のローカルWebアプリ")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--portは1〜65535で指定してください。")
    if args.timeout <= 0:
        parser.error("--timeoutは0より大きい値にしてください。")

    server = FacilityWebServer(
        (args.host, args.port),
        configured_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
        model=args.model,
        timeout=args.timeout,
    )
    host, port = server.server_address[:2]
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{display_host}:{port}/"
    print(f"Webアプリを起動しました: {url}")
    print("終了するには Control+C を押してください。")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWebアプリを終了します。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
