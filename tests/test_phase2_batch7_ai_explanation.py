import pytest
import os
from unittest.mock import patch, MagicMock
import requests

from ai.explainer import AIExplainer, DEFAULT_HF_MODEL
from analyzer.history_manager import HistoryManager
from analyzer.playground_manager import PlaygroundManager
from models.analysis_models import (
    AnalysisResult, ParsedQuery, DatabaseSchema, TableRef, ColumnRef,
    PlanNode, Finding, Recommendation, Severity, BenchmarkMetrics,
    BenchmarkResult, EquivalenceResult, HistoryEntry
)


def _create_sample_analysis_result() -> AnalysisResult:
    """Helper to construct a rich AnalysisResult with benchmark and equivalence facts."""
    parsed = ParsedQuery(
        raw_sql="SELECT * FROM customers WHERE city = 'London';",
        is_select=True,
        tables=[TableRef(name="customers")],
        selected_columns=[ColumnRef(name="*", is_wildcard=True)]
    )

    plan_nodes = [
        PlanNode(id=1, parent=0, detail="SCAN customers", step_type="SCAN", table_name="customers")
    ]

    findings = [
        Finding(
            rule_id="RULE_01",
            title="Unindexed Filter Column",
            severity=Severity.HIGH,
            category="Indexing",
            description="Table customers has no index on filter column city.",
            rationale="A full table scan is performed for equality filters without an index.",
            affected_table="customers",
            affected_column="city"
        )
    ]

    recs = [
        Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Create index on customers(city)",
            ddl="CREATE INDEX idx_customers_city ON customers(city);",
            table="customers",
            columns=["city"],
            strategy="INDEX_BASED"
        )
    ]

    orig_metrics = BenchmarkMetrics(
        exec_time_ms=12.50,
        rows_returned=50,
        plan_nodes=plan_nodes,
        plan_summary="SCAN customers",
        min_time_ms=11.80,
        max_time_ms=13.20,
        median_time_ms=12.50,
        raw_times_ms=[12.0, 12.5, 13.0, 12.2, 12.8, 12.5, 12.5],
        warmup_times_ms=[15.0, 13.5, 12.9]
    )

    opt_metrics = BenchmarkMetrics(
        exec_time_ms=1.25,
        rows_returned=50,
        plan_nodes=[
            PlanNode(id=1, parent=0, detail="SEARCH customers USING INDEX idx_customers_city (city=?)", step_type="SEARCH", table_name="customers", index_name="idx_customers_city")
        ],
        plan_summary="SEARCH customers USING INDEX idx_customers_city",
        min_time_ms=1.10,
        max_time_ms=1.45,
        median_time_ms=1.25,
        raw_times_ms=[1.2, 1.3, 1.25, 1.25, 1.1, 1.45, 1.2],
        warmup_times_ms=[3.5, 1.8, 1.3]
    )

    eq_res = EquivalenceResult(
        is_equivalent=True,
        columns_match=True,
        column_count_match=True,
        row_count_match=True,
        values_match=True,
        order_match=True,
        has_order_by=False,
        orig_row_count=50,
        opt_row_count=50,
        orig_col_count=4,
        opt_col_count=4,
        explanation="The optimized query returned the same result set as the original query."
    )

    bench_res = BenchmarkResult(
        original_metrics=orig_metrics,
        optimized_metrics=opt_metrics,
        speedup_factor=10.0,
        time_saved_ms=11.25,
        indexes_created=["CREATE INDEX idx_customers_city ON customers(city)"],
        benchmark_applied=True,
        plan_changed=True,
        plan_change_desc="SCAN → SEARCH",
        equivalence_result=eq_res,
        note="The tested candidate improved measured execution time by approximately 90.0% in this multi-run benchmark (12.50ms → 1.25ms).",
        candidate_name="customers(city)",
        strategy="INDEX_BASED",
        candidate_ddl="CREATE INDEX idx_customers_city ON customers(city);",
        warmup_runs=3,
        measured_runs=7,
        original_median_ms=12.50,
        optimized_median_ms=1.25,
        original_min_ms=11.80,
        optimized_min_ms=1.10,
        original_max_ms=13.20,
        optimized_max_ms=1.45,
        median_difference_ms=-11.25,
        median_speedup=10.0,
        percentage_difference=-90.0,
        performance_classification="PERFORMANCE_IMPROVED"
    )

    return AnalysisResult(
        query="SELECT * FROM customers WHERE city = 'London';",
        parsed_query=parsed,
        schema=DatabaseSchema(db_path="test.db"),
        plan_nodes=plan_nodes,
        findings=findings,
        recommendations=recs,
        optimization_strategy="INDEX_BASED",
        benchmark_result=bench_res
    )


