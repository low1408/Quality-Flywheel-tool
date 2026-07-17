"""Feature extraction, clustering, and cross-run cluster comparison."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Callable

import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.feature_extraction.text import TfidfVectorizer

from .models import EmbeddedFailure, FailureCluster, FailureInstance
from .taxonomy import FAILURE_DESCRIPTIONS, FailureCategory

# -----------------------------------------------------------------------------
# Failure Clustering Engine
# -----------------------------------------------------------------------------

HUMAN_CAUSE_LABELS: dict[str, str] = {
    "prompt_underspecification": "Prompt Underspecification / Ambiguous Requirements",
    "tool_execution_error": "Tool Execution / Selection Error",
    "verification_failure": "Verification / Validation Failure",
    "implementation_defect": "Implementation / Code Defect",
    "dependency_failure": "Environment / Dependency Setup Failure",
}


def normalize_root_cause(text: str) -> str:
    """Normalize free-text root causes into standard taxonomy codes."""
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r'\s+', ' ', text)
    
    mappings = {
        "vague prompt": "prompt_underspecification",
        "underspecified requirements": "prompt_underspecification",
        "missing acceptance criteria": "prompt_underspecification",
        "ambiguous task": "prompt_underspecification",
        "ambiguous prompt": "prompt_underspecification",
        "missing requirements": "prompt_underspecification",
        "requirements were underspecified": "prompt_underspecification",
        "prompt issue": "prompt_underspecification",
        
        "tool selection error": "tool_execution_error",
        "tool selection": "tool_execution_error",
        "incorrect tool arguments": "tool_execution_error",
        "wrong tool selected": "tool_execution_error",
        "tool arguments": "tool_execution_error",
        "tool execution": "tool_execution_error",
        "tool error": "tool_execution_error",
        "tool issue": "tool_execution_error",
        
        "missing validation": "verification_failure",
        "skipping validation": "verification_failure",
        "no validation": "verification_failure",
        "premature stop": "verification_failure",
        "premature completion": "verification_failure",
        "verification": "verification_failure",
        
        "implementation defect": "implementation_defect",
        "code logic error": "implementation_defect",
        "code syntax error": "implementation_defect",
        "syntax error": "implementation_defect",
        "logic error": "implementation_defect",
        "model limitation": "implementation_defect",
        
        "dependency failure": "dependency_failure",
        "missing dependency": "dependency_failure",
        "environment failure": "dependency_failure",
        "setup failure": "dependency_failure",
    }
    
    for key, val in mappings.items():
        if key in text:
            return val
            
    text = re.sub(r'[^a-z0-9\s_]', '', text)
    text = re.sub(r'\s+', '_', text.strip())
    return text


def normalize_error_message(text: str) -> str:
    """Strip volatile paths, numbers, hex addresses, and hashes from error messages."""
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r'/[^:\s]+/([^:\s]+)', r'\1', text)
    text = re.sub(r'\bline \d+\b', 'line', text)
    text = re.sub(r'\b0x[a-f0-9]+\b', 'hex_addr', text)
    text = re.sub(r'\b[a-f0-9]{32,}\b', 'hash_val', text)
    text = re.sub(r'\s+', ' ', text)
    return text


class FailureClusteringEngine:
    """
    Clusters failures by semantic similarity using embeddings.

    Inspired by Composo.ai's approach:
    1. Generate diagnostic descriptions (via LLM judge)
    2. Embed descriptions with task prefix for cleaner clusters
    3. Cluster in higher-dimensional space, visualize in 2D
    4. Match clusters across time by membership (Jaccard on trace IDs)
    """

    def __init__(
        self,
        embedding_fn: Callable[[list[str]], list[list[float]]] | None = None,
        min_cluster_size: int = 2,
        eps: float = 0.3,
    ):
        """
        Args:
            embedding_fn: Function that takes a list of texts and returns embeddings.
                         If None, uses TF-IDF as a fallback.
            min_cluster_size: Minimum failures to form a cluster
            eps: DBSCAN epsilon parameter
        """
        self.embedding_fn = embedding_fn or self._tfidf_embed
        self.min_cluster_size = min_cluster_size
        self.eps = eps
        self.clusters: list[FailureCluster] = []
        self._embedded_failures: list[EmbeddedFailure] = []
        self._failure_ids: set[str] = set()
        self._embeddings: np.ndarray | None = None
        self.algorithm_used: str = "dbscan"

    def add_failures(self, failures: list[FailureInstance]) -> None:
        """Add failures to the clustering engine."""
        for f in failures:
            if f.failure_id in self._failure_ids:
                continue
            self._embedded_failures.append(self._embed_failure(f))
            self._failure_ids.add(f.failure_id)

    def cluster(self, failures: list[FailureInstance] | None = None) -> list[FailureCluster]:
        """
        Cluster failures by semantic similarity.

        Args:
            failures: Optional explicit failure collection. When supplied, the
                clustering run uses exactly this collection instead of mutable
                accumulated state.

        Returns list of FailureCluster objects.
        """
        embedded_failures = (
            [self._embed_failure(f) for f in failures]
            if failures is not None
            else self._embedded_failures
        )
        if failures is not None:
            self._embedded_failures = embedded_failures
            self._failure_ids = {item.failure.failure_id for item in embedded_failures}

        if len(embedded_failures) < self.min_cluster_size:
            self.clusters = []
            self._embeddings = None
            self.algorithm_used = "dbscan"
            return []

        embedding_texts = [item.embedding_text for item in embedded_failures]

        # Generate embeddings
        try:
            embeddings = self.embedding_fn(embedding_texts)
        except Exception as e:
            raise RuntimeError(f"Clustering feature extraction failed: {e}") from e
        self._embeddings = np.array(embeddings)
        for embedded_failure, embedding in zip(embedded_failures, embeddings):
            embedded_failure.failure.embedding = list(embedding)

        # DBSCAN with cosine distance is default for all sizes to eliminate discontinuity
        clusterer = DBSCAN(eps=self.eps, min_samples=self.min_cluster_size, metric="cosine")
        labels = clusterer.fit_predict(self._embeddings)
        self.algorithm_used = "dbscan"

        # Group failures by cluster
        cluster_groups: dict[int, list[tuple[int, FailureInstance]]] = defaultdict(list)
        for i, (label, embedded_failure) in enumerate(zip(labels, embedded_failures)):
            if label >= 0:  # -1 is noise
                cluster_groups[int(label)].append((i, embedded_failure.failure))

        # Build FailureCluster objects
        self.clusters = []
        for cluster_id, items in cluster_groups.items():
            indices, failures_list = zip(*items)
            cluster = self._build_cluster(cluster_id, list(failures_list), list(indices))
            cluster.assignment_type = self.algorithm_used
            self.clusters.append(cluster)

        # Calculate centroids and confidence for each cluster
        for cluster in self.clusters:
            embeddings_list = [f.embedding for f in cluster.failures if f.embedding]
            if not embeddings_list:
                for f in cluster.failures:
                    f.cluster_confidence = 1.0
                continue
            
            centroid = np.mean(np.array(embeddings_list), axis=0)
            centroid_norm = np.linalg.norm(centroid)
            if centroid_norm > 0:
                centroid = centroid / centroid_norm

            for f in cluster.failures:
                if f.embedding:
                    f_arr = np.array(f.embedding)
                    f_norm = np.linalg.norm(f_arr)
                    if f_norm > 0:
                        f_arr = f_arr / f_norm
                    sim = float(np.dot(f_arr, centroid))
                    f.cluster_confidence = max(0.0, min(1.0, sim))
                else:
                    f.cluster_confidence = 1.0

        return self.clusters

    def _get_all_failures(self) -> list[FailureInstance]:
        """Get all failure instances that were added."""
        return [item.failure for item in self._embedded_failures]

    def _embed_failure(self, failure: FailureInstance) -> EmbeddedFailure:
        """Create the embedding text and keep it attached to the source failure."""
        norm_cause = normalize_root_cause(failure.probable_cause)
        norm_desc = failure.description.lower().strip()
        norm_error = normalize_error_message(failure.error_message)

        causal_parts = []
        if failure.category:
            causal_parts.append(f"category {failure.category}")
        if failure.subcategory:
            causal_parts.append(f"subcategory {failure.subcategory}")
        if norm_cause:
            causal_parts.append(f"cause {norm_cause}")
        if failure.affected_prompt_component:
            causal_parts.append(f"prompt {failure.affected_prompt_component}")

        causal_text = " ".join(causal_parts)
        surface_text = f"desc {norm_desc} error {norm_error}"
        text = f"{causal_text} | {surface_text}"

        return EmbeddedFailure(
            failure=failure,
            embedding_text=text,
            causal_text=causal_text,
            surface_text=surface_text,
        )

    def _build_cluster(self, cluster_id: int, failures: list[FailureInstance], indices: list[int]) -> FailureCluster:
        """Build a FailureCluster from grouped failures."""
        # Determine dominant category
        category_counts = Counter(f.category for f in failures if f.category)
        subcategory_counts = Counter(f.subcategory for f in failures if f.subcategory)

        dominant_category = category_counts.most_common(1)[0][0] if category_counts else None
        dominant_subcategory = subcategory_counts.most_common(1)[0][0] if subcategory_counts else None

        # Determine dominant cause
        cause_counts = Counter(normalize_root_cause(f.probable_cause) for f in failures if f.probable_cause)
        dominant_cause = cause_counts.most_common(1)[0][0] if cause_counts else None

        # Extract common keywords from descriptions
        all_descriptions = " ".join(f.description for f in failures)
        keywords = self._extract_keywords(all_descriptions)

        # Extract common keywords from probable causes (causal keywords)
        all_causes = " ".join(f.probable_cause for f in failures if f.probable_cause)
        causal_keywords = self._extract_keywords(all_causes) if all_causes else []

        # Extract common tool calls
        tool_calls = []
        for f in failures:
            if f.trace_snippet:
                tools = re.findall(r'Tool:\s*(\w+)', f.trace_snippet)
                tool_calls.extend(tools)
        common_tools = [tool for tool, count in Counter(tool_calls).most_common(5) if count > 1]

        # Calculate average severity
        severity_scores = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        avg_sev_score = sum(severity_scores.get(f.severity, 2) for f in failures) / len(failures)
        severity_map = {1: "low", 2: "medium", 3: "high", 4: "critical"}
        avg_severity = severity_map.get(round(avg_sev_score), "medium")

        # Generate cluster label using cause-first approach
        label = self._generate_cluster_label(
            dominant_cause, dominant_subcategory, causal_keywords, keywords, len(failures)
        )

        # Generate suggestions
        prompt_fix = self._generate_prompt_fix(dominant_subcategory, failures)
        tool_fix = self._generate_tool_fix(dominant_subcategory, failures)

        return FailureCluster(
            cluster_id=cluster_id,
            label=label,
            description=f"Cluster of {len(failures)} failures: {dominant_subcategory or 'mixed'}",
            failures=failures,
            dominant_category=dominant_category,
            dominant_subcategory=dominant_subcategory,
            affected_agents=set(f.agent_name for f in failures),
            affected_models=set(f.model_id for f in failures if f.model_id),
            common_keywords=keywords[:10],
            common_tool_calls=common_tools,
            avg_severity=avg_severity,
            suggested_prompt_fix=prompt_fix,
            suggested_tool_fix=tool_fix,
        )

    def _generate_cluster_label(
        self,
        dominant_cause: str | None,
        subcategory: str | None,
        causal_keywords: list[str],
        desc_keywords: list[str],
        count: int
    ) -> str:
        """Generate a human-readable label for the cluster."""
        if dominant_cause and dominant_cause in HUMAN_CAUSE_LABELS:
            base = HUMAN_CAUSE_LABELS[dominant_cause]
        elif subcategory and subcategory in FAILURE_DESCRIPTIONS:
            base = FAILURE_DESCRIPTIONS[subcategory]
        elif causal_keywords:
            base = f"{causal_keywords[0].title()} Causal Issues"
        elif desc_keywords:
            base = f"{desc_keywords[0].title()} Issues"
        else:
            base = "Unknown Failure Pattern"

        return f"{base} (n={count})"

    def _generate_prompt_fix(self, subcategory: str | None, failures: list[FailureInstance]) -> str:
        """Generate a prompt fix suggestion based on the failure pattern."""
        prompt_fixes = {
            FailureCategory.SUBCATEGORY_DISOBEY_SPEC: "Add explicit step-by-step instructions and constraint checklist to system prompt",
            FailureCategory.SUBCATEGORY_WRONG_TOOL: "Improve tool descriptions with usage examples and clarify when to use each tool",
            FailureCategory.SUBCATEGORY_WRONG_ARGS: "Add JSON schema validation and examples of correct tool arguments",
            FailureCategory.SUBCATEGORY_PREMATURE_STOP: "Add explicit completion criteria and require verification before stopping",
            FailureCategory.SUBCATEGORY_NO_VALIDATION: "Require agent to run tests and verify output before completing",
            FailureCategory.CODE_SYNTAX: "Add syntax validation step and require compilation before submission",
            FailureCategory.LLM_HALLUCINATION: "Add grounding requirements - agent must cite specific APIs and verify existence",
        }
        return prompt_fixes.get(subcategory or "", "Review system prompt for clarity and completeness")

    def _generate_tool_fix(self, subcategory: str | None, failures: list[FailureInstance]) -> str:
        """Generate a tool fix suggestion."""
        tool_fixes = {
            FailureCategory.SUBCATEGORY_WRONG_TOOL: "Review and enhance tool descriptions with clearer use cases",
            FailureCategory.SUBCATEGORY_TOOL_NOT_FOUND: "Ensure all referenced tools are properly registered and available",
            FailureCategory.SUBCATEGORY_REPEATED_TOOL_ERRORS: "Add tool error handling and fallback mechanisms",
        }
        return tool_fixes.get(subcategory or "", "No specific tool changes needed")

    def _extract_keywords(self, text: str, top_n: int = 15) -> list[str]:
        """Extract important keywords from failure descriptions."""
        vectorizer = TfidfVectorizer(
            max_features=100,
            stop_words="english",
            ngram_range=(1, 2),
        )
        try:
            tfidf = vectorizer.fit_transform([text])
            feature_names = vectorizer.get_feature_names_out()
            scores = tfidf.toarray()[0]
            top_indices = scores.argsort()[-top_n:][::-1]
            return [feature_names[i] for i in top_indices if scores[i] > 0]
        except Exception:
            return []

    def _tfidf_embed(self, texts: list[str]) -> list[list[float]]:
        """Generate TF-IDF embeddings as fallback, using weighted causal & surface features."""
        if (
            getattr(self, "_embedded_failures", None)
            and len(self._embedded_failures) == len(texts)
        ):
            causal_texts = [item.causal_text for item in self._embedded_failures]
            surface_texts = [item.surface_text for item in self._embedded_failures]
        else:
            causal_texts = []
            surface_texts = []
            for t in texts:
                parts = t.split(" | ", 1)
                if len(parts) == 2:
                    causal_texts.append(parts[0])
                    surface_texts.append(parts[1])
                else:
                    causal_texts.append(t)
                    surface_texts.append("")

        causal_vectorizer = TfidfVectorizer(max_features=128, stop_words="english")
        surface_vectorizer = TfidfVectorizer(max_features=128, stop_words="english")

        try:
            try:
                causal_matrix = causal_vectorizer.fit_transform(causal_texts).toarray()
            except ValueError:
                causal_matrix = np.zeros((len(causal_texts), 128))

            try:
                surface_matrix = surface_vectorizer.fit_transform(surface_texts).toarray()
            except ValueError:
                surface_matrix = np.zeros((len(surface_texts), 128))

            # L2 normalize rows
            causal_norms = np.linalg.norm(causal_matrix, axis=1, keepdims=True)
            causal_matrix = np.divide(causal_matrix, causal_norms, out=np.zeros_like(causal_matrix), where=causal_norms > 0)

            surface_norms = np.linalg.norm(surface_matrix, axis=1, keepdims=True)
            surface_matrix = np.divide(surface_matrix, surface_norms, out=np.zeros_like(surface_matrix), where=surface_norms > 0)

            combined = np.hstack([0.8 * causal_matrix, 0.2 * surface_matrix])
            return combined.tolist()
        except Exception as e:
            raise RuntimeError(f"Clustering feature extraction failed: {e}") from e

    def compare_with_previous(
        self,
        previous_clusters: list[FailureCluster],
        trace_id_field: str = "trace_id",
    ) -> dict[str, Any]:
        """
        Compare current clusters with previous run to detect drift.

        Uses Jaccard similarity on trace IDs for robust matching.
        """
        matches = []
        new_clusters = []
        resolved_clusters = []

        current_trace_sets = {c.cluster_id: self._cluster_trace_ids(c) for c in self.clusters}
        previous_trace_sets = {c.cluster_id: self._cluster_trace_ids(c) for c in previous_clusters}

        # Find matches
        for curr_id, curr_traces in current_trace_sets.items():
            best_match = None
            best_jaccard = 0.0

            for prev_id, prev_traces in previous_trace_sets.items():
                intersection = len(curr_traces & prev_traces)
                union = len(curr_traces | prev_traces)
                jaccard = intersection / union if union > 0 else 0

                if jaccard > best_jaccard and jaccard > 0.3:  # Threshold for "same cluster"
                    best_jaccard = jaccard
                    best_match = prev_id

            if best_match is not None:
                curr_cluster = next(c for c in self.clusters if c.cluster_id == curr_id)
                prev_cluster = next(c for c in previous_clusters if c.cluster_id == best_match)
                matches.append({
                    "current_cluster_id": curr_id,
                    "previous_cluster_id": best_match,
                    "jaccard": best_jaccard,
                    "previous_count": len(prev_cluster.failures),
                    "current_count": len(curr_cluster.failures),
                    "trend": "growing" if len(curr_cluster.failures) > len(prev_cluster.failures) else "shrinking",
                })
            else:
                new_clusters.append(curr_id)

        # Find resolved clusters (in previous but not in current)
        matched_prev_ids = set(m["previous_cluster_id"] for m in matches)
        for prev_id in previous_trace_sets:
            if prev_id not in matched_prev_ids:
                resolved_clusters.append(prev_id)

        return {
            "matched_clusters": matches,
            "new_clusters": new_clusters,
            "resolved_clusters": resolved_clusters,
            "total_current": len(self.clusters),
            "total_previous": len(previous_clusters),
        }

    def _cluster_trace_ids(self, cluster: FailureCluster) -> set[str]:
        """Return only real trace IDs; missing IDs cannot establish cluster continuity."""
        return {f.trace_id for f in cluster.failures if f.trace_id is not None}
