import os

class KnowledgeBase:
    def __init__(self, docs_dir="docs"):
        self.docs_dir = docs_dir
        self.documents = {}
        self._load_documents()

    def _load_documents(self):
        """Loads all text files from the docs directory into memory."""
        if not os.path.exists(self.docs_dir):
            print(f"Warning: Knowledge base directory '{self.docs_dir}' not found.")
            return

        for filename in os.listdir(self.docs_dir):
            if filename.endswith(".txt"):
                filepath = os.path.join(self.docs_dir, filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    self.documents[filename] = f.read()
        print(f"Loaded {len(self.documents)} documents into knowledge base.")

    def search(self, query: str) -> list[str]:
        """
        Performs a simple keyword-based search across all documents.
        Returns a list of document contents that contain any of the query keywords.
        """
        query_keywords = query.lower().split()
        results = []
        
        print(f"Searching knowledge base for keywords: {query_keywords}")

        for doc_name, content in self.documents.items():
            content_lower = content.lower()
            if any(keyword in content_lower for keyword in query_keywords):
                results.append(f"--- Document: {doc_name} ---\
{content}\
")
        
        return results

if __name__ == "__main__":
    # Simple test for KnowledgeBase
    kb = KnowledgeBase()
    
    print("\
--- Test Search 1 (AI) ---")
    results1 = kb.search("AI")
    for r in results1:
        print(r)

    print("\
--- Test Search 2 (RAG) ---")
    results2 = kb.search("RAG")
    for r in results2:
        print(r)

    print("\
--- Test Search 3 (newstate) ---")
    results3 = kb.search("newstate")
    for r in results3:
        print(r)
