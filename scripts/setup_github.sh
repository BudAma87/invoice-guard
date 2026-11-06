#!/usr/bin/env bash
# Creates the GitHub repo, pushes the scaffold, adds labels and protects main.
# Prereqs: git, GitHub CLI (`gh auth login` done). Usage: ./scripts/setup_github.sh [repo-name] [private|public]
set -euo pipefail

REPO="${1:-invoice-guard}"
VIS="${2:-private}"
OWNER="$(gh api user --jq .login)"

git init -b main 2>/dev/null || true
git add .
git commit -m "chore: initial scaffold for invoice-guard" || echo "nothing to commit"

gh repo create "$REPO" --"$VIS" --source=. --remote=origin --push \
  --description "AI-powered invoice validation: OCR, LangGraph + Claude extraction, rule validation, human review, exactly-once ERP posting"

# Labels
for l in "rules:d93f0b" "llm:5319e7" "ocr:0e8a16" "ui:1d76db" "erp:fbca04" "eval:c5def5"; do
  gh label create "${l%%:*}" --color "${l##*:}" --repo "$OWNER/$REPO" --force >/dev/null
done

# Branch protection (private repos need GitHub Pro/Team for this; skipped on failure)
gh api -X PUT "repos/$OWNER/$REPO/branches/main/protection" \
  -H "Accept: application/vnd.github+json" \
  --input - <<JSON || echo "Branch protection skipped (plan does not support it)."
{
  "required_status_checks": { "strict": true, "contexts": ["backend"] },
  "enforce_admins": false,
  "required_pull_request_reviews": { "required_approving_review_count": 1 },
  "restrictions": null
}
JSON

sed -i "s/@your-github-username/@$OWNER/g" .github/CODEOWNERS
git commit -am "chore: set CODEOWNERS" && git push

echo "Done: https://github.com/$OWNER/$REPO"
