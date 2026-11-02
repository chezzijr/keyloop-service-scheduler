#!/usr/bin/env bash
# Double-booking under load, against a running, freshly seeded API (ids from scripts/seed.py).
#
# Sends N oil-change bookings at the same moment, one per fleet vehicle (5-14). An oil change
# needs a lift and a technician with the general skill: the seed has 3 lifts and 4 such
# technicians, so exactly 3 can be booked for one slot. Expect 3 x 201 and N-3 x 409, then
# ask Postgres directly whether any bay or technician is double-booked (expect 0).
#
# Needs curl, jq, GNU date, and docker compose for the SQL check.
# BASE_URL defaults to http://localhost:8000. START (UTC, e.g. 2026-11-10T14:00:00Z) defaults
# to the next weekday at least 2 days ahead, 14:00 Europe/London (demo.sh uses 10:00).
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
OIL=1 FLEET_CUSTOMER=4 FIRST_VEHICLE=5 N=10 CAPACITY=3

if [[ -z "${START:-}" ]]; then
  for d in 2 3 4 5 6; do
    day=$(TZ=Europe/London date -d "+$d days" +%F)
    if [[ $(date -d "$day" +%u) -le 5 ]]; then break; fi
  done
  START=$(date -u -d "TZ=\"Europe/London\" $day 14:00" +%Y-%m-%dT%H:%M:%SZ)
fi

OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT

curl -s -f -o /dev/null --retry 30 --retry-all-errors --retry-delay 1 "$BASE_URL/health"
echo "Slot: $START. Sending $N oil-change bookings at once (capacity: $CAPACITY lifts)."

for i in $(seq 0 $((N - 1))); do
  vehicle=$((FIRST_VEHICLE + i))
  body=$(jq -nc --argjson v "$vehicle" --argjson c "$FLEET_CUSTOMER" --argjson s "$OIL" --arg t "$START" \
    '{customer_id: $c, vehicle_id: $v, dealership_id: 1, service_type_id: $s, start: $t}')
  curl -s -o "$OUT/$vehicle.json" -w '%{http_code}' -H 'Content-Type: application/json' \
    -d "$body" "$BASE_URL/appointments" > "$OUT/$vehicle.status" &
done
wait

echo
printf '%-8s %-6s %s\n' vehicle status result
booked=0 rejected=0
for i in $(seq 0 $((N - 1))); do
  vehicle=$((FIRST_VEHICLE + i))
  status=$(cat "$OUT/$vehicle.status")
  if [[ "$status" == 201 ]]; then
    booked=$((booked + 1))
    result=$(jq -r '"booked: \(.bay.name), \(.technician.name)"' "$OUT/$vehicle.json")
  else
    if [[ "$status" == 409 ]]; then rejected=$((rejected + 1)); fi
    result=$(jq -r '.reason // .title' "$OUT/$vehicle.json" 2>/dev/null || echo "?")
  fi
  printf '%-8s %-6s %s\n' "$vehicle" "$status" "$result"
done

echo
echo "201 booked: $booked (expected $CAPACITY)   409 rejected: $rejected (expected $((N - CAPACITY)))"

overlaps=$(docker compose exec -T db psql -U scheduler -d scheduler -Atc "
  SELECT count(*) FROM appointment a JOIN appointment b ON a.id < b.id
  WHERE a.status <> 'cancelled' AND b.status <> 'cancelled'
    AND (a.service_bay_id = b.service_bay_id OR a.technician_id = b.technician_id)
    AND a.during && b.during")
echo "Pairs of active appointments sharing a bay or technician at overlapping times: $overlaps (expected 0)"

if [[ "$booked" != "$CAPACITY" || "$rejected" != $((N - CAPACITY)) || "$overlaps" != 0 ]]; then
  if [[ "$booked" == 0 ]] && grep -q vehicle_busy "$OUT"/*.json; then
    echo "The fleet vehicles are already booked at $START (race.sh was run before on this data)." >&2
    echo "Reset: docker compose down -v && docker compose up -d --build && docker compose run --rm seed" >&2
    echo "Or pick another slot: START=<future weekday, 15-min grid, UTC> $0" >&2
  fi
  exit 1
fi
echo "Race finished: exactly $CAPACITY bookings, no double-booking."
