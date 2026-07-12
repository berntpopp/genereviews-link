# Security Policy

`genereviews-link` is a research tool that federates NCBI GeneReviews content.
It is **not** clinical decision support. Do not submit identifiable patient data
to public instances.

## Reporting a vulnerability

Please report suspected vulnerabilities privately to the maintainer
(bernt.popp@charite.de) or via GitHub's private "Report a vulnerability" flow on
the repository's **Security** tab. Do not open a public issue for an unfixed
vulnerability.

## Required repository settings

These are **repository settings** (not code) and must be enabled by an operator
with admin rights. CI already runs CodeQL (`.github/workflows/security.yml`) and
Dependabot (`.github/dependabot.yml`); the settings below are the remaining
GitHub-native controls.

### Secret scanning & push protection (F-18)

Enable GitHub secret scanning and push protection so committed/pushed
credentials are detected and blocked at push time.

Operator command (run once; **not** run by CI or this project's code):

```bash
gh api -X PATCH repos/berntpopp/genereviews-link \
  -f 'security_and_analysis[secret_scanning][status]=enabled' \
  -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled'
```

Verify:

```bash
gh api repos/berntpopp/genereviews-link --jq '.security_and_analysis'
# expect: secret_scanning.status == "enabled"
#         secret_scanning_push_protection.status == "enabled"
```

(Equivalent UI path: **Settings -> Code security and analysis -> Secret
scanning / Push protection -> Enable**.)

## Deployment hardening

The backend is unauthenticated by design and MUST be reachable only through the
GeneFoundry router / reverse proxy, never published directly. See
`docker/README.md` and the fleet container-hardening standard for the
non-root / read-only-rootfs / `cap_drop: ALL` / resource-limit baseline.
