"""Index only public fictional products, never customer profiles."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from chromadb.config import Settings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings

PRODUCTS = Path(__file__).parent / "data" / "products.json"


def collection_name(model):
    # Changing either catalog contents or embedding model requires a new index.
    digest = hashlib.sha256(PRODUCTS.read_bytes() + model.encode()).hexdigest()[:20]
    return f"bank-products-{digest}"


def vector_store():
    model = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
    return Chroma(
        collection_name=collection_name(model),
        persist_directory=os.environ.get("CHROMA_DIR", ".chroma"),
        client_settings=Settings(anonymized_telemetry=False),
        embedding_function=OllamaEmbeddings(
            model=model, base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        ),
    )


def index_products(store):
    products = json.loads(PRODUCTS.read_text(encoding="utf-8"))
    documents = [Document(
        page_content=f"{product['name']}\n{product['description']}",
        metadata={"product_id": product["id"], "minimum_income_cents": product["minimum_income_cents"]},
    ) for product in products]
    store.add_documents(documents, ids=[product["id"] for product in products])
    return len(documents)


if __name__ == "__main__":
    argparse.ArgumentParser(description="Embed the fictional credit-card catalog in Chroma.").parse_args()
    print(f"Indexed {index_products(vector_store())} products.")
