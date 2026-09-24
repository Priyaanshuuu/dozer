"""Exercise actual PostgreSQL -> Dozer -> ClickHouse -> API CDC, optionally LLM."""

import argparse
import json
from pathlib import Path
import subprocess
import time

from dozer_client import ProfileClient, ProfileError

ROOT = Path(__file__).parent


def sql(statement):
    subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "bank", "-d", "bank",
         "-v", "ON_ERROR_STOP=1", "-c", statement], cwd=ROOT, check=True, capture_output=True,
    )


def wait_for(client, predicate, timeout, identifier=1):
    deadline = time.monotonic() + timeout
    last = "no profile"
    while time.monotonic() < deadline:
        try:
            last = client.fetch(identifier)
            if predicate(last):
                return last
        except ProfileError as error:
            last = str(error)
        time.sleep(0.5)
    raise RuntimeError(f"CDC did not reach the expected state in {timeout}s: {last}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--llm", action="store_true", help="Also call the real LangChain/Chroma/Ollama flow.")
    args = parser.parse_args()
    client = ProfileClient()
    baseline = wait_for(client, lambda row: row["total_spend_cents"] == 220000 and row["transaction_count"] == 3, args.timeout)
    other = wait_for(client, lambda row: row["grocery_spend_cents"] == 100000 and row["total_spend_cents"] == 110000, args.timeout, 2)
    wait_for(client, lambda row: row["transaction_count"] == 0, args.timeout, 3)
    session = None
    if args.llm:
        from chat import ChatSession
        from products import vector_store
        from langchain_ollama import ChatOllama
        import os
        session = ChatSession(1, client, vector_store(), ChatOllama(
            model=os.environ.get("CHAT_MODEL", "llama3.2:3b"), temperature=0,
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            client_kwargs={"timeout": 180},
        ))
        print(json.dumps(session.ask("Which card suits my spending and why?"), indent=2))
    # Reserved test ID; only delete it if this process successfully inserted it.
    inserted = False
    try:
        sql("INSERT INTO transactions VALUES (990001, 1, 'groceries', 500000)")
        inserted = True
        wait_for(client, lambda row: row["grocery_spend_cents"] == 500000 and row["transaction_count"] == 4, args.timeout)
        sql("UPDATE transactions SET amount_cents = 600000 WHERE transaction_id = 990001")
        updated = wait_for(client, lambda row: row["grocery_spend_cents"] == 600000 and row["total_spend_cents"] == 820000, args.timeout)
        assert client.fetch(2) == other, "Another customer's profile changed"
        print("CDC insert/update passed:", json.dumps(updated))
        if session:
            result = session.ask("Has my spending changed? Compare suitable cards now.")
            assert result["profile"]["grocery_spend_cents"] == 600000
            assert result["answer"].strip() and result["sources"]
            print(json.dumps(result, indent=2))
    finally:
        if inserted:
            sql("DELETE FROM transactions WHERE transaction_id = 990001")
            wait_for(client, lambda row: row == baseline, args.timeout)
            print("CDC delete passed; original profile restored.")


if __name__ == "__main__":
    main()
