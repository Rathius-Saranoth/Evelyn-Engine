import sys
sys.path.insert(0, r'c:\Projects\LocalAI\Evelyn\tools')
import chroma_rag

col = chroma_rag.get_or_create_collection('evelyn_gists')

res = col.get(include=["metadatas"])
ids = res.get("ids", [])
metadatas = res.get("metadatas", [])

ids_to_delete = []
for doc_id, meta in zip(ids, metadatas):
    source = meta.get("source", "").lower()
    if "context entries" in source or "ce_" in source:
        ids_to_delete.append(doc_id)

print(f"Deleting {len(ids_to_delete)} orphaned chunks from evelyn_gists.")
if ids_to_delete:
    col.delete(ids=ids_to_delete)
    print("Done!")
