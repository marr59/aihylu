---
name: "deploy-site"
description: "Deploy a static or containerised site to a remote host: build, sync, restart, verify. Use when asked to ship, deploy, or roll back a site."
---

# Deploy Site

A skill is a markdown manifest the agent loads **on demand**. The front-matter
`description` is the only part that stays resident in context — the body is read
when the task actually matches. This keeps dozens of capabilities available at
near-zero token cost.

## When to use

- "deploy / ship / push live" for a known project
- a build is green and should go out
- a rollback is requested

Do **not** use for: first-time provisioning, DNS changes, certificate issuance.

## Inputs

Read from environment, never hardcoded:

| Variable | Meaning |
| --- | --- |
| `DEPLOY_HOST` | target host, e.g. `YOUR_VPS_IP` |
| `DEPLOY_USER` | ssh user |
| `DEPLOY_SSH_KEY` | path to private key |

## Flow

```
1. Confirm the target        → which project, which host, prod or staging
2. Build locally             → fail fast; never sync a broken build
3. Dry-run the sync          → rsync --dry-run, show the file delta
4. Ask before mutating prod  → external, hard to reverse → explicit go/no-go
5. Sync                      → rsync over ssh
6. Fix permissions           → ownership resets after sync; always re-apply
7. Restart the service       → systemd unit or container
8. Verify                    → curl the health endpoint, check status code
9. Report                    → what changed, what the health check returned
```

## Commands

```bash
# 3 — preview
rsync -avzn --delete ./dist/ "$DEPLOY_USER@$DEPLOY_HOST:/var/www/example/"

# 5 — sync
rsync -avz --delete -e "ssh -i $DEPLOY_SSH_KEY" \
      ./dist/ "$DEPLOY_USER@$DEPLOY_HOST:/var/www/example/"

# 6 — permissions (skipping this is the single most common breakage)
ssh -i "$DEPLOY_SSH_KEY" "$DEPLOY_USER@$DEPLOY_HOST" \
    "chown -R www-data:www-data /var/www/example && chmod -R 755 /var/www/example"

# 7-8 — restart and verify
ssh -i "$DEPLOY_SSH_KEY" "$DEPLOY_USER@$DEPLOY_HOST" "systemctl restart example.service"
curl -s -o /dev/null -w '%{http_code}' https://example.com/health
```

## Guardrails

- **Never** deploy without a passing local build.
- **Never** run `--delete` against prod without showing the dry-run first.
- Writing to a live host is an outward-facing action — confirm, then act.
- If the health check is not `200`, roll back immediately and report; do not
  "wait and see".

## Rollback

```bash
ssh … "ln -sfn /var/www/example-releases/<previous> /var/www/example && systemctl restart example.service"
```
