from pathlib import Path
import re


WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "inspect-monthly-governance-job.yml"
)


def test_monthly_job_inspection_runs_after_worker_window():
    source = WORKFLOW.read_text(encoding="utf-8")

    assert re.search(r'cron: "0 12 2 \\* \\*"', source)
    assert "required: false" in source
    assert 'PERIOD: ${{ inputs.period }}' in source


def test_scheduled_inspection_defaults_to_previous_completed_utc_month():
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "if not period:" in source
    assert "datetime.now(timezone.utc)" in source
    assert "now.date().replace(day=1) - timedelta(days=1)" in source
    assert 'period >= now.strftime("%Y-%m")' in source


def test_monthly_job_inspection_uses_only_get():
    source = WORKFLOW.read_text(encoding="utf-8")

    assert 'urllib.request.Request(endpoint, method="GET"' in source
    assert 'method="POST"' not in source
    assert "/v1/monthly-review/jobs/{period}" in source
