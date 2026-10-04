# Isolated negative control proof

These files intentionally contain vulnerable dependencies and unsafe/incorrect workflow text. They are never installed or executed. The YAML fixtures are outside `.github/workflows`. This temporary draft targets only the recovery branch, carries do-not-merge and will be closed after evidence collection. Production manifests and dependency versions are unchanged.

Lodash fixture: GHSA-35jh-r3h4-6jhm (HIGH); Django fixture: GHSA-w24h-v9qh-8gxj (CRITICAL). Dependency Review must reject these deltas. The separate proof workflow checks the direct pinned scanners reject their isolated fixtures and retains machine-readable output.
