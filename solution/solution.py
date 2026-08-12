"""
Day 14 — AI Evaluation & Benchmarking Pipeline
AICB-P1: AI Practical Competency Program, Phase 1
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Task 1 — Data Models (Golden Dataset + Evaluation Results)
# ---------------------------------------------------------------------------

@dataclass
class QAPair:
    question: str
    expected_answer: str
    context: str | None = ""
    metadata: dict | None = field(default_factory=dict)
    retrieved_contexts: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    qa_pair: QAPair
    actual_answer: str
    faithfulness: float
    relevance: float
    completeness: float
    passed: bool
    failure_type: str | None = None
    context_precision: float | None = None
    context_recall: float | None = None

    def overall_score(self) -> float:
        """Compute the average of faithfulness, relevance, and completeness."""
        return (self.faithfulness + self.relevance + self.completeness) / 3.0


# ---------------------------------------------------------------------------
# Task 2 — RAGAS Evaluator (Simplified word-overlap heuristic)
# ---------------------------------------------------------------------------

STOPWORDS: set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "with", "as", "by", "and", "or",
    "it", "its", "this", "that", "these", "those", "from", "into", "than",
}


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokenization, ignoring punctuation and stopwords."""
    if not text:
        return set()
    tokens = re.findall(r"\b\w+\b", text.lower())
    return {t for t in tokens if t not in STOPWORDS}


class RAGASEvaluator:
    def evaluate_faithfulness(self, answer: str, context: str) -> float:
        if not answer: return 1.0
        answer_tokens = _tokenize(answer)
        if not answer_tokens: return 1.0
        context_tokens = _tokenize(context)
        score = len(answer_tokens & context_tokens) / len(answer_tokens)
        return min(max(score, 0.0), 1.0)

    def evaluate_relevance(self, answer: str, question: str) -> float:
        if not question: return 1.0
        question_tokens = _tokenize(question)
        if not question_tokens: return 1.0
        answer_tokens = _tokenize(answer)
        score = len(answer_tokens & question_tokens) / len(question_tokens)
        return min(max(score, 0.0), 1.0)

    def evaluate_completeness(self, answer: str, expected: str) -> float:
        if not expected: return 1.0
        expected_tokens = _tokenize(expected)
        if not expected_tokens: return 1.0
        answer_tokens = _tokenize(answer)
        score = len(answer_tokens & expected_tokens) / len(expected_tokens)
        return min(max(score, 0.0), 1.0)

    def evaluate_context_recall(self, contexts: list[str], expected: str) -> float:
        if not expected: return 1.0
        expected_tokens = _tokenize(expected)
        if not expected_tokens: return 1.0
        union_tokens = set()
        for chunk in contexts:
            union_tokens.update(_tokenize(chunk))
        score = len(expected_tokens & union_tokens) / len(expected_tokens)
        return min(max(score, 0.0), 1.0)

    def evaluate_context_precision(
        self,
        contexts: list[str],
        expected: str,
        relevance_threshold: float = 0.1,
    ) -> float:
        if not expected: return 1.0
        if not contexts: return 0.0
        expected_tokens = _tokenize(expected)
        if not expected_tokens: return 1.0

        relevant_chunks = []
        for chunk in contexts:
            chunk_tokens = _tokenize(chunk)
            overlap = len(chunk_tokens & expected_tokens) / len(expected_tokens)
            relevant_chunks.append(overlap >= relevance_threshold)

        num_relevant = sum(relevant_chunks)
        if num_relevant == 0: return 0.0

        ap_sum = 0.0
        for k, is_relevant in enumerate(relevant_chunks, start=1):
            if is_relevant:
                precision_at_k = sum(relevant_chunks[:k]) / k
                ap_sum += precision_at_k

        return min(max(ap_sum / num_relevant, 0.0), 1.0)

    def run_full_eval(
        self,
        answer: str,
        question: str,
        context: str,
        expected: str,
        contexts: list[str] | None = None,
    ) -> EvalResult:
        f = self.evaluate_faithfulness(answer, context)
        r = self.evaluate_relevance(answer, question)
        c = self.evaluate_completeness(answer, expected)
        
        passed = f >= 0.5 and r >= 0.5 and c >= 0.5
        
        failure_type = None
        if not passed:
            if f < 0.3: failure_type = "hallucination"
            elif r < 0.3: failure_type = "irrelevant"
            elif c < 0.3: failure_type = "incomplete"
            else: failure_type = "off_topic"
            
        qa = QAPair(
            question=question, 
            expected_answer=expected, 
            context=context, 
            retrieved_contexts=contexts or []
        )
        
        result = EvalResult(
            qa_pair=qa,
            actual_answer=answer,
            faithfulness=f,
            relevance=r,
            completeness=c,
            passed=passed,
            failure_type=failure_type
        )
        
        if contexts is not None:
            result.context_recall = self.evaluate_context_recall(contexts, expected)
            result.context_precision = self.evaluate_context_precision(contexts, expected)
            
        return result


# ---------------------------------------------------------------------------
# Reranking helper (Bonus — Exercise 3.5)
# ---------------------------------------------------------------------------

