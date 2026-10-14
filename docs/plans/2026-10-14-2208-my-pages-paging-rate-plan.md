# My pages login limits, paging, and local times

## Outcome

`/app/` stays a read-only catalog on the API origin. Three follow-ups:

1. Login limits are not one global bucket when SWAG is the peer.
2. The list continues past 50 pages.
3. Times are labeled in the viewer's zone, default America/New_York. No JavaScript.

## Rate limit

`client_ip` today is the TCP peer. Behind SWAG every request is `192.168.30.160`, so `AM_LOGIN_LIMIT` is global.

- `AM_TRUSTED_PROXIES` is a comma-separated list of IPs or CIDRs. Empty means forwarded headers are ignored.
- An entry that is not an IP or CIDR fails closed when the process loads config.
- Trust `X-Real-IP`, else the rightmost `X-Forwarded-For` hop, only when the peer is in that list. A missing or invalid header uses the peer address.
- A second bucket hashes the first 12 characters of the stripped token (sha256). The raw token and the prefix are not stored. An empty or over-long token counts against the address only.
- A failed login increments both buckets. 429 when either is over its limit (`AM_LOGIN_LIMIT` and `AM_LOGIN_PREFIX_LIMIT`, both default 8, same window). Success clears both for that address and that prefix.
- CSRF failures stay uncounted.
- Existing `login_attempts` rows gain `kind` (`ip` or `prefix`) on startup. Do not drop the table.

## Paging

50 rows per page, newest first. Fetch 51 to know whether a next page exists. `page` is a query parameter. Non-integers, page < 1, and page > 200 are 400. Next and previous keep `q` and `status`.

## Times

Catalog rows keep the unix timestamp. The page formats it. Default zone is America/New_York, with the label `Times shown in America/New_York`. `?tz=` sets an HttpOnly `am_tz` cookie (path `/app`, SameSite=Strict, Secure when the session cookie is). An unknown zone falls back to New York and does not replace a valid cookie.

## Checks

Untrusted peer ignores forwarded headers. Trusted peer splits limits by `X-Real-IP` and by the rightmost forwarded hop. The prefix limit trips across different client IPs. Page 51 is on page 2. A January UTC timestamp renders as Eastern time, and `America/Los_Angeles` changes the hour. Invalid `tz` stays on New York.
