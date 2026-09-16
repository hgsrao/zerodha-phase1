# Redis Configuration — Authoritative Reference

**Date:** August 30, 2026  
**Status:** Audit-corrected (resolved port mismatch)

## Summary

All ECS system components use **Redis on port 6379** (standard Redis default).

Previous PDFs incorrectly stated port 6380. This document corrects that discrepancy.

## Configuration

```
Host:     localhost
Port:     6379  ← AUTHORITATIVE
DB:       0
Protocol: TCP
Auth:     None (local development only)
```

## Verification

Check all running code uses this configuration:

```bash
# Should show 6379 in all ECS/production files
grep -r "REDIS_PORT\|redis.Redis.*port" . --include="*.py" | grep -v test | grep -v old
```

### Files Using Correct Configuration (6379)

- ✅ `MainTradingLoop.py:64` → `REDIS_PORT = 6379`
- ✅ `Streamlit_Dashboard_Enhanced.py:53` → `port=6379`
- ✅ `ECS_TradingSupervisor_Enhanced.py` → Uses 6379
- ✅ `KiteOrderImbalanceConnector.py` → Uses 6379

### Historical Discrepancy

Earlier architecture PDFs (now retracted) incorrectly stated port 6380. This was an error in documentation, not in actual implementation. All production code has always used the correct port (6379).

## Key Channels & Schemas

### Imbalance Data

```
Key:   imbalance:{SYMBOL}
Value: {
  "value_pct": -15.3,      # Buy/sell bias percentage (-100 to +100)
  "heat": "STRONG",        # Intensity level
  "confidence": 0.87,      # Data quality (0.0 to 1.0)
  "timestamp": "2026-08-30T21:30:00Z"
}
TTL:   60 seconds (auto-expire stale data)
```

### ECS State

```
Key:   ecs:state
Value: {
  "mode": "NEUTRAL",
  "stress_factor": 0.15,
  "symbols_managed": 48,
  "current_mode": "LIVE"
}
```

### Panic Scores (Per-Symbol)

```
Key:   ecs:panic_score:{SYMBOL}
Value: 0.23  (float, 0.0 to 1.0)
```

## Redis Connection Best Practices

1. **Always use localhost** (not 0.0.0.0 or public IP in development)
2. **Check port 6379** — If connection fails, verify Redis daemon is running
3. **No authentication** — Local development only; if moving to production, add AUTH
4. **Connection pooling** — Use `@st.cache_resource` or equivalent for client reuse

## Testing Connection

```python
import redis
r = redis.Redis(host='localhost', port=6379, decode_responses=True)
try:
    r.ping()
    print("✅ Redis connected")
except Exception as e:
    print(f"❌ Redis error: {e}")
```

## Future Enhancements

If Redis is scaled to production:
- Use port 6380 or custom port (for separation)
- Add TLS/SSL encryption
- Implement authentication (ACL in Redis 6+)
- Add persistence (RDB/AOF)
- Set up clustering/replication

## References

- [[live-trading-safety-constraints]] — No real capital connected
- [[ecs-audit-findings-20260830]] — Full audit including port mismatch finding
- CRITICAL_AUDIT_RESPONSE_20260830.md — Remediation roadmap
