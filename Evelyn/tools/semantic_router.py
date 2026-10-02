# semantic_router.py
# date created: 2026-10-02 17:20:00
# date modified: 2026-10-02 17:26:33
# tags: #semantic-router, #procedures, #intent, #embeddings, #rag

"""Vector-based Semantic Intent Router for operational procedures.

Inspired by Aurelio AI's semantic-router standard. Leverages the engine's canonical
BAAI/bge-large-en-v1.5 embedding model to compute dense cosine similarities against
representative user utterance exemplars and route centroids in <15ms.

Eliminates keyword-matching brittleness across natural spoken vernacular without
incurring LLM inference latency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
from typing import Any

import numpy as np

import evelyn_config as cfg

logger = logging.getLogger("evelyn.semantic_router")

DEFAULT_ROUTES_PATH = os.path.join(cfg.BASE_DIR, "reference", "procedure_routes.json")
DEFAULT_CACHE_PATH = os.path.join(cfg.BASE_DIR, "data", "semantic_routes_cache.npz")


class SemanticProcedureRouter:
    """Vector-based semantic intent router matching user queries against procedure routes."""

    def __init__(
        self,
        routes_path: str = DEFAULT_ROUTES_PATH,
        cache_path: str = DEFAULT_CACHE_PATH,
        default_threshold: float = 0.58,
    ) -> None:
        self.routes_path = routes_path
        self.cache_path = cache_path
        self.default_threshold = default_threshold
        self.routes: list[dict[str, Any]] = []
        self._centroids: np.ndarray | None = None
        self._exemplars: np.ndarray | None = None
        self._exemplar_route_indices: np.ndarray | None = None
        self._initialized: bool = False
        self._lock = threading.Lock()

    def _compute_routes_hash(self) -> str:
        """Compute SHA-256 hash of the routes definition file."""
        if not os.path.exists(self.routes_path):
            return ""
        hasher = hashlib.sha256()
        with open(self.routes_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def load_routes(self) -> list[dict[str, Any]]:
        """Load route definitions from JSON configuration."""
        if not os.path.exists(self.routes_path):
            logger.warning("Routes configuration not found at %s", self.routes_path)
            return []
        try:
            with open(self.routes_path, encoding="utf-8") as f:
                data = json.load(f)
            self.default_threshold = data.get("default_score_threshold", self.default_threshold)
            self.routes = data.get("routes", [])
            return self.routes
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed to load routes from %s: %e", self.routes_path, e)
            return []

    def ensure_index(self) -> bool:
        """Ensure route centroids and exemplar vectors are loaded and ready.

        Uses cached .npz embeddings if valid; computes and caches embeddings otherwise.
        """
        if self._initialized and self._centroids is not None:
            return True

        with self._lock:
            if self._initialized and self._centroids is not None:
                return True

            if not self.routes:
                self.load_routes()
            if not self.routes:
                return False

            file_hash = self._compute_routes_hash()

            # 1. Try loading valid cached embeddings
            if os.path.exists(self.cache_path):
                try:
                    cached = np.load(self.cache_path, allow_pickle=True)
                    cached_hash = str(cached.get("routes_hash", ""))
                    if cached_hash == file_hash:
                        self._centroids = cached["centroids"].astype(np.float32)
                        self._exemplars = cached["exemplars"].astype(np.float32)
                        self._exemplar_route_indices = cached["route_indices"].astype(np.int32)
                        self._initialized = True
                        ex_count = len(self._exemplars) if self._exemplars is not None else 0
                        logger.info(
                            "Loaded %d semantic routes (%d exemplars) from cache.",
                            len(self.routes),
                            ex_count,
                        )
                        return True
                except (OSError, ValueError, KeyError) as e:
                    logger.warning("Corrupted or stale routes cache at %s: %s", self.cache_path, e)

            # 2. Compute embeddings using the canonical embedding function
            return self._build_and_cache_index(file_hash)

    def _build_and_cache_index(self, file_hash: str) -> bool:
        """Encode all route utterances and compute normalized centroids."""
        try:
            from Evelyn.tools.chroma_rag import _get_embedding_fn

            embedding_fn = _get_embedding_fn()
        except (ImportError, AttributeError, RuntimeError) as e:
            logger.error("Cannot load embedding function for semantic router: %s", e)
            return False

        all_utterances: list[str] = []
        route_indices: list[int] = []

        for r_idx, route in enumerate(self.routes):
            utterances = route.get("utterances", [])
            for utt in utterances:
                cleaned = utt.strip()
                if cleaned:
                    all_utterances.append(cleaned)
                    route_indices.append(r_idx)

        if not all_utterances:
            return False

        try:
            raw_embs = embedding_fn(all_utterances)
            exemplars = np.array(raw_embs, dtype=np.float32)

            # Normalize exemplars to unit sphere (L2 norm)
            norms = np.linalg.norm(exemplars, axis=1, keepdims=True)
            norms[norms == 0.0] = 1.0
            exemplars = exemplars / norms

            # Compute normalized centroid for each route
            num_routes = len(self.routes)
            dim = exemplars.shape[1]
            centroids = np.zeros((num_routes, dim), dtype=np.float32)

            route_indices_arr = np.array(route_indices, dtype=np.int32)
            for r_idx in range(num_routes):
                mask = route_indices_arr == r_idx
                if np.any(mask):
                    cluster_mean = np.mean(exemplars[mask], axis=0)
                    c_norm = np.linalg.norm(cluster_mean)
                    if c_norm > 0.0:
                        cluster_mean = cluster_mean / c_norm
                    centroids[r_idx] = cluster_mean

            self._centroids = centroids
            self._exemplars = exemplars
            self._exemplar_route_indices = route_indices_arr
            self._initialized = True

            # Persist to cache
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            np.savez_compressed(
                self.cache_path,
                routes_hash=file_hash,
                centroids=self._centroids,
                exemplars=self._exemplars,
                route_indices=self._exemplar_route_indices,
            )
            logger.info(
                "Indexed and cached %d semantic routes with %d exemplars.",
                num_routes,
                len(all_utterances),
            )
            return True
        except (OSError, ValueError, RuntimeError, TypeError) as e:
            logger.error("Failed to compute semantic route embeddings: %s", e)
            return False

    def route_query(
        self,
        query: str,
        top_k: int = 3,
        threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Match an incoming user query against route centroids and exemplars.

        Args:
            query: The user input text.
            top_k: Maximum number of routes to return.
            threshold: Optional score threshold override.

        Returns:
            list[dict]: Ranked matched routes with 'semantic_score', 'procedure_id',
                        'suggested_tools', and 'route_name'.
        """
        clean_query = query.strip()
        if not clean_query or not self.ensure_index():
            return []

        try:
            from Evelyn.tools.chroma_rag import _get_embedding_fn

            embedding_fn = _get_embedding_fn()
            q_emb = embedding_fn([clean_query])
            q_vec = np.array(q_emb[0], dtype=np.float32)
            q_norm = np.linalg.norm(q_vec)
            if q_norm == 0.0:
                return []
            q_vec = q_vec / q_norm
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as e:
            logger.warning("Failed to embed query for semantic routing: %s", e)
            return []

        assert self._centroids is not None
        assert self._exemplars is not None
        assert self._exemplar_route_indices is not None

        # Dot product against centroids and exemplars
        c_sims = np.dot(self._centroids, q_vec)
        e_sims = np.dot(self._exemplars, q_vec)

        matches: list[dict[str, Any]] = []
        for r_idx, route in enumerate(self.routes):
            route_thresh = (
                threshold if threshold is not None else route.get("score_threshold", self.default_threshold)
            )
            c_score = float(c_sims[r_idx])
            mask = self._exemplar_route_indices == r_idx
            e_score = float(np.max(e_sims[mask])) if np.any(mask) else 0.0

            # Standard semantic-router nearest-exemplar similarity scoring
            score = e_score

            if score >= route_thresh:
                matches.append({
                    "route_name": route.get("route_name", ""),
                    "procedure_id": route.get("procedure_id"),
                    "suggested_tools": route.get("suggested_tools", []),
                    "semantic_score": round(score, 4),
                    "centroid_score": round(c_score, 4),
                    "exemplar_score": round(e_score, 4),
                })

        matches.sort(key=lambda x: x["semantic_score"], reverse=True)
        return matches[:top_k]

    def match_procedures(
        self,
        query: str,
        top_k: int = 3,
        threshold: float | None = None,
        status: str = "live",
    ) -> list[dict[str, Any]]:
        """Retrieve full procedure dictionaries from SQLite for matching semantic routes.

        Args:
            query: The user message query.
            top_k: Maximum procedures to return.
            threshold: Semantic score threshold.
            status: Expected procedure status ('live').

        Returns:
            list[dict]: Hydrated procedure records enriched with 'semantic_score'.
        """
        route_matches = self.route_query(query, top_k=top_k, threshold=threshold)
        if not route_matches:
            return []

        matched_ids = [m["procedure_id"] for m in route_matches if m.get("procedure_id") is not None]
        if not matched_ids:
            return []

        scores_by_id = {m["procedure_id"]: m["semantic_score"] for m in route_matches}

        try:
            from Evelyn.tools import memory_db

            con = memory_db.get_db()
            placeholders = ",".join("?" for _ in matched_ids)
            query_sql = f"SELECT * FROM procedures WHERE id IN ({placeholders}) AND status = ?"
            params = [*matched_ids, status]
            rows = con.execute(query_sql, params).fetchall()
            con.close()

            hydrated: list[dict[str, Any]] = []
            for r in rows:
                p_dict = dict(r)
                p_id = p_dict.get("id")
                p_dict["semantic_score"] = scores_by_id.get(p_id, 0.0)
                hydrated.append(p_dict)

            hydrated.sort(key=lambda x: x.get("semantic_score", 0.0), reverse=True)
            return hydrated
        except (sqlite3.Error, OSError, ImportError) as e:
            logger.warning("Database hydration failed during semantic procedure matching: %s", e)
            return []

    def get_suggested_tools(self, query: str, threshold: float | None = None) -> list[str]:
        """Return unique suggested tools derived from semantically matched routes."""
        matches = self.route_query(query, top_k=5, threshold=threshold)
        tools: list[str] = []
        for m in matches:
            for t in m.get("suggested_tools", []):
                if t and t not in tools:
                    tools.append(t)
        return tools


_ROUTER_SINGLETON: SemanticProcedureRouter | None = None
_ROUTER_LOCK = threading.Lock()


def get_semantic_router() -> SemanticProcedureRouter:
    """Return the global cached SemanticProcedureRouter singleton instance."""
    global _ROUTER_SINGLETON
    if _ROUTER_SINGLETON is None:
        with _ROUTER_LOCK:
            if _ROUTER_SINGLETON is None:
                _ROUTER_SINGLETON = SemanticProcedureRouter()
    return _ROUTER_SINGLETON
