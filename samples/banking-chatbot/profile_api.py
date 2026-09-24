"""Read-only HTTP adapter over Dozer's ClickHouse output; no source DB access."""

import base64
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

FIELDS = (
    "customer_id", "display_name", "annual_income_cents", "current_card",
    "transaction_count", "total_spend_cents", "travel_spend_cents",
    "grocery_spend_cents", "dining_spend_cents",
)


def customer_id(value):
    text = str(value)
    if not re.fullmatch(r"[1-9][0-9]{0,18}", text) or int(text) > 2**63 - 1:
        raise ValueError("customer ID must be a positive 64-bit integer")
    return int(text)


def read_profile(identifier):
    identifier = customer_id(identifier)
    # FINAL collapses old/new CDC rows at query time, before background merges.
    # sign=1 excludes cancelled/deleted records after collapsing.
    sql = (
        f"SELECT {', '.join(FIELDS)} FROM customer_profiles FINAL "
        "WHERE customer_id = {id:Int64} AND sign = 1 FORMAT JSON"
    )
    params = urlencode({
        "param_id": identifier,
        "database": os.environ.get("CLICKHOUSE_DATABASE", "default"),
        "output_format_json_quote_64bit_integers": 0,
    })
    url = os.environ.get("CLICKHOUSE_URL", "http://localhost:8123").rstrip("/")
    credentials = "{}:{}".format(
        os.environ.get("CLICKHOUSE_USER", "bank"),
        os.environ.get("CLICKHOUSE_PASSWORD", "bank"),
    )
    request = Request(
        f"{url}/?{params}", data=sql.encode(),
        headers={"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()},
    )
    try:
        with urlopen(request, timeout=10) as response:
            rows = json.load(response)["data"]
    except HTTPError as error:
        error.close()
        raise
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("ambiguous customer profile")
    return rows[0]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        match = re.fullmatch(r"/customers/([0-9]+)", self.path)
        if not match:
            self.reply(404, {"error": "use /customers/<customer_id>"})
            return
        try:
            identifier = customer_id(match[1])
        except ValueError as error:
            self.reply(400, {"error": str(error)})
            return
        try:
            profile = read_profile(identifier)
            self.reply(200 if profile else 404, profile or {"error": "customer not found"})
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
            self.reply(503, {"error": "profile unavailable; check Dozer and ClickHouse"})

    def reply(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    host = os.environ.get("API_HOST", "127.0.0.1")
    print(f"Customer profile API listening on {host}:8000", flush=True)
    ThreadingHTTPServer((host, 8000), Handler).serve_forever()
