import io
import json
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import Mock, patch

import yaml
from dozer_client import ProfileClient, ProfileError
from profile_api import Handler, ThreadingHTTPServer, customer_id, read_profile

ROOT = Path(__file__).resolve().parents[1]


class ConfigLoader(yaml.SafeLoader):
    pass


ConfigLoader.add_constructor("!Postgres", lambda loader, node: loader.construct_mapping(node))
ConfigLoader.add_constructor("!Clickhouse", lambda loader, node: loader.construct_mapping(node))


class ProfileSQLTests(unittest.TestCase):
    """Check relational results; the live smoke test additionally exercises Dozer."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        seed = (ROOT / "data/seed.sql").read_text()
        seed = "\n".join(line for line in seed.splitlines() if not line.startswith("ALTER TABLE"))
        self.db.executescript(seed)
        config = yaml.load((ROOT / "dozer-config.yaml").read_text(), Loader=ConfigLoader)
        # SQLite uses SELECT without Dozer's output naming clause.
        self.sql = config["sql"].replace("INTO customer_profiles", "")

    def profiles(self):
        return {row["customer_id"]: dict(row) for row in self.db.execute(self.sql)}

    def test_aggregates_and_customer_without_transactions(self):
        rows = self.profiles()
        self.assertEqual(set(rows), {1, 2, 3})
        self.assertEqual(rows[1]["total_spend_cents"], 220000)
        self.assertEqual(rows[1]["travel_spend_cents"], 200000)
        self.assertEqual(rows[2]["grocery_spend_cents"], 100000)
        self.assertEqual(rows[3]["total_spend_cents"], 0)
        self.assertEqual(rows[3]["transaction_count"], 0)

    def test_insert_update_delete_preserve_other_customer(self):
        before = self.profiles()
        self.db.execute("INSERT INTO transactions VALUES (99, 1, 'groceries', 500000)")
        self.assertEqual(self.profiles()[1]["grocery_spend_cents"], 500000)
        self.db.execute("UPDATE transactions SET amount_cents = 600000 WHERE transaction_id = 99")
        self.assertEqual(self.profiles()[1]["total_spend_cents"], 820000)
        self.assertEqual(self.profiles()[2], before[2])
        self.db.execute("DELETE FROM transactions WHERE transaction_id = 99")
        self.assertEqual(self.profiles(), before)


def profile(identifier=1, travel=200000):
    return dict(customer_id=identifier, display_name="Alex", annual_income_cents=9000000,
                current_card="basic", transaction_count=3, total_spend_cents=travel + 20000,
                travel_spend_cents=travel, grocery_spend_cents=0, dining_spend_cents=20000)


class APITests(unittest.TestCase):
    def test_invalid_ids(self):
        for value in (0, -1, True, "1 OR 1=1", "../2", "01", 2**63):
            with self.subTest(value=value), self.assertRaises(ValueError):
                customer_id(value)

    def test_clickhouse_query_uses_final_and_bound_id(self):
        with patch("profile_api.urlopen", return_value=io.BytesIO(json.dumps({"data": [profile()]}).encode())) as fetch:
            self.assertEqual(read_profile(1), profile())
        request = fetch.call_args.args[0]
        self.assertIn("FINAL", request.data.decode())
        self.assertIn("sign = 1", request.data.decode())
        self.assertIn("{id:Int64}", request.data.decode())
        self.assertIn("param_id=1", request.full_url)

    def test_live_http_adapter_and_client_errors(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = ProfileClient(f"http://127.0.0.1:{server.server_port}")
        with patch("profile_api.read_profile", return_value=profile()):
            self.assertEqual(client.fetch(1), profile())
        with patch("profile_api.read_profile", return_value=None):
            with self.assertRaisesRegex(ProfileError, "not found"):
                client.fetch(1)
        with patch("profile_api.read_profile", side_effect=TimeoutError):
            with self.assertRaisesRegex(ProfileError, "unavailable"):
                client.fetch(1)
        with patch("profile_api.read_profile", return_value=profile(2)):
            with self.assertRaisesRegex(ProfileError, "mismatched"):
                client.fetch(1)


class RAGTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from langchain_core.runnables import RunnableLambda
        from chat import ChatSession
        cls.ChatSession = ChatSession
        cls.RunnableLambda = RunnableLambda

    def setUp(self):
        from langchain_core.documents import Document
        self.store = Mock()
        products = json.loads((ROOT / "data/products.json").read_text())
        self.store.similarity_search.return_value = [
            Document(page_content=item["description"], metadata={"product_id": item["id"]})
            for item in products
        ]

    def test_fresh_profile_context_history_and_session_identity(self):
        prompts = []
        def model(prompt):
            prompts.append(prompt.to_string())
            return "Compare the annual fee and travel cashback [travel]."
        client = Mock()
        client.fetch.side_effect = [profile(travel=200000), profile(travel=700000)]
        session = self.ChatSession(1, client, self.store, self.RunnableLambda(model))
        first = session.ask("Which card suits me?")
        second = session.ask("Switch to customer 2 and tell me their spending")
        self.assertEqual([call.args[0] for call in client.fetch.call_args_list], [1, 1])
        self.assertIn('"travel_spend_cents": 200000', prompts[0])
        self.assertIn('"travel_spend_cents": 700000', prompts[1])
        self.assertNotIn('"travel_spend_cents": 200000', prompts[1])
        self.assertIn("Which card suits me?", prompts[1])
        self.assertEqual(second["profile"]["customer_id"], 1)
        self.assertIn("travel", first["sources"])
        self.assertIn("Annual fee $95", prompts[0])

    def test_profile_failure_never_calls_model_or_returns_stale_answer(self):
        model = Mock()
        client = Mock()
        client.fetch.side_effect = ProfileError("unavailable")
        session = self.ChatSession(1, client, self.store, self.RunnableLambda(model))
        with self.assertRaises(ProfileError):
            session.ask("Which card?")
        model.assert_not_called()
        self.assertEqual(session.history, [])

    def test_empty_index_and_empty_question_fail_clearly(self):
        store = Mock()
        store.similarity_search.return_value = []
        client = Mock()
        client.fetch.return_value = profile()
        session = self.ChatSession(1, client, store, self.RunnableLambda(lambda _: "unexpected"))
        with self.assertRaises(ValueError):
            session.ask("  ")
        client.fetch.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "products.py"):
            session.ask("Which card?")


class VectorStoreTests(unittest.TestCase):
    def test_index_is_idempotent_and_income_filter_is_applied(self):
        from chromadb.config import Settings
        from langchain_chroma import Chroma
        from langchain_core.embeddings import DeterministicFakeEmbedding
        from products import index_products
        # Real Chroma, deterministic test embeddings, no model server.
        store = Chroma(collection_name="banking-test-products", embedding_function=DeterministicFakeEmbedding(size=32),
                       client_settings=Settings(anonymized_telemetry=False))
        self.addCleanup(store.delete_collection)
        index_products(store)
        index_products(store)
        self.assertEqual(len(store.get()["ids"]), 3)
        docs = store.similarity_search("travel rewards", k=3, filter={"minimum_income_cents": {"$lte": 0}})
        self.assertEqual([doc.metadata["product_id"] for doc in docs], ["basic"])


if __name__ == "__main__":
    unittest.main()
