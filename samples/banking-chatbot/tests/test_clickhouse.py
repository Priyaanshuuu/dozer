"""Opt-in adapter check against actual ClickHouse, independent of Dozer/LLM."""

import base64
import json
import os
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from uuid import uuid4

from profile_api import read_profile


@unittest.skipUnless(os.environ.get("LIVE_CLICKHOUSE_TESTS") == "1", "requires live ClickHouse")
class ClickHouseTests(unittest.TestCase):
    def execute(self, sql):
        credentials = f"{os.environ.get('CLICKHOUSE_USER', 'bank')}:{os.environ.get('CLICKHOUSE_PASSWORD', 'bank')}"
        request = Request(
            os.environ.get("CLICKHOUSE_URL", "http://localhost:8123"), data=sql.encode(),
            headers={"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()},
        )
        with urlopen(request, timeout=10) as response:
            return response.read()

    def test_current_profile_after_insert_update_and_delete(self):
        # Never write into the sample's output table or an existing database.
        database = "banking_test_" + uuid4().hex
        self.execute(f"CREATE DATABASE {database}")
        self.addCleanup(self.execute, f"DROP DATABASE {database}")
        table = database + ".customer_profiles"
        self.execute(f"""CREATE TABLE {table} (
            customer_id Int64, display_name String, annual_income_cents Int64,
            current_card String, transaction_count Int64, total_spend_cents Int64,
            travel_spend_cents Int64, grocery_spend_cents Int64,
            dining_spend_cents Int64, sign Int8
        ) ENGINE = CollapsingMergeTree(sign) ORDER BY customer_id""")
        old = dict(customer_id=1, display_name="Alex", annual_income_cents=9000000,
                   current_card="basic", transaction_count=1, total_spend_cents=1000,
                   travel_spend_cents=1000, grocery_spend_cents=0, dining_spend_cents=0)
        new = dict(old, transaction_count=2, total_spend_cents=3000, grocery_spend_cents=2000)
        def insert(*rows):
            self.execute(f"INSERT INTO {table} FORMAT JSONEachRow\n" + "\n".join(json.dumps(row) for row in rows))
        with patch.dict(os.environ, {"CLICKHOUSE_DATABASE": database}):
            self.assertIsNone(read_profile(1))
            insert(dict(old, sign=1))
            self.assertEqual(read_profile(1), old)
            insert(dict(old, sign=-1), dict(new, sign=1))
            self.assertEqual(read_profile(1), new)
            self.assertIsNone(read_profile(2))
            insert(dict(new, sign=-1))
            self.assertIsNone(read_profile(1))


if __name__ == "__main__":
    unittest.main()
