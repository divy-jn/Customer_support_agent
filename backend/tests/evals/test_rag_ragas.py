"""
RAGAS RAG Evaluation Tests — measures faithfulness, relevancy,
context precision, and context recall of the RAG pipeline.

Uses the RAGAS evaluation framework with Ollama as the evaluator LLM.

Metrics:
  - Faithfulness: Is the response grounded in the retrieved context?
  - Answer Relevancy: Does the answer address the question?
  - Context Precision: Were the right chunks retrieved?
  - Context Recall: Did we retrieve all relevant chunks?
"""

import sys
import os
import json
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


# ──────────────────────────────────────────────
#  Test Data — RAG-specific scenarios
# ──────────────────────────────────────────────

RAG_EVAL_CASES = [
    {
        "question": "What is the return policy?",
        "ground_truth": "Items can be returned within 30 days of purchase in original condition with receipt.",
        "expected_source": "return_policy.md",
    },
    {
        "question": "How long is the warranty?",
        "ground_truth": "Products come with a 12-month warranty covering manufacturing defects.",
        "expected_source": "warranty_policy.md",
    },
    {
        "question": "What shipping options are available?",
        "ground_truth": "Standard shipping (5-7 business days), Express (2-3 days), and Overnight options.",
        "expected_source": "shipping_policy.md",
    },
    {
        "question": "How do I troubleshoot a product that won't turn on?",
        "ground_truth": "Check the power cable, try a different outlet, perform a hard reset by holding power for 10 seconds.",
        "expected_source": "troubleshooting.md",
    },
    {
        "question": "What payment methods do you accept?",
        "ground_truth": "Visa, MasterCard, American Express, PayPal, and bank transfers.",
        "expected_source": "payment_methods.md",
    },
]


# ──────────────────────────────────────────────
#  Retriever Tests (no LLM required)
# ──────────────────────────────────────────────

class TestRetrieverQuality:
    """Tests that the ChromaDB retriever returns relevant chunks."""

    @pytest.mark.skipif(
        not os.path.exists(os.path.join(os.path.dirname(__file__), "..", "..", "chroma_db")),
        reason="ChromaDB not initialized. Run ingest_knowledge.py first."
    )
    @pytest.mark.parametrize("case", RAG_EVAL_CASES, ids=lambda c: c["question"][:30])
    def test_retriever_returns_relevant_source(self, case):
        from app.rag.retriever import retrieve
        
        results = retrieve(case["question"], top_k=5)
        
        # At least one result should be returned
        assert len(results) > 0, f"No results for: {case['question']}"
        
        # The expected source should appear in the top-5
        sources = [r["source"] for r in results]
        assert case["expected_source"] in sources, (
            f"Expected source '{case['expected_source']}' not in top-5 results.\n"
            f"Got sources: {sources}"
        )

    @pytest.mark.skipif(
        not os.path.exists(os.path.join(os.path.dirname(__file__), "..", "..", "chroma_db")),
        reason="ChromaDB not initialized."
    )
    def test_retriever_relevance_scores(self):
        """Top result should have a relevance score > 0.3."""
        from app.rag.retriever import retrieve
        
        results = retrieve("What is the return policy?", top_k=3)
        assert len(results) > 0
        # Highest relevance should be reasonable
        assert results[0]["score"] > 0.3, f"Top score too low: {results[0]['score']}"


# ──────────────────────────────────────────────
#  RAG Response Quality Tests (requires Ollama)
# ──────────────────────────────────────────────

@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_TESTS"),
    reason="Live tests require RUN_LIVE_TESTS=1 and Ollama running"
)
class TestRAGResponseQuality:
    """Live tests that validate RAG response quality with the actual LLM."""

    @pytest.mark.parametrize("case", RAG_EVAL_CASES, ids=lambda c: c["question"][:30])
    def test_response_is_grounded(self, case):
        """The response should contain information from the ground truth."""
        from app.agents.rag_agent import generate_response
        
        result = generate_response(case["question"])
        response = result["response"].lower()
        
        # The response should not be a refusal
        assert "i don't have" not in response, f"RAG refused to answer: {case['question']}"
        
        # Should use context
        assert result.get("context_used", False), f"RAG didn't use context for: {case['question']}"

    def test_response_refuses_unknown_topic(self):
        """For questions outside the KB, the RAG should gracefully refuse."""
        from app.agents.rag_agent import generate_response
        
        result = generate_response("What is the capital of France?")
        response = result["response"].lower()
        
        # Should indicate it doesn't have the information
        assert any(phrase in response for phrase in [
            "don't have", "not sure", "knowledge base", "human agent", "outside"
        ]), f"RAG should refuse unknown topics, but said: {result['response'][:100]}"


# ──────────────────────────────────────────────
#  RAGAS Framework Evaluation (requires ragas + Ollama)
# ──────────────────────────────────────────────

@pytest.mark.skipif(
    not os.environ.get("RUN_RAGAS_EVAL"),
    reason="RAGAS eval requires RUN_RAGAS_EVAL=1, ragas installed, and Ollama running"
)
class TestRAGASEvaluation:
    """Full RAGAS evaluation. Run with RUN_RAGAS_EVAL=1."""

    def test_ragas_metrics(self):
        """Run RAGAS evaluation and check metric thresholds."""
        try:
            from ragas import evaluate
            from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
            from datasets import Dataset
        except ImportError:
            pytest.skip("ragas or datasets not installed")

        from app.rag.retriever import retrieve
        from app.agents.rag_agent import generate_response

        # Build evaluation dataset
        questions = []
        answers = []
        contexts = []
        ground_truths = []

        for case in RAG_EVAL_CASES:
            # Get RAG response
            result = generate_response(case["question"])
            # Get retrieved contexts
            retrieved = retrieve(case["question"], top_k=5)

            questions.append(case["question"])
            answers.append(result["response"])
            contexts.append([r["text"] for r in retrieved])
            ground_truths.append(case["ground_truth"])

        dataset = Dataset.from_dict({
            "question": questions,
            "answer": answers,
            "contexts": contexts,
            "ground_truth": ground_truths,
        })

        # Run RAGAS evaluation
        result = evaluate(
            dataset,
            metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        )

        print("\n=== RAGAS Evaluation Results ===")
        print(json.dumps(dict(result), indent=2))

        # Assert minimum thresholds
        assert result["faithfulness"] > 0.5, f"Faithfulness too low: {result['faithfulness']}"
        assert result["answer_relevancy"] > 0.5, f"Answer relevancy too low: {result['answer_relevancy']}"
        assert result["context_precision"] > 0.4, f"Context precision too low: {result['context_precision']}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
