# stacks/edge

CloudFront `rw-cdn` (`cloudfront-site` module) in front of `rw-frontend-<acct>` (Origin Access
Control) and `/api/*` to `rw-http-api`, plus the frontend bucket policy (TLS only, reads only
from this distribution). The data stack leaves that bucket policy to this stack.

**Inputs:** none required.

**Outputs:** `cloudfront_domain`, `distribution_id`.

**Apply order:** after `data` (frontend bucket) and `serverless` (HTTP API). Then the two-pass step
(Sprint 2): put `https://<cloudfront_domain>` into `data` `allowed_origin` and `serverless`
`dashboard_url`, and apply both again so upload CORS and Cognito callbacks use the real domain.

The frontend is not built in Sprint 1. The deploy step uploads a static `index.html` ("RipWatch
dashboard coming in Sprint 3") so the stack can be tested.
