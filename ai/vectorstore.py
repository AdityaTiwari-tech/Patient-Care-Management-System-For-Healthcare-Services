"""
ai/vectorstore.py
Thin persistence layer over Chroma. If chromadb isn't installed, falls
back to an in-memory list + naive cosine search so the app doesn't crash.
"""
import os
from ai.embeddings import embed_texts

_PERSIST_DIR = os.path.join(os.path.dirname(__file__), "..", ".chroma")


class _FallbackStore:
    def __init__(self):
        self.docs, self.vecs, self.metas = [], [], []

    def add(self, docs, metadatas):
        self.docs.extend(docs)
        self.metas.extend(metadatas)
        self.vecs.extend(embed_texts(docs))

    def query(self, text, k=3):
        if not self.docs:
            return []
        import math
        q = embed_texts([text])[0]

        def cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
            return dot / (na * nb + 1e-9)

        scored = sorted(range(len(self.docs)), key=lambda i: -cos(q, self.vecs[i]))
        return [self.docs[i] for i in scored[:k]]


def get_collection(name: str = "smartcare_kb"):
    try:
        import chromadb
        client = chromadb.PersistentClient(path=_PERSIST_DIR)
        return client.get_or_create_collection(name)
    except Exception:
        return _FallbackStore()


def index_documents(collection, docs: list[str], metadatas: list[dict]):
    if isinstance(collection, _FallbackStore):
        collection.add(docs, metadatas)
        return
    try:
        # Always supply our own embeddings (see ai/embeddings.py) so Chroma
        # never tries to download its default ONNX model over the network —
        # that download is what causes httpx.ReadTimeout on flaky/offline
        # connections.
        ids = [str(i) for i in range(len(docs))]
        embeddings = embed_texts(docs)
        collection.add(documents=docs, metadatas=metadatas, ids=ids, embeddings=embeddings)
    except Exception:
        # Any Chroma failure here just means the chatbot loses hospital-
        # directory grounding for this session — it should never crash the page.
        pass


def search(collection, query: str, k: int = 3) -> list[str]:
    if isinstance(collection, _FallbackStore):
        return collection.query(query, k)
    try:
        query_embedding = embed_texts([query])
        result = collection.query(query_embeddings=query_embedding, n_results=k)
        return result.get("documents", [[]])[0]
    except Exception:
        return []