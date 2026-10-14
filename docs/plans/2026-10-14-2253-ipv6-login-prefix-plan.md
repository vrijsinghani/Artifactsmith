# IPv6 login buckets

Failed My pages sign-ins are counted by the address `client_ip` returns. An IPv6 client can change the lower 64 bits and get a new bucket.

Group IPv6 by `/64`. Keep each IPv4 address as its own bucket. Treat an IPv4-mapped IPv6 address (`::ffff:a.b.c.d`) as that IPv4 address. A value that is not an IP stays the raw peer string.

Apply the grouping only to the rate-limit key, after the trusted-proxy decision. The trust check still uses the real peer address.

The stored key is the network address plus `/64` (`2001:db8:1:2::/64`). The interface id is not stored.