def rerank_by_overlap(contexts: list[str], query: str) -> list[str]:
    query_tokens = _tokenize(query)
    return sorted(contexts, key=lambda c: len(_tokenize(c) & query_tokens), reverse=True)


# ---------------------------------------------------------------------------
# Task 3 — LLM Judge
# ---------------------------------------------------------------------------

class LLMJudge:
    def __init__(self, judge_llm_fn: Callable[[str], str]) -> None:
        self.judge_llm_fn = judge_llm_fn

    def score_response(
        self,
        question: str,
        answer: str,
        rubric: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = (f"Question: {question}\nAnswer: {answer}\nRubric: {json.dumps(rubric)}\n"
                  f"Provide JSON with 'scores' mapping criteria to float 0-1, and 'reasoning' string.")
        try:
            response = self.judge_llm_fn(prompt)
            data = json.loads(response)
            if "scores" not in data or "reasoning" not in data:
                raise ValueError
            return data
        except Exception:
            return {
                "scores": {k: 0.5 for k in rubric.keys()},
                "reasoning": "Failed to parse LLM output."
            }

    def detect_bias(self, scores_batch: list[dict[str, Any]]) -> dict[str, Any]:
        positional = False
        leniency = False
        severity = False
        
        if scores_batch:
            all_scores = []
            for b in scores_batch:
                all_scores.extend(b["scores"].values())
            
            avg = sum(all_scores) / len(all_scores) if all_scores else 0.5
            if avg > 0.8: leniency = True
            if avg < 0.3: severity = True
            
            if len(scores_batch) > 1:
                first_avg = sum(scores_batch[0]["scores"].values()) / len(scores_batch[0]["scores"])
                rest_avg = sum(sum(b["scores"].values()) / len(b["scores"]) for b in scores_batch[1:]) / (len(scores_batch) - 1)
                if first_avg > rest_avg + 0.2:
                    positional = True

        return {
            "positional_bias": positional,
            "leniency_bias": leniency,
            "severity_bias": severity
        }


# ---------------------------------------------------------------------------
# Task 4 — Benchmark Runner
# ---------------------------------------------------------------------------

class BenchmarkRunner:
    def run(
        self,
        qa_pairs: list[QAPair],
        agent_fn: Callable[[str], str],
        evaluator: RAGASEvaluator,
    ) -> list[EvalResult]:
        results = []
        for pair in qa_pairs:
            answer = agent_fn(pair.question)
            res = evaluator.run_full_eval(
                answer=answer,
                question=pair.question,
                context=pair.context or "",
                expected=pair.expected_answer,
                contexts=pair.retrieved_contexts
            )
            res.qa_pair = pair
            results.append(res)
        return results

    def generate_report(self, results: list[EvalResult]) -> dict[str, Any]:
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        
        f_scores = [r.faithfulness for r in results]
        r_scores = [r.relevance for r in results]
        c_scores = [r.completeness for r in results]
        
        recall_scores = [r.context_recall for r in results if r.context_recall is not None]
        prec_scores = [r.context_precision for r in results if r.context_precision is not None]
        
        failures = {}
        for r in results:
            if not r.passed and r.failure_type:
                failures[r.failure_type] = failures.get(r.failure_type, 0) + 1
                
        return {
            "total": total,
            "passed": passed,
            "pass_rate": passed / total if total else 0.0,
            "avg_faithfulness": sum(f_scores) / len(f_scores) if f_scores else 0.0,
            "avg_relevance": sum(r_scores) / len(r_scores) if r_scores else 0.0,
            "avg_completeness": sum(c_scores) / len(c_scores) if c_scores else 0.0,
            "avg_context_recall": sum(recall_scores) / len(recall_scores) if recall_scores else None,
            "avg_context_precision": sum(prec_scores) / len(prec_scores) if prec_scores else None,
            "failure_types": failures
        }

    def run_regression(self, new_results: list, baseline_results: list) -> dict:
        new_f = sum(r.faithfulness for r in new_results) / len(new_results)
        new_r = sum(r.relevance for r in new_results) / len(new_results)
        new_c = sum(r.completeness for r in new_results) / len(new_results)
        
        base_f = sum(r.faithfulness for r in baseline_results) / len(baseline_results)
        base_r = sum(r.relevance for r in baseline_results) / len(baseline_results)
        base_c = sum(r.completeness for r in baseline_results) / len(baseline_results)
        
        regressions = []
        if base_f - new_f > 0.05: regressions.append("faithfulness")
        if base_r - new_r > 0.05: regressions.append("relevance")
        if base_c - new_c > 0.05: regressions.append("completeness")
        
        return {
            "new_avg_faithfulness": new_f,
            "new_avg_relevance": new_r,
            "new_avg_completeness": new_c,
            "baseline_avg_faithfulness": base_f,
            "baseline_avg_relevance": base_r,
            "baseline_avg_completeness": base_c,
            "regressions": regressions,
            "passed": len(regressions) == 0
        }

    def identify_failures(
        self,
        results: list[EvalResult],
        threshold: float = 0.5,
    ) -> list[EvalResult]:
        return [r for r in results if r.faithfulness < threshold or r.relevance < threshold or r.completeness < threshold]


# ---------------------------------------------------------------------------
# Task 5 — Failure Analyzer
# ---------------------------------------------------------------------------

class FailureAnalyzer:
    def categorize_failures(
        self, failures: list[EvalResult]
    ) -> dict[str, int]:
        counts = {}
        for f in failures:
            if f.failure_type:
                counts[f.failure_type] = counts.get(f.failure_type, 0) + 1
        return counts

    def find_root_cause(self, failure: EvalResult) -> str:
        scores = {
            "faithfulness": failure.faithfulness,
            "relevance": failure.relevance,
            "completeness": failure.completeness
        }
        lowest = min(scores, key=scores.get)
        if scores[lowest] >= 0.5:
            return "Multiple issues detected — review full pipeline"
        if lowest == "faithfulness":
            return "Context is missing or irrelevant — improve retrieval"
        elif lowest == "relevance":
            return "Answer does not address the question — improve prompt clarity"
        else:
            return "Answer is missing key information — increase context window or improve generation"

    def generate_improvement_log(self, failures: list, suggestions: list[str]) -> str:
        lines = [
            "| Failure ID | Type | Root Cause | Suggested Fix | Status |",
            "|------------|------|------------|---------------|--------|"
        ]
        for i, f in enumerate(failures):
            fid = f"F{i+1:03d}"
            ftype = f.failure_type or "Unknown"
            cause = self.find_root_cause(f)
            fix = suggestions[i] if i < len(suggestions) else "Investigate further"
            lines.append(f"| {fid} | {ftype} | {cause} | {fix} | Open |")
        return "\n".join(lines)

    def generate_improvement_suggestions(
        self, failures: list[EvalResult]
    ) -> list[str]:
        suggestions = []
        for f in failures:
            cause = self.find_root_cause(f)
            if "retrieval" in cause:
                suggestions.append("Improve retrieval by using better embeddings or reranking")
            elif "prompt" in cause:
                suggestions.append("Refine the prompt to be more specific and clear")
            else:
                suggestions.append("Increase chunk size or use few-shot examples")
        
        while len(suggestions) < 3:
            suggestions.append("General pipeline review and hyperparameter tuning")
            
        return suggestions


# ---------------------------------------------------------------------------
# Entry point for manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    qa_pairs = [
        QAPair(
            question="What is RAG?",
            expected_answer="RAG stands for Retrieval-Augmented Generation, which combines retrieval with text generation.",
            context="RAG is a technique that retrieves relevant documents and uses them to ground LLM generation.",
            metadata={"difficulty": "easy", "category": "definition"},
        ),
        QAPair(
            question="What is the capital of France?",
            expected_answer="Paris is the capital of France.",
            context="France is a country in Western Europe. Its capital city is Paris.",
            metadata={"difficulty": "easy", "category": "factual"},
        ),
        QAPair(
            question="Explain backpropagation and why it matters for training",
            expected_answer="Backpropagation is an algorithm for training neural networks by computing gradients efficiently, enabling deep learning models to learn from errors.",
            context="Neural networks learn through gradient descent. Backpropagation efficiently computes these gradients layer by layer.",
            metadata={"difficulty": "medium", "category": "explanation"},
        ),
        QAPair(
            question="Should I use RAG or fine-tuning for my chatbot?",
            expected_answer="It depends on the use case: RAG is better for frequently updated knowledge, fine-tuning for consistent style/behavior. Consider cost, latency, and data freshness.",
            context="RAG retrieves external documents at inference time. Fine-tuning modifies model weights during training.",
            metadata={"difficulty": "hard", "category": "comparison"},
        ),
        QAPair(
            question="What is the meaning of life?",
            expected_answer="This question is outside the scope of this system. I can help with AI and technology questions.",
            context="This is an AI assistant specialized in technology topics.",
            metadata={"difficulty": "adversarial", "category": "out_of_scope"},
        ),
    ]

    evaluator = RAGASEvaluator()
    runner = BenchmarkRunner()

    def mock_agent(question: str) -> str:
        return f"Based on my knowledge: {question[:30]}... The answer involves key concepts."

    results = runner.run(qa_pairs, mock_agent, evaluator)
    report = runner.generate_report(results)
    print("=== Benchmark Report ===")
    for k, v in report.items():
        print(f"  {k}: {v}")

    failures = runner.identify_failures(results, threshold=0.5)
    print(f"\n=== Failures ({len(failures)}) ===")
    analyzer = FailureAnalyzer()

    categories = analyzer.categorize_failures(failures)
    print("Failure Categories:", categories)

    for f in failures:
        cause = analyzer.find_root_cause(f)
        print(f"  Root cause: {cause}")

    suggestions = analyzer.generate_improvement_suggestions(failures)
    print("\nImprovement Suggestions:")
    for s in suggestions:
        print(f"  - {s}")

    log = analyzer.generate_improvement_log(failures, suggestions)
    print("\n=== Improvement Log ===")
    print(log)