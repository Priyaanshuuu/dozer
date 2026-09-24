# Personalized banking chatbot

A runnable sample for [#1690](https://github.com/getdozer/dozer/issues/1690): Dozer
joins customer records with live transaction aggregates; LangChain combines the
resulting profile with credit-card documents retrieved from Chroma. Ollama runs
the embedding and chat models locally. All people, products, and amounts are fictional.

## Architecture and compatibility

```text
PostgreSQL customers ----+
                        +--> Dozer SQL --> ClickHouse --> profile HTTP API --+
PostgreSQL transactions -+                                                  |
                                                                           +--> LangChain --> Ollama
Product documents --> Ollama embeddings --> Chroma --> semantic retrieval ---+
```

This sample targets the sink-based configuration in this checkout. The original
`dozer-api` crate was removed in commit `242d75d2`; adding `endpoints:` to this
checkout's YAML does not restore generated APIs. `profile_api.py` is an explicit,
sample-owned replacement exposing Dozer's output over HTTP. It does not query
PostgreSQL or compute the customer profile. No changes to the Rust engine are needed.

Dozer computes all-history spending, including category totals and transaction
counts, and left-joins it to customers (including customers with no transactions).
Amounts are integer USD cents. The ClickHouse sink uses `CollapsingMergeTree` for
updates/deletes; the API reads `FINAL` and `sign = 1` so cancelled versions do not
appear while background merges are pending.

Each chat turn fetches the profile again. Customer identity is selected when the
CLI session starts and cannot be changed by a model tool call. Only public product
documents are stored in Chroma. The query uses spending context, and retrieval
filters on a fictional minimum-income rule. The model explains suitability and
fees with product IDs as references; it does not make lending decisions.

## Run

Prerequisites: Docker Compose with a running Linux container engine, and a Dozer
binary built from this checkout. For Windows, run Dozer in WSL2 with Docker Desktop
WSL integration. Allow several GB for local models; CPU inference can be slow.

From the repository root, in your Rust build environment:

```sh
cargo build --release --bin dozer
cd samples/banking-chatbot
docker compose up -d --build postgres clickhouse api ollama
docker compose exec ollama ollama pull llama3.2:3b
docker compose exec ollama ollama pull nomic-embed-text
```

Then build the configuration and run Dozer in a dedicated terminal:

```sh
../../target/release/dozer -c dozer-config.yaml build
../../target/release/dozer -c dozer-config.yaml run
```

The configuration connects to PostgreSQL on `localhost:55432` and ClickHouse's
native protocol on `localhost:59000`. The profile API is published only on
`127.0.0.1:58000`. If a port is occupied, update both Compose and the configuration.

In another terminal in this sample directory:

```sh
curl --fail http://localhost:58000/customers/1
docker compose run --rm chat python products.py
docker compose run --rm chat python chat.py --customer 1
```

The API can return 503 until Dozer has created its output table; wait for snapshot
ingestion to finish before chatting. A missing customer returns 404. A failed API
lookup stops the answer rather than silently using an old profile.

Try the same question, `Which credit card suits my spending?`, for customer 1 and
customer 2. Alex has $2,000 travel spending, $200 dining, and $90,000 annual income;
Sam has $1,000 groceries, $100 dining, and $45,000 annual income. Customer 3 has no
transactions. Responses should explain these differences and cite retrieved
products. Wording and recommendations are model-dependent, not test assertions.

For a single response including profile and retrieved source IDs:

```sh
docker compose run --rm chat python chat.py --customer 2 --question "Compare cards for me"
```

## Show a live update

With Dozer still running:

```sh
docker compose exec postgres psql -U bank -d bank -c "INSERT INTO transactions VALUES (100, 1, 'groceries', 500000);"
```

Poll `/customers/1` until `grocery_spend_cents` is `500000`, then ask another
question in the same chat session. The fresh profile now includes substantial
grocery spending. Do not re-index Chroma: the products have not changed.

Restore the dataset after the demo:

```sh
docker compose exec postgres psql -U bank -d bank -c "DELETE FROM transactions WHERE transaction_id = 100;"
```

## Validation

Offline application tests use real LangChain and Chroma with deterministic test
embeddings and a stub model; they do not download models or call external APIs:

```sh
docker compose run --rm --no-deps chat python -m unittest discover -s tests -v
```

They check aggregate results (via SQLite), customers without transactions,
insert/update/delete arithmetic, HTTP errors, customer isolation, index
idempotence, income filtering, and fresh context on subsequent chat turns.
SQLite checks are not a substitute for running the Dozer SQL planner.

An additional opt-in test checks the API's actual ClickHouse query against inserts,
updates, and deletes in a temporary database (removed after the test):

```sh
docker compose exec -e LIVE_CLICKHOUSE_TESTS=1 api python -m unittest tests.test_clickhouse -v
```

For actual CDC validation, use Python 3.11+ on the host (the first command needs
only the standard library), with Dozer and the Compose services running:

```sh
python smoke.py
```

This expects the original seed state. It inserts a reserved transaction, updates
it, waits for the API to reflect each change, checks another customer is unchanged,
and deletes the transaction in a `finally` block. It fails if updates do not arrive
within 60 seconds (`--timeout` can override this).

To exercise a real LLM before and after the CDC change, install the application
locally. The Compose Ollama service is available on `localhost:11434`:

```sh
python -m venv .venv
# Activate .venv using your shell's activation command.
python -m pip install -r requirements.txt
python products.py
python smoke.py --llm
```

Alternatively use an existing host Ollama instance instead of the Compose Ollama
service and pull the same two models. `.env.example` documents the
environment variables; local Python processes read exported environment variables,
not `.env` files automatically. The default host Ollama address is port 11434.

`--llm` prints both responses for manual assessment of grounding and personalization;
it checks updated context and nonempty output, not exact model wording.

## Lifecycle and limitations

- Run one Dozer process for this sample. Treat the demo as a fresh session; do not
  assume exactly-once recovery of this stateful pipeline across restarts. Reusing a
  populated sink with a fresh snapshot can duplicate state.
- To start clean, stop Dozer, run `docker compose down --volumes` in this directory,
  and remove this sample's `.dozer` directory and `dozer.lock` if present. This
  removes all sample database/index/model volumes, so models must be downloaded again.
- Re-running `products.py` upserts stable product IDs. Catalog or embedding-model
  name changes select a new collection; index again before chatting. If you replace
  model weights under the same name, use a new name or a fresh Chroma directory.
- This is a local synthetic-data demo. The adapter has no authentication. A deployed
  application must derive customer identity from authentication rather than a CLI
  argument and enforce it on the server.
- Product citations are prompt-guided; retrieved source IDs are also returned
  separately for inspection. Income filters and product terms are fictional.

Integration references: [LangChain Ollama](https://docs.langchain.com/oss/python/integrations/chat/ollama)
and [LangChain Chroma](https://docs.langchain.com/oss/python/integrations/vectorstores/chroma).
