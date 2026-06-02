"""QA capabilities — profiler, differ, validator, lineage, pr-impact, test-gen.

All capabilities call into `touchstone.security.gateway.Gateway.execute()`.
Direct connector imports are forbidden (enforced by Ruff custom rule + integration test).
"""

from touchstone.qa.differ import diff_environments
from touchstone.qa.lineage import explain_lineage
from touchstone.qa.pr import analyze_pr_data_impact
from touchstone.qa.profiler import profile_table
from touchstone.qa.test_gen import generate_test_cases
from touchstone.qa.validator import check_data_quality

__all__ = [
    "analyze_pr_data_impact",
    "check_data_quality",
    "diff_environments",
    "explain_lineage",
    "generate_test_cases",
    "profile_table",
]
