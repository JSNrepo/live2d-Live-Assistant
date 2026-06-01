"""Memory graph persistence for personal facts."""

import json
import logging
import threading
from pathlib import Path

from config import PROJECT_ROOT

log = logging.getLogger("sakura")

# C3: Maximum number of memory edges to prevent unbounded growth
MAX_EDGES = 500


class MemoryGraph:
    def __init__(self, filepath=None):
        if filepath is None:
            filepath = PROJECT_ROOT / "memory_graph.json"
        self.filepath = Path(filepath)
        self.data = {"nodes": {}, "edges": []}
        self.lock = threading.Lock()
        self.load()

    def load(self):
        with self.lock:
            if self.filepath.exists():
                try:
                    with open(self.filepath, "r") as f:
                        self.data = json.load(f)
                except Exception:
                    self.data = {"nodes": {}, "edges": []}
            else:
                self.data = {"nodes": {}, "edges": []}

    def save(self):
        with self.lock:
            try:
                # E06: Write to a temporary file first, then atomically rename/replace it to prevent corruption on crash
                temp_filepath = self.filepath.with_suffix(".tmp")
                with open(temp_filepath, "w") as f:
                    json.dump(self.data, f, indent=2)
                # Atomic rename
                temp_filepath.replace(self.filepath)
            except Exception as e:
                log.error("Failed to save memory graph: %s", e)

    def add_relationship(self, source: str, relation: str, target: str) -> dict:
        s = source.strip().lower()
        r = relation.strip().lower()
        t = target.strip().lower()
        with self.lock:
            # Check for duplicate
            for edge in self.data["edges"]:
                if edge["source"] == s and edge["relation"] == r and edge["target"] == t:
                    return {"result": f"Fact already remembered: {s} {r} {t}"}
            self.data["edges"].append({"source": s, "relation": r, "target": t})
            # C3: Enforce maximum edge limit — evict oldest facts (FIFO)
            if len(self.data["edges"]) > MAX_EDGES:
                evicted = len(self.data["edges"]) - MAX_EDGES
                self.data["edges"] = self.data["edges"][-MAX_EDGES:]
                log.info("Memory graph pruned: evicted %d oldest edges (cap=%d)", evicted, MAX_EDGES)
        self.save()
        return {"result": f"Successfully remembered: {s} {r} {t}"}

    def remove_relationship(self, source: str, relation: str, target: str) -> dict:
        s = source.strip().lower()
        r = relation.strip().lower()
        t = target.strip().lower()
        with self.lock:
            edges = self.data["edges"]
            new_edges = [e for e in edges if not (e["source"] == s and e["relation"] == r and e["target"] == t)]
            removed = len(edges) - len(new_edges)
            self.data["edges"] = new_edges
        self.save()
        if removed > 0:
            return {"result": f"Successfully forgot: {s} {r} {t}"}
        return {"result": f"Fact not found in memory: {s} {r} {t}"}

    def get_relationship_graph(self, entity: str) -> dict:
        ent = entity.strip().lower()
        facts = []
        visited = set()

        def get_relations_dfs(current_ent, depth):
            if depth > 2 or current_ent in visited:
                return
            visited.add(current_ent)

            with self.lock:
                edges = list(self.data["edges"])

            for edge in edges:
                s, r, t = edge["source"], edge["relation"], edge["target"]
                if s == current_ent or t == current_ent:
                    fact = f"{s} {r} {t}"
                    if fact not in facts:
                        facts.append(fact)
                    neighbor = t if s == current_ent else s
                    get_relations_dfs(neighbor, depth + 1)

        get_relations_dfs(ent, 1)
        return {"entity": entity, "connected_facts": facts}


memory_db = MemoryGraph()


def remember_relationship(source: str, relation: str, target: str) -> dict:
    return memory_db.add_relationship(source, relation, target)


def forget_relationship(source: str, relation: str, target: str) -> dict:
    return memory_db.remove_relationship(source, relation, target)


def get_relationship_graph(entity: str) -> dict:
    return memory_db.get_relationship_graph(entity)
