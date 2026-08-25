#!/bin/bash

# Fix knowledge_base.py
cat <<'EOF' > dycrag_system/knowledge_base.py
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
                results.append(f"--- Document: {doc_name} ---
{content}
")
        
        return results

if __name__ == "__main__":
    # Simple test for KnowledgeBase
    kb = KnowledgeBase()
    
    print("
--- Test Search 1 (AI) ---")
    results1 = kb.search("AI")
    for r in results1:
        print(r)

    print("
--- Test Search 2 (RAG) ---")
    results2 = kb.search("RAG")
    for r in results2:
        print(r)

    print("
--- Test Search 3 (newstate) ---")
    results3 = kb.search("newstate")
    for r in results3:
        print(r)
EOF

# Fix agent.py
cat <<'EOF' > dycrag_system/agent.py
import random
import os

class DyCRAGAgent:
    def __init__(self, knowledge_base):
        self.knowledge_base = knowledge_base
        self.state = {
            "current_goal": "",
            "understanding": "",
            "retrieval_strategy": "broad", # Can be 'broad', 'focused', 're-evaluate'
            "retrieval_query": "",
            "retrieved_info": [],
            "llm_calls_count": 0,
            "thoughts": []
        }

    def _call_llm_placeholder(self, prompt: str) -> str:
        """Simulates an LLM call."""
        self.state["llm_calls_count"] += 1
        print(f"  [LLM Placeholder Called, count: {self.state['llm_calls_count']}]")
        
        # This is where an actual LLM would generate a response.
        # For now, it's a simple placeholder based on current state.
        response_parts = []
        response_parts.append("Based on retrieved information:")
        if self.state["retrieved_info"]:
            response_parts.append("
" + "\
".join(self.state["retrieved_info"]))
        else:
            response_parts.append("No specific information found.")
        
        response_parts.append(f"

Regarding '{self.state['current_goal']}', I am formulating a response.My current understanding leads me to believe...") # Simplified for placeholder

        return "
".join(response_parts)

    def _determine_retrieval_query(self, current_goal: str) -> str:
        """Determines what to search for in the knowledge base."""
        if not self.state["understanding"]:
            # Initial broad search: extract key terms from the goal
            self.state["current_query_strategy"] = "broad"
            keywords = []
            for term in ["AI", "RAG", "newstate", "agentic"]: # Hardcode some expected key terms for now
                if term.lower() in current_goal.lower():
                    keywords.append(term)
            if keywords:
                return " ".join(keywords)
            return current_goal.split(" ")[0] # Fallback to first word if no key terms found
        
        # Placeholder for more sophisticated query generation
        return "further details" # Example: if understanding is partial, seek further details

    def _update_understanding(self, new_info: list[str]):
        """Updates the agent's understanding based on new information."""
        if new_info:
            # In a real agent, this would involve integrating new_info with existing understanding
            # For this demo, we'll simply store the new_info
            self.state["retrieved_info"] = new_info
            print("Agent updated its understanding with new information.")
        else:
            print("No new information to update understanding.")

    def iterate(self, initial_goal: str, max_iterations: int = 5):
        self.state["current_goal"] = initial_goal
        print(f"Agent Goal: {initial_goal}
")

        for i in range(1, max_iterations + 1):
            print(f"--- Agent Iteration {i} ---")
            
            # 1. Plan Retrieval
            retrieval_query = self._determine_retrieval_query(self.state["current_goal"])
            self.state["retrieval_query"] = retrieval_query
            self.state["thoughts"].append(f"My current strategy is '{self.state['retrieval_strategy']}'. I will search for '{retrieval_query}'.")
            print(f"Agent thought: {self.state['thoughts'][-1]}")

            # 2. Retrieve Information
            retrieved_chunks = self.knowledge_base.search(retrieval_query)
            self.state["retrieved_info"] = retrieved_chunks

            # 3. Update Understanding
            self._update_understanding(retrieved_chunks)

            # 4. Generate Interim Response/Thought (LLM Call)
            llm_prompt = f"Given the goal '{self.state['current_goal']}' and retrieved information: {self.state['retrieved_info']}, provide an interim thought or partial response."
            interim_response = self._call_llm_placeholder(llm_prompt)
            self.state["understanding"] = interim_response # Update understanding with LLM's thought
            print("Agent's interim response/thought:")
            print(self.state["understanding"])
            print("")

        print("--- Final Agent Summary ---")
        print(f"Goal: {self.state['current_goal']}")
        print(f"Total LLM Placeholder Calls: {self.state['llm_calls_count']}")
        print("
Final Understanding (Aggregated):
")
        print(self.state["understanding"]) # In a real agent, this would be a more refined final output

        print("
Final Generated Response:")
        print(self.state["understanding"]) # Using interim for now, refine with actual LLM output
EOF

# Fix main.py
cat <<'EOF' > dycrag_system/main.py
import os
print("DEBUG: main.py started execution.") # Early debug print
from knowledge_base import KnowledgeBase
from agent import DyCRAGAgent

def run_dycrag_demonstration():
    print(f"DEBUG: Current working directory in main.py: {os.getcwd()}") # Confirm CWD
    print("Initializing DyCRAG System...")
    
    docs_path = os.path.join(os.path.dirname(__file__), "docs")
    print(f"DEBUG: Initializing KnowledgeBase with docs_dir: {docs_path}")
    
    # Initialize Knowledge Base
    # Assumes docs directory is relative to where main.py is executed,
    # which will be `dycrag_system` when run via `run.sh`.
    kb = KnowledgeBase(docs_dir=docs_path)
    
    print(f"DEBUG: KnowledgeBase initialized. Documents loaded: {len(kb.documents)}")
    
    if not kb.documents:
        print("Error: No documents loaded into the knowledge base. Please check 'dycrag_system/docs/'.")
        return

    # Initialize DyCRAG Agent
    agent = DyCRAGAgent(knowledge_base=kb)

    # Define the initial goal for the agent
    initial_agent_goal = "Explain the relationship between Agentic AI, RAG, and the newstate concept in AI systems."

    print("") # Just a newline
    print("--- Starting DyCRAG Agent's iterative process ---")
    agent.iterate(initial_agent_goal, max_iterations=4) # Run for a few iterations to show dynamism
    print("") # Just a newline
    print("DyCRAG Demonstration Finished.")

if __name__ == "__main__":
    run_dycrag_demonstration()
EOF
