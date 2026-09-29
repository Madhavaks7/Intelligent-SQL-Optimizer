import os
import json
import logging
from typing import Optional, Dict, Any

from dotenv import load_dotenv
load_dotenv()

try:
    from huggingface_hub import InferenceClient
    from huggingface_hub.errors import HfHubHTTPError
except ImportError:
    InferenceClient = None
    HfHubHTTPError = Exception

import requests
from models.analysis_models import AnalysisResult

logger = logging.getLogger(__name__)

DEFAULT_HF_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"

class AIExplainer:
    """
    Lightweight Hugging Face AI Explanation Layer (Phase 2 — Batch 7 & 7.1).
    Translates deterministic query analysis findings, execution plan observations,
    isolated test safety protocols, plan comparisons, result-equivalence validations,
    and multi-run benchmark facts into comprehensive, educational human-readable explanations.

    The deterministic optimizer remains the sole authority. The AI layer strictly
    articulates verified system facts and never alters SQL, generates candidates,
    or overrides benchmark results.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        timeout: int = 20
    ):
        # Developer-side token configuration from environment or constructor
        raw_token = (
            token or
            os.getenv("HF_TOKEN") or
            os.getenv("HUGGINGFACE_API_KEY") or
            os.getenv("HUGGINGFACE_TOKEN", "")
        )
        self.token = raw_token.strip() if raw_token else ""
        self.model = (model or os.getenv("HF_MODEL", DEFAULT_HF_MODEL)).strip()
        self.provider = provider or os.getenv("HF_PROVIDER")
        self.timeout = timeout
        self.router_url = "https://router.huggingface.co/v1/chat/completions"

    @property
    def is_configured(self) -> bool:
        """Returns True if a developer-configured Hugging Face API token is present."""
        return bool(self.token)

    def build_structured_context(self, analysis: AnalysisResult) -> Dict[str, Any]:
        """
        Extracts verified deterministic facts from the analysis result into a structured dictionary.
        Strictly contains empirical data produced by parser, rules, plans, and benchmarks.
        """
        tables = [t.name for t in analysis.parsed_query.tables]
        
        # Extract query specifics
        cols = []
        for c in analysis.parsed_query.selected_columns:
            prefix = f"{c.table}." if c.table else ""
            func_wrap = f"{c.agg_func}({prefix}{c.name})" if c.agg_func else f"{prefix}{c.name}"
            cols.append(func_wrap)

        filters = []
        for p in analysis.parsed_query.where_predicates:
            p_str = p.raw_expr if p.raw_expr else f"{p.column} {p.operator} {p.value or ''}".strip()
            filters.append(p_str)

        joins_info = [f"{j.join_type} {j.right_table} ON {' AND '.join(j.on_conditions)}" for j in analysis.parsed_query.joins]
        order_by_info = [f"{o.column} {o.direction}" for o in analysis.parsed_query.order_by]
        group_by_info = analysis.parsed_query.group_by
        limit_info = analysis.parsed_query.limit

        # Schema details relevant to query tables
        schema_tables = {}
        if analysis.schema and getattr(analysis.schema, "tables", None):
            for t_name in tables:
                if t_name in analysis.schema.tables:
                    t_schema = analysis.schema.tables[t_name]
                    schema_tables[t_name] = {
                        "row_count": t_schema.row_count,
                        "primary_key": t_schema.primary_key,
                        "existing_indexes": [idx.name for idx in t_schema.indexes]
                    }

        findings_data = [
            {
                "rule_id": f.rule_id,
                "title": f.title,
                "severity": f.severity.value,
                "category": f.category,
                "description": f.description,
                "rationale": f.rationale,
                "table": f.affected_table,
                "column": f.affected_column
            }
            for f in analysis.findings
        ]

        orig_steps = [f"{n.step_type}: {n.detail}" for n in analysis.plan_nodes]
        recs_data = [
            {
                "type": r.type,
                "description": r.description,
                "ddl": r.ddl,
                "strategy": r.strategy,
                "table": r.table,
                "columns": r.columns
            }
            for r in analysis.recommendations
        ]

        context: Dict[str, Any] = {
            "sql_query": analysis.query,
            "tables": tables,
            "selected_columns": cols,
            "where_predicates": filters,
            "joins": joins_info,
            "group_by": group_by_info,
            "order_by": order_by_info,
            "limit": limit_info,
            "schema_facts": schema_tables,
            "optimization_strategy": analysis.optimization_strategy,
            "findings": findings_data,
            "recommendations": recs_data,
            "original_plan_steps": orig_steps,
            "has_benchmark": False
        }

        if analysis.benchmark_result and analysis.benchmark_result.benchmark_applied:
            b = analysis.benchmark_result
            opt_steps = (
                [f"{n.step_type}: {n.detail}" for n in b.optimized_metrics.plan_nodes]
                if b.optimized_metrics else []
            )

            eq_data = None
            if b.equivalence_result:
                eq = b.equivalence_result
                eq_data = {
                    "is_equivalent": eq.is_equivalent,
                    "columns_match": eq.columns_match,
                    "values_match": eq.values_match,
                    "order_match": eq.order_match,
                    "has_order_by": eq.has_order_by,
                    "orig_row_count": eq.orig_row_count,
                    "opt_row_count": eq.opt_row_count,
                    "orig_col_count": eq.orig_col_count,
                    "opt_col_count": eq.opt_col_count,
                    "explanation": eq.explanation,
                    "mismatch_reason": eq.mismatch_reason
                }

            context["has_benchmark"] = True
            context["benchmark"] = {
                "candidate_name": b.candidate_name,
                "strategy": b.strategy,
                "candidate_ddl": b.candidate_ddl,
                "benchmark_applied": b.benchmark_applied,
                "safety_isolation": {
                    "database_isolation": "Isolated temporary SQLite database copy created.",
                    "original_database_safe": "Original database remained strictly read-only and untouched.",
                    "sql_unmodified": "SQL query remained 100% unchanged.",
                    "test_environment_modified": "Index candidate applied strictly within temporary isolated copy."
                },
                "plan_changed": b.plan_changed,
                "plan_change_desc": b.plan_change_desc,
                "original_plan_summary": b.original_metrics.plan_summary if b.original_metrics else "",
                "optimized_plan_summary": b.optimized_metrics.plan_summary if b.optimized_metrics else "",
                "optimized_plan_steps": opt_steps,
                "equivalence": eq_data,
                "warmup_runs": b.warmup_runs,
                "measured_runs": b.measured_runs,
                "warmups_excluded": True,
                "original_median_ms": b.original_median_ms,
                "optimized_median_ms": b.optimized_median_ms,
                "original_min_ms": b.original_min_ms,
                "optimized_min_ms": b.optimized_min_ms,
                "original_max_ms": b.original_max_ms,
                "optimized_max_ms": b.optimized_max_ms,
                "median_difference_ms": b.median_difference_ms,
                "median_speedup": b.median_speedup,
                "percentage_difference": b.percentage_difference,
                "performance_classification": b.performance_classification,
                "benchmark_note": b.note
            }

        return context

    def format_prompt(self, context: Dict[str, Any]) -> str:
        """
        Formats structured context into a concise, factual instruction prompt for the LLM.
        """
        lines = []
        lines.append("=== STRUCTURED FACTS FOR AI EXPLANATION ===")
        lines.append(f"SQL Query: {context['sql_query']}")
        lines.append(f"Tables Involved: {', '.join(context['tables']) if context['tables'] else 'N/A'}")
        if context.get("where_predicates"):
            lines.append(f"WHERE Filters: {', '.join(context['where_predicates'])}")
        if context.get("order_by"):
            lines.append(f"ORDER BY: {', '.join(context['order_by'])}")
        if context.get("group_by"):
            lines.append(f"GROUP BY: {', '.join(context['group_by'])}")
        if context.get("joins"):
            lines.append(f"Joins: {', '.join(context['joins'])}")

        lines.append(f"Deterministic Strategy: {context['optimization_strategy']}")

        lines.append("\nOriginal Plan Summary:")
        if context["original_plan_steps"]:
            for step in context["original_plan_steps"]:
                lines.append(f"  - {step}")
        else:
            lines.append("  - (No plan steps recorded)")

        lines.append("\nFindings & Candidates:")
        if context["findings"]:
            for f in context["findings"]:
                lines.append(f"  - Finding: [{f['rule_id']}] {f['title']} - {f['description']}")
        if context.get("recommendations"):
            for r in context["recommendations"]:
                lines.append(f"  - Candidate: {r['description']} | DDL: {r.get('ddl', 'None')} | Strategy: {r.get('strategy', 'N/A')}")

        if context.get("has_benchmark") and "benchmark" in context:
            bm = context["benchmark"]
            lines.append("\n=== EMPIRICAL TEST & BENCHMARK FACTS ===")
            lines.append(f"Candidate Tested: {bm.get('candidate_name')} (DDL: {bm.get('candidate_ddl')})")
            lines.append("Safety Protocol: Tested in temporary isolated DB copy; original DB unmodified; SQL unchanged.")
            lines.append(f"Plan Change: {bm.get('plan_change_desc')}")
            if bm.get("optimized_plan_steps"):
                lines.append("Optimized Plan Steps:")
                for step in bm["optimized_plan_steps"]:
                    lines.append(f"  - {step}")

            eq = bm.get("equivalence")
            if eq:
                eq_pass = "PASS" if eq["is_equivalent"] else "FAIL"
                eq_expl = f" - {eq.get('explanation')}" if eq.get('explanation') else ""
                lines.append(f"Result Equivalence: {eq_pass} (Original rows: {eq.get('orig_row_count')}, Optimized rows: {eq.get('opt_row_count')}, Columns match: {eq.get('columns_match')}, Values match: {eq.get('values_match')}){eq_expl}")

            if bm.get("original_median_ms") is not None:
                lines.append("Benchmark Protocol: 3 warm-up runs excluded, 7 measured runs used (tolerance: 5%).")
                orig_med = bm.get("original_median_ms")
                opt_med = bm.get("optimized_median_ms")
                orig_min = bm.get("original_min_ms")
                orig_max = bm.get("original_max_ms")
                opt_min = bm.get("optimized_min_ms")
                opt_max = bm.get("optimized_max_ms")
                orig_range = f" (min {orig_min:.2f} ms, max {orig_max:.2f} ms)" if orig_min is not None and orig_max is not None else ""
                opt_range = f" (min {opt_min:.2f} ms, max {opt_max:.2f} ms)" if opt_min is not None and opt_max is not None else ""
                lines.append(f"Original Median: {orig_med:.2f} ms{orig_range}")
                lines.append(f"Optimized Median: {opt_med:.2f} ms{opt_range}" if opt_med is not None else "Optimized Median: N/A")
                lines.append(f"Speedup: {bm.get('median_speedup', 1.0):.2f}x")
                lines.append(f"Percentage Difference: {bm.get('percentage_difference', 0.0):+.1f}%")
                
                # Classification label
                cls_raw = bm.get("performance_classification", "")
                if "IMPROVE" in cls_raw.upper():
                    cls_label = "✓ Performance Improved"
                elif "REGRESS" in cls_raw.upper():
                    cls_label = "✗ Performance Regression"
                elif "NO_MEASURABLE" in cls_raw.upper() or "NO MEASURABLE" in cls_raw.upper():
                    cls_label = "≈ No Measurable Performance Improvement"
                else:
                    cls_label = cls_raw
                lines.append(f"Deterministic Performance Classification: {cls_raw} ({cls_label})")
        else:
            lines.append("\n=== TEST STATUS: ANALYSIS-ONLY (TEST NOT RUN) ===")
            lines.append("Testing has not been performed yet. No benchmark or result equivalence data exists.")

        return "\n".join(lines)

    def generate_explanation(self, analysis: AnalysisResult) -> Optional[str]:
        """
        Generates a dense, compact natural language explanation of deterministic analysis and
        benchmark facts across the complete optimization lifecycle in exactly 5 required sections.
        Returns a structured markdown explanation string, or a safe user-facing message if unavailable.
        """
        if not self.is_configured:
            return "AI explanation is not configured. The developer must configure HF_TOKEN."

        context = self.build_structured_context(analysis)
        prompt_text = self.format_prompt(context)
        has_test = context.get("has_benchmark", False)

        if has_test:
            section_guide = (
                "You must write EXACTLY these 5 markdown sections with '##' headings:\n\n"
                "## Summary\n"
                "1–2 sentences: describe the query and deterministic finding.\n\n"
                "## Optimization Tested\n"
                "1–2 sentences: candidate, strategy, DDL, and state it was tested in an isolated temporary database copy with SQL unchanged.\n\n"
                "## Plan Change\n"
                "1–2 sentences: important original → optimized plan change (e.g., SCAN to SEARCH, TEMP B-TREE eliminated). Do not dump raw EXPLAIN.\n\n"
                "## Correctness & Performance\n"
                "Compact summary of empirical evidence. Must include:\n"
                "- Result equivalence: PASS or FAIL\n"
                "- 3 warm-up runs excluded, 7 measured runs used\n"
                "- Original median ms (and min/max)\n"
                "- Optimized median ms (and min/max)\n"
                "- Speedup (X.XXx) and difference percentage (+X.X% or -X.X%)\n"
                "- Classification label (e.g., 'Classification: ✓ Performance Improved' or 'Classification: ✗ Performance Regression')\n"
                "Example format: 'Result equivalence: PASS. 3 warm-up runs were excluded and 7 measured runs were used. Original median: 6.46 ms; optimized median: 7.66 ms (0.84x speedup, -18.6% difference; tolerance: 5%). Classification: ✗ Performance Regression.'\n\n"
                "## Final Verdict\n"
                "ONE OR TWO SENTENCES ONLY. Summarize why the outcome occurred and end with the exact classification:\n"
                "✓ Performance Improved\n"
                "OR\n"
                "✗ Performance Regression\n"
                "OR\n"
                "≈ No Measurable Performance Improvement\n"
                "Do NOT write anything after the classification."
            )
        else:
            section_guide = (
                "You must write EXACTLY these 5 markdown sections with '##' headings:\n\n"
                "## Summary\n"
                "1–2 sentences: describe the query and deterministic finding.\n\n"
                "## Optimization Tested\n"
                "1–2 sentences: candidate optimization, strategy, DDL, and state empirical validation has not yet been performed.\n\n"
                "## Plan Change\n"
                "1–2 sentences: current execution plan and expected change if tested.\n\n"
                "## Correctness & Performance\n"
                "Performance testing and result equivalence validation have not been performed yet, so there is no measured speedup or regression to report.\n\n"
                "## Final Verdict\n"
                "ONE SENTENCE ONLY:\n"
                "Recommendation remains unvalidated until empirical testing is performed."
            )

        system_instruction = (
            "You are a Database Performance and SQL Optimization Specialist providing concise engineering summaries.\n\n"
            "CRITICAL CONSTRAINTS:\n"
            "1. HARD LENGTH TARGET: 200–350 tokens (maximum ~400 tokens). Be dense, direct, and concise.\n"
            "2. NO REPETITION & NO TUTORIALS: Do not explain SQLite internals or general theory unless necessary.\n"
            "3. Rely strictly on the supplied facts. Never invent numbers, row counts, timings, or plans.\n"
            "4. Never override the deterministic performance classification or result equivalence validation.\n"
            "5. If execution plan improved but measured runtime was slower, explicitly state: 'Plan improved, but runtime regressed.'\n"
            "6. The response MUST reach '## Final Verdict' and end with the classification. Do not get cut off.\n"
            "7. Use EXACTLY the 5 section headers shown below:\n\n"
            f"{section_guide}"
        )

        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Please provide a compact, complete educational explanation of the optimization lifecycle based on these verified facts:\n\n{prompt_text}"}
        ]

        # Primary Inference Path: Official Hugging Face InferenceClient
        if InferenceClient is not None:
            try:
                client = InferenceClient(
                    token=self.token,
                    provider=self.provider,
                    timeout=self.timeout
                )
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=700
                )
                if resp.choices and len(resp.choices) > 0:
                    content = resp.choices[0].message.content
                    if content and content.strip():
                        return content.strip()
            except HfHubHTTPError as e:
                status = e.response.status_code if getattr(e, "response", None) is not None else None
                if status in (401, 403):
                    return "Authentication Error: The configured Hugging Face token (HF_TOKEN) is invalid or unauthorized."
                elif status == 429:
                    return "Rate Limit Exceeded: Hugging Face API rate limit reached. Please wait a moment before trying again."
                elif status == 503:
                    return "Model Loading: The Hugging Face inference model is currently initializing. Please retry in a few moments."
                else:
                    return "AI explanation service is temporarily unavailable. Deterministic optimization analysis is still available."
            except (requests.exceptions.Timeout, TimeoutError):
                return "Request Timeout: The Hugging Face inference service did not respond within the timeout window. Please retry."
            except (requests.exceptions.ConnectionError, requests.exceptions.RequestException, Exception) as e:
                # Log safe diagnostic internally without leaking token
                logger.debug("InferenceClient request failed: %s", type(e).__name__)

        # Secondary / Fallback Inference Path: OpenAI-compatible router endpoint
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
            router_payload = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 700
            }

            resp = requests.post(self.router_url, headers=headers, json=router_payload, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                if "choices" in data and len(data["choices"]) > 0:
                    content = data["choices"][0]["message"]["content"]
                    if content and content.strip():
                        return content.strip()

            if resp.status_code in (401, 403):
                return "Authentication Error: The configured Hugging Face token (HF_TOKEN) is invalid or unauthorized."
            elif resp.status_code == 429:
                return "Rate Limit Exceeded: Hugging Face API rate limit reached. Please wait a moment before trying again."
            elif resp.status_code == 503:
                return "Model Loading: The Hugging Face inference model is currently initializing. Please retry in a few moments."
            else:
                return "AI explanation service is temporarily unavailable. Deterministic optimization analysis is still available."

        except requests.exceptions.Timeout:
            return "Request Timeout: The Hugging Face inference service did not respond within the timeout window. Please retry."
        except Exception:
            return "AI explanation service is temporarily unavailable. Deterministic optimization analysis is still available."
