# Finding out why deployment is blocked

Open the failed check from the pull request, then open the workflow run's **Summary**.

Security checks publish **Security findings and deployment blockers** with check outcomes, Gitleaks rule/file/line/commit/fingerprint details and Trivy vulnerability IDs, affected package versions and fixes or configuration resolutions. File links open the detected historical commit. A history finding may already be removed from current source; removal does not revoke a genuine credential.

Download **security-diagnostics-RUN_ID-ATTEMPT** under **Artifacts** for the readable Markdown and complete metadata-only JSON. Secret values and matching source snippets are omitted. Reports remain available for 30 days. No raw scanner report or raw CI log is uploaded by this reporting change.

Gitleaks findings use exit 42. Other nonzero Gitleaks exits are operational scanner errors, not evidence that secrets were found. Missing or invalid reports are described explicitly. Trivy, Gitleaks and workflow lint all produce results before the final gate enforces their failures; no failed required scanner becomes a green check.

For a genuine secret, revoke/rotate with its provider and remove it from current source. For a false positive, propose a narrow rule/fingerprint exception with evidence and a recorded reason. A match alone is not a confirmed leak; do not broadly disable scanning.

After **Deployment failure diagnostics** is merged to the default branch, failed/cancelled GitHub Actions workflows produce a separate summary listing the exact job and failed step, linked logs and diagnostic artifacts. It uses read-only access and default-branch reporting code; it never downloads or executes failed-branch code, artifacts or logs. It cannot see external-provider checks that have no GitHub Actions run; open those check's Details link directly.

Use the diagnostics workflow's **Run workflow** form with an existing run ID to investigate an older failure. The report records the source run's latest attempt and head commit. Original failed check conclusions and protection rules remain enforced.
