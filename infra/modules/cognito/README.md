# cognito

User pool `rw-users` (email sign-in, admin-created users only, 12+ character passwords, optional
TOTP MFA), a public SPA client (authorization code flow, no secret) and a hosted UI domain
`rw-<8 hex>` derived from the account ID (stable across applies, no random provider).

Callback and logout URLs need the CloudFront domain, which exists only after the `edge` stack.
Sprint 2 applies with a placeholder first, then again with the real domain (two-pass apply,
sprint-1.md section 12).

Outputs: `user_pool_id`, `user_pool_arn`, `client_id`, `issuer_url`, `domain`.
