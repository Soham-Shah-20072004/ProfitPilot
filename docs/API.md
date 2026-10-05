# API examples

Base URL when running locally: `http://localhost:8000/api/v1`. Everything below can also be tried from the interactive docs at `http://localhost:8000/docs`.

The demo seller is `demo` (send `X-Seller-Id: <id>` to act as another seller). If the server was started with `PP_API_KEY`, add `X-API-Key: <key>` to every write request.

## 1. Floor and first price for a new product (no account needed)

```bash
curl -s localhost:8000/api/v1/first-price -H 'Content-Type: application/json' \
  -d '{"category":"ethnic","cs":180,"pack":10,"fwd":25,"target":60,"ret":12,"rto":8,
       "band":[329,429],"median":379,"lookalikes":24,"rival_price":299}'
```

Returns the floor (₹309), the safety margin (₹18 → safe minimum ₹327), the start price (₹369), the no-return price (₹339), the price for each goal mode, the market check, and the Why.

## 2. This week's cards and a YES

```bash
curl -s localhost:8000/api/v1/recommendations          # one card per product
curl -s localhost:8000/api/v1/decisions -H 'Content-Type: application/json' \
  -d '{"key":"kurti:growth:369>384:up","decision":"y","product_id":"kurti","kind":"up","from_price":369,"to_price":384}'
```

The server re-runs the guardrails (floor, step ≤ 8%, 7-day cooldown, ≤ 2 moves a month, views sanity check). If one fails you get `409` with the list of checks:

```json
{"detail": {"message": "blocked by guardrails: Cooldown 7 days", "checks": [{"k": "Floor", "ok": true, "d": "₹399 ≥ floor ₹309"}, ...]}}
```

Undo within 24 hours: `POST /decisions/kurti:growth:369%3E384:up/undo` (URL-encode the key).

## 3. Time travel and the day-14 / day-28 checks (demo clock)

```bash
curl -s -X POST localhost:8000/api/v1/admin/clock/advance -H 'Content-Type: application/json' -d '{"days":14}'
curl -s localhost:8000/api/v1/jobs
```

Day 14 judges the move on orders (profit per impression before vs after; reverts automatically if it fell). Day 28 confirms it on kept orders. Without posted metrics the checks use the demand model and say so in `basis`.

## 4. Lifecycle and the rival test

```bash
curl -s localhost:8000/api/v1/products/kurti/lifecycle
```

`rival_test`: hold ₹384 → 14 orders/day → ₹819/day; match ₹369 → 14 × (384/369)³ = 15.8 orders/day → ₹739/day → `"decision": "hold"`.

## 5. Weekly metrics (the production data entry point)

```bash
curl -s localhost:8000/api/v1/products/vase/metrics -H 'Content-Type: application/json' -d '{"weeks":[
 {"week_start":"2026-08-03","impressions":4000,"clicks":160,"orders":20,"kept":100}, ... 8 weeks ...]}'
```

With 8 or more weeks the stage is re-classified from kept units (last 4 weeks ÷ the 4 before).

## 6. Diagnose before discount

```bash
curl -s -X POST localhost:8000/api/v1/diagnose/cases/conv/run      # demo case
curl -s localhost:8000/api/v1/diagnose -H 'Content-Type: application/json' -d '{
  "product_id":"kurti","price":369,"band":[329,429],"stock_days":35,"delivery_days":4.5,
  "weeks":[{"impressions":8000,"clicks":328,"orders":4,"returns_pct":12,"rto_pct":8},
           {"impressions":8000,"clicks":328,"orders":4,"returns_pct":12,"rto_pct":8}]}'
```

## 7. Price search

Demo (simulated buyers):

```bash
curl -s localhost:8000/api/v1/experiments -H 'Content-Type: application/json' -d '{"product_id":"kurti","seed":1}'
curl -s localhost:8000/api/v1/experiments/1/simulate -H 'Content-Type: application/json' -d '{"days":30}'
```

Live (real traffic): create with `"kind":"live"`, then every morning `POST /experiments/{id}/choose` (today's menu for every buyer) and every evening `POST /experiments/{id}/outcomes` with `{"price":369,"impressions":4500,"kept":17}`.

## 8. Pilot, 2.0 modules, coach

```bash
curl -s localhost:8000/api/v1/pilot/sample-size -H 'Content-Type: application/json' -d '{"sigma":40,"delta":10}'   # 251 sellers per group
curl -s localhost:8000/api/v1/pilot/impact
curl -s localhost:8000/api/v1/v2/pool -H 'Content-Type: application/json' -d '{}'      # 300 units ≥ 300 → ₹188
curl -s localhost:8000/api/v1/v2/credit -H 'Content-Type: application/json' -d '{}'    # ≈ ₹34,000
curl -s localhost:8000/api/v1/coach/ask -H 'Content-Type: application/json' -d '{"text":"mera profit kitna hai","product_id":"kurti"}'
```