class TestLightweightHuggingFaceAIExplanation:
    """Test suite for Phase 2 — Batch 7 Hugging Face AI Explanation Layer."""

    def test_structured_context_generation(self):
        """1. Verifies that build_structured_context extracts exact verified deterministic facts."""
        analysis = _create_sample_analysis_result()
        explainer = AIExplainer(token="hf_mock_token_123")

        context = explainer.build_structured_context(analysis)

        assert context["sql_query"] == "SELECT * FROM customers WHERE city = 'London';"
        assert context["tables"] == ["customers"]
        assert context["optimization_strategy"] == "INDEX_BASED"
        assert len(context["findings"]) == 1
        assert context["findings"][0]["rule_id"] == "RULE_01"
        assert context["findings"][0]["severity"] == "High"
        assert len(context["recommendations"]) == 1
        assert context["recommendations"][0]["ddl"] == "CREATE INDEX idx_customers_city ON customers(city);"
        assert context["has_benchmark"] is True

    def test_candidate_and_benchmark_metrics_in_context(self):
        """2. Verifies that multi-run benchmark metrics and candidate identity are passed."""
        analysis = _create_sample_analysis_result()
        explainer = AIExplainer(token="hf_mock_token_123")

        context = explainer.build_structured_context(analysis)
        bm = context["benchmark"]

        assert bm["candidate_name"] == "customers(city)"
        assert bm["strategy"] == "INDEX_BASED"
        assert bm["candidate_ddl"] == "CREATE INDEX idx_customers_city ON customers(city);"
        assert bm["warmup_runs"] == 3
        assert bm["measured_runs"] == 7
        assert bm["original_median_ms"] == 12.50
        assert bm["optimized_median_ms"] == 1.25
        assert bm["median_difference_ms"] == -11.25
        assert bm["median_speedup"] == 10.0
        assert bm["percentage_difference"] == -90.0
        assert bm["performance_classification"] == "PERFORMANCE_IMPROVED"
        assert bm["plan_change_desc"] == "SCAN → SEARCH"
        assert bm["equivalence"]["is_equivalent"] is True

    def test_prompt_formatting_contains_all_verified_facts(self):
        """3. Verifies format_prompt includes all factual sections in clear structure."""
        analysis = _create_sample_analysis_result()
        explainer = AIExplainer(token="hf_mock_token_123")

        context = explainer.build_structured_context(analysis)
        prompt = explainer.format_prompt(context)

        assert "=== STRUCTURED FACTS FOR AI EXPLANATION ===" in prompt
        assert "SELECT * FROM customers WHERE city = 'London';" in prompt
        assert "RULE_01" in prompt
        assert "customers(city)" in prompt
        assert "CREATE INDEX idx_customers_city ON customers(city);" in prompt
        assert "Original Median: 12.50 ms" in prompt
        assert "Optimized Median: 1.25 ms" in prompt
        assert "Percentage Difference: -90.0%" in prompt
        assert "PERFORMANCE_IMPROVED" in prompt
        assert "Result Equivalence: PASS" in prompt

    def test_missing_hf_token_graceful_handling(self):
        """4. Verifies explainer returns clear developer-configuration guidance when HF_TOKEN is absent."""
        with patch.dict(os.environ, {}, clear=True):
            explainer = AIExplainer(token=None)
            assert explainer.is_configured is False

            analysis = _create_sample_analysis_result()
            result = explainer.generate_explanation(analysis)

            assert "AI explanation is not configured" in result
            assert "The developer must configure HF_TOKEN" in result

    def test_hf_token_loaded_from_environment(self):
        """5. Verifies HF_TOKEN is correctly loaded from environment variable."""
        with patch.dict(os.environ, {"HF_TOKEN": "hf_env_secret_token_777"}):
            explainer = AIExplainer()
            assert explainer.is_configured is True
            assert explainer.token == "hf_env_secret_token_777"

    def test_credentials_never_in_prompt_or_context(self):
        """6. Verifies API credentials are never injected into structured context or prompt."""
        token_secret = "hf_super_secret_token_999"
        explainer = AIExplainer(token=token_secret)
        analysis = _create_sample_analysis_result()

        context = explainer.build_structured_context(analysis)
        prompt = explainer.format_prompt(context)

        assert token_secret not in str(context)
        assert token_secret not in prompt
        assert "token" not in prompt.lower()
        assert "hf_" not in prompt

    @patch("ai.explainer.InferenceClient")
    def test_mocked_huggingface_inference_client_success(self, mock_client_cls):
        """7. Verifies successful explanation generation via official Hugging Face InferenceClient."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "The query filters by city on an unindexed table, causing a full SCAN. Creating the index allows SQLite to use SEARCH, speeding up execution by 90.0%."
        mock_resp.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        explainer = AIExplainer(token="hf_valid_token_abc")
        analysis = _create_sample_analysis_result()
        explanation = explainer.generate_explanation(analysis)

        assert "full SCAN" in explanation
        assert "90.0%" in explanation
        assert mock_client.chat.completions.create.called

    @patch("ai.explainer.InferenceClient", None)
    @patch("requests.post")
    def test_mocked_huggingface_router_success(self, mock_post):
        """8. Verifies successful explanation generation via Hugging Face router endpoint fallback."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "Router fallback explanation: Index eliminates SCAN with 10x speedup."
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        explainer = AIExplainer(token="hf_valid_token_abc")
        analysis = _create_sample_analysis_result()
        explanation = explainer.generate_explanation(analysis)

        assert "Router fallback explanation" in explanation
        assert mock_post.called

    @patch("ai.explainer.InferenceClient", None)
    @patch("requests.post")
    def test_mocked_auth_error_handling(self, mock_post):
        """9. Verifies 401 unauthorized token returns safe message without leaking token."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        token_secret = "hf_secret_token_never_leak"
        explainer = AIExplainer(token=token_secret)
        analysis = _create_sample_analysis_result()
        explanation = explainer.generate_explanation(analysis)

        assert "Authentication Error" in explanation
        assert token_secret not in explanation

    @patch("ai.explainer.InferenceClient", None)
    @patch("requests.post")
    def test_mocked_rate_limit_error_handling(self, mock_post):
        """10. Verifies 429 rate limit returns user-friendly guidance."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_post.return_value = mock_response

        explainer = AIExplainer(token="hf_mock_token")
        analysis = _create_sample_analysis_result()
        explanation = explainer.generate_explanation(analysis)

        assert "Rate Limit Exceeded" in explanation

    @patch("ai.explainer.InferenceClient", None)
    @patch("requests.post")
    def test_mocked_model_loading_503_handling(self, mock_post):
        """11. Verifies 503 model initializing returns clear retry note."""
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_post.return_value = mock_response

        explainer = AIExplainer(token="hf_mock_token")
        analysis = _create_sample_analysis_result()
        explanation = explainer.generate_explanation(analysis)

        assert "Model Loading" in explanation

    @patch("ai.explainer.InferenceClient", None)
    @patch("requests.post")
    def test_mocked_timeout_handling(self, mock_post):
        """12. Verifies timeout returns clean non-crashing message."""
        mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")

        explainer = AIExplainer(token="hf_mock_token", timeout=2)
        analysis = _create_sample_analysis_result()
        explanation = explainer.generate_explanation(analysis)

        assert "Request Timeout" in explanation

    @patch("ai.explainer.InferenceClient", None)
    @patch("requests.post")
    def test_mocked_connection_error_graceful_handling(self, mock_post):
        """13. Verifies connection error is handled gracefully with safe user message."""
        mock_post.side_effect = requests.exceptions.ConnectionError("Failed to resolve host")

        explainer = AIExplainer(token="hf_mock_token")
        analysis = _create_sample_analysis_result()
        explanation = explainer.generate_explanation(analysis)

        assert "AI explanation service is temporarily unavailable" in explanation
        assert "Deterministic optimization analysis is still available" in explanation
        assert "Failed to resolve host" not in explanation
        assert "Traceback" not in explanation

    def test_deterministic_candidate_protection(self):
        """14. Verifies AI layer cannot modify deterministic recommendations, SQL, or plans."""
        analysis = _create_sample_analysis_result()
        original_ddl = analysis.recommendations[0].ddl
        original_sql = analysis.query
        original_strategy = analysis.optimization_strategy
        original_speedup = analysis.benchmark_result.speedup_factor

        explainer = AIExplainer(token="hf_mock_token")
        explainer.build_structured_context(analysis)

        # Confirm nothing in the deterministic models was mutated
        assert analysis.recommendations[0].ddl == original_ddl
        assert analysis.query == original_sql
        assert analysis.optimization_strategy == original_strategy
        assert analysis.benchmark_result.speedup_factor == original_speedup

    def test_playground_state_isolation_and_invalidation(self):
        """15. Verifies updating SQL in Query Playground invalidates stale AI explanation."""
        pm = PlaygroundManager()
        q = pm.create_query("Query 1")
        
        analysis = _create_sample_analysis_result()
        analysis.ai_explanation = "Previous explanation for query 1"
        pm.set_analysis(q.id, analysis)

        assert q.analysis_result.ai_explanation == "Previous explanation for query 1"

        # When SQL changes, analysis_result is cleared
        pm.update_sql(q.id, "SELECT * FROM orders WHERE total_amount > 100;")
        assert q.analysis_result is None
        assert q.status == "not_analyzed"

    def test_history_manager_stores_ai_explanation(self):
        """16. Verifies HistoryManager updates and persists generated AI explanation."""
        hm = HistoryManager()
        query = "SELECT * FROM customers WHERE city = 'London';"

        entry = hm.record_analysis(
            db_name="test.db",
            query=query,
            optimization_name="customers(city)",
            strategy="INDEX_BASED"
        )
        assert entry.ai_explanation is None

        explanation_text = "Educational explanation generated on demand."
        updated = hm.update_ai_explanation(query=query, ai_explanation=explanation_text)

        assert updated is not None
        assert updated.ai_explanation == explanation_text
        assert hm.get_entries()[0].ai_explanation == explanation_text

    def test_history_manager_legacy_entries_compatibility(self):
        """17. Verifies legacy HistoryEntry without ai_explanation remains completely valid."""
        legacy = HistoryEntry(
            id="leg_100",
            timestamp="2026-09-01 10:00:00",
            db_name="test.db",
            query="SELECT * FROM orders;",
            optimization_name="orders(customer_id)",
            strategy="INDEX_BASED",
            is_tested=True,
            orig_time_ms=20.0,
            opt_time_ms=4.0,
            speedup_factor=5.0
        )

        assert legacy.ai_explanation is None
        assert legacy.speedup_factor == 5.0

    def test_full_analysis_only_explanation(self):
        """18. Verifies analysis-only state formats prompt without fabricated benchmark claims."""
        parsed = ParsedQuery(
            raw_sql="SELECT * FROM orders WHERE status = 'pending';",
            is_select=True,
            tables=[TableRef(name="orders")],
            selected_columns=[ColumnRef(name="*", is_wildcard=True)]
        )
        analysis_only = AnalysisResult(
            query="SELECT * FROM orders WHERE status = 'pending';",
            parsed_query=parsed,
            schema=DatabaseSchema(db_path="test.db"),
            plan_nodes=[PlanNode(id=1, parent=0, detail="SCAN orders", step_type="SCAN", table_name="orders")],
            findings=[Finding(rule_id="RULE_01", title="Unindexed Filter Column", severity=Severity.HIGH, category="Indexing", description="orders.status is unindexed.", rationale="Causes full scan.", affected_table="orders", affected_column="status")],
            recommendations=[Recommendation(id="REC_01", type="CREATE INDEX", description="Create index on orders(status)", ddl="CREATE INDEX idx_orders_status ON orders(status);", table="orders", columns=["status"], strategy="INDEX_BASED")],
            optimization_strategy="INDEX_BASED",
            benchmark_result=None
        )

        explainer = AIExplainer(token="hf_mock_token")
        context = explainer.build_structured_context(analysis_only)
        assert context["has_benchmark"] is False
        assert "benchmark" not in context

        prompt = explainer.format_prompt(context)
        assert "=== TEST STATUS: ANALYSIS-ONLY (TEST NOT RUN) ===" in prompt
        assert "orders(status)" in prompt
        assert "Benchmark Protocol" not in prompt
        assert "Result Equivalence:" not in prompt

    def test_full_tested_optimization_explanation_contains_all_lifecycle_facts(self):
        """19. Verifies tested optimization includes safety isolation, plan diff, equivalence, and benchmark."""
        analysis = _create_sample_analysis_result()
        explainer = AIExplainer(token="hf_mock_token")
        context = explainer.build_structured_context(analysis)
        prompt = explainer.format_prompt(context)

        assert "=== EMPIRICAL TEST & BENCHMARK FACTS ===" in prompt
        assert "Safety Protocol: Tested in temporary isolated DB copy; original DB unmodified; SQL unchanged." in prompt
        assert "Plan Change:" in prompt
        assert "Result Equivalence: PASS" in prompt
        assert "Benchmark Protocol:" in prompt
        assert "Deterministic Performance Classification: PERFORMANCE_IMPROVED" in prompt

    def test_performance_regression_lifecycle_explanation(self):
        """20. Verifies PERFORMANCE_REGRESSION passes regression facts and preserves classification."""
        analysis = _create_sample_analysis_result()
        bm = analysis.benchmark_result
        bm.original_median_ms = 4.96
        bm.optimized_median_ms = 5.59
        bm.median_difference_ms = 0.63
        bm.median_speedup = 0.88
        bm.percentage_difference = 12.7
        bm.performance_classification = "PERFORMANCE_REGRESSION"
        bm.plan_change_desc = "TEMP B-TREE eliminated"

        explainer = AIExplainer(token="hf_mock_token")
        context = explainer.build_structured_context(analysis)
        prompt = explainer.format_prompt(context)

        assert "PERFORMANCE_REGRESSION" in prompt
        assert "TEMP B-TREE eliminated" in prompt
        assert "4.96 ms" in prompt
        assert "5.59 ms" in prompt
        assert "+12.7%" in prompt

    def test_no_measurable_improvement_lifecycle_explanation(self):
        """21. Verifies NO_MEASURABLE_IMPROVEMENT passes tolerance facts cleanly."""
        analysis = _create_sample_analysis_result()
        bm = analysis.benchmark_result
        bm.original_median_ms = 5.00
        bm.optimized_median_ms = 4.95
        bm.median_difference_ms = -0.05
        bm.median_speedup = 1.01
        bm.percentage_difference = -1.0
        bm.performance_classification = "NO_MEASURABLE_IMPROVEMENT"

        explainer = AIExplainer(token="hf_mock_token")
        context = explainer.build_structured_context(analysis)
        prompt = explainer.format_prompt(context)

        assert "NO_MEASURABLE_IMPROVEMENT" in prompt
        assert "5.00 ms" in prompt
        assert "4.95 ms" in prompt

    def test_validation_failure_lifecycle_explanation(self):
        """22. Verifies result non-equivalence / validation failure is clearly passed."""
        analysis = _create_sample_analysis_result()
        bm = analysis.benchmark_result
        bm.equivalence_result = EquivalenceResult(
            is_equivalent=False,
            columns_match=True,
            column_count_match=True,
            row_count_match=False,
            values_match=False,
            order_match=False,
            has_order_by=False,
            orig_row_count=50,
            opt_row_count=30,
            orig_col_count=4,
            opt_col_count=4,
            explanation="Row count mismatch: Original produced 50 rows, Optimized produced 30 rows.",
            mismatch_reason="ROW_COUNT_MISMATCH"
        )
        bm.performance_classification = "VALIDATION_FAILURE"

        explainer = AIExplainer(token="hf_mock_token")
        context = explainer.build_structured_context(analysis)
        prompt = explainer.format_prompt(context)

        assert "Result Equivalence: FAIL" in prompt
        assert "Row count mismatch" in prompt
        assert "VALIDATION_FAILURE" in prompt

    @patch("ai.explainer.InferenceClient")
    def test_mocked_five_section_generation_for_tested_improvement(self, mock_client_cls):
        """23. Verifies complete 5-section structure for a tested optimization."""
        mock_output = (
            "## Summary\n"
            "The query filters by city on an unindexed table, causing a full table scan.\n\n"
            "## Optimization Tested\n"
            "Candidate index customers(city) was tested in an isolated temporary database copy. Original database and SQL remained unchanged.\n\n"
            "## Plan Change\n"
            "The plan changed from SCAN customers to SEARCH customers USING INDEX idx_customers_city.\n\n"
            "## Correctness & Performance\n"
            "Result equivalence: PASS. 3 warm-up runs were excluded and 7 measured runs were used. Original median: 12.50 ms; optimized median: 1.25 ms (10.00x speedup, +90.0% difference; tolerance: 5%). Classification: ✓ Performance Improved.\n\n"
            "## Final Verdict\n"
            "The index allows direct index search and significantly speeds up execution while preserving equivalent results.\n"
            "✓ Performance Improved"
        )
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = mock_output
        mock_resp.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        explainer = AIExplainer(token="hf_mock_token")
        analysis = _create_sample_analysis_result()
        result = explainer.generate_explanation(analysis)

        assert "## Summary" in result
        assert "## Optimization Tested" in result
        assert "## Plan Change" in result
        assert "## Correctness & Performance" in result
        assert "## Final Verdict" in result
        assert "✓ Performance Improved" in result
        assert "Result equivalence: PASS." in result

    @patch("ai.explainer.InferenceClient")
    def test_mocked_five_section_generation_for_analysis_only(self, mock_client_cls):
        """24. Verifies complete 5-section structure for an analysis-only query without benchmark data."""
        mock_output = (
            "## Summary\n"
            "The query filters by city on an unindexed table, causing a full table scan.\n\n"
            "## Optimization Tested\n"
            "Candidate index customers(city) is recommended. Empirical validation has not been performed yet.\n\n"
            "## Plan Change\n"
            "Current plan uses SCAN. Creating the index is expected to change the plan to SEARCH.\n\n"
            "## Correctness & Performance\n"
            "Performance testing and result equivalence validation have not been performed yet, so there is no measured speedup or regression to report.\n\n"
            "## Final Verdict\n"
            "Recommendation remains unvalidated until empirical testing is performed."
        )
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = mock_output
        mock_resp.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        explainer = AIExplainer(token="hf_mock_token")
        parsed = ParsedQuery(
            raw_sql="SELECT * FROM customers WHERE city = 'London';",
            is_select=True,
            tables=[TableRef(name="customers")],
            selected_columns=[ColumnRef(name="*", is_wildcard=True)]
        )
        analysis_untested = AnalysisResult(
            query="SELECT * FROM customers WHERE city = 'London';",
            parsed_query=parsed,
            schema=DatabaseSchema(db_path="test.db"),
            plan_nodes=[PlanNode(id=1, parent=0, detail="SCAN customers", step_type="SCAN", table_name="customers")],
            findings=[Finding(rule_id="RULE_01", title="Unindexed Filter Column", severity=Severity.HIGH, category="Indexing", description="city is unindexed.", rationale="Causes full scan.", affected_table="customers", affected_column="city")],
            recommendations=[Recommendation(id="REC_01", type="CREATE INDEX", description="Create index on customers(city)", ddl="CREATE INDEX idx_customers_city ON customers(city);", table="customers", columns=["city"], strategy="INDEX_BASED")],
            optimization_strategy="INDEX_BASED",
            benchmark_result=None
        )
        result = explainer.generate_explanation(analysis_untested)

        assert "## Summary" in result
        assert "## Optimization Tested" in result
        assert "## Plan Change" in result
        assert "## Correctness & Performance" in result
        assert "## Final Verdict" in result
        assert "Performance testing and result equivalence validation have not been performed yet" in result
        assert "Recommendation remains unvalidated until empirical testing is performed." in result
