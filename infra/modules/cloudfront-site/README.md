# cloudfront-site

Distribution `rw-cdn`: the dashboard from the private `rw-frontend` bucket through Origin Access
Control (`CachingOptimized`), `/api/*` to the HTTP API (`CachingDisabled`,
`AllViewerExceptHostHeader`, all methods), HTTPS redirect, managed security headers, and SPA
fallback (403 and 404 serve `/index.html`).

The bucket policy that lets this distribution read `rw-frontend` lives in the `edge` stack, using
the `arn` output in an `AWS:SourceArn` condition.

Notes:
- Default CloudFront certificate (no custom domain, north star 22), so a minimum TLS version
  cannot be pinned; CloudFront still serves TLS 1.2+ to modern browsers.
- The SPA fallback also turns API 403 and 404 responses into `index.html` with status 200. The
  dashboard must check the `content-type` of `/api/*` responses (Sprint 2 frontend).

Outputs: `domain_name`, `distribution_id`, `arn`.
