# Weighted token billing

mAIvn exposes provider-neutral model tiers. The default is `auto`; the public
SDK does not choose or promise a specific provider model.

| Tier | Weighted-token multiplier |
| --- | ---: |
| Fast | 1× |
| Balanced | 3× |
| Max | 5× |
| Ultra | 10× |

Weighted usage is calculated from the quota-relevant raw token count reported
by the service:

```text
customer raw tokens × resolved tier multiplier
```

For `auto`, billing follows the tier actually served. If you explicitly select
a tier, its fixed multiplier applies. The developer portal shows both raw and
weighted usage so the calculation remains auditable.
