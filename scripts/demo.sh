#!/usr/bin/env bash
# The demo story against a running, freshly seeded API (ids from scripts/seed.py):
#   1 book brake pads   2 read it   3 same vehicle again (vehicle_busy)
#   4 two EV checks at once (no_technician)   5 cancel step 1   6 rebook its slot
# Needs curl, jq and GNU date. BASE_URL defaults to http://localhost:8000.
# START (UTC, e.g. 2026-11-10T10:00:00Z) defaults to the next weekday at least 2 days
# ahead, 10:00 Europe/London.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
OIL=1 BRAKES=3 EV=6

if [[ -z "${START:-}" ]]; then
  for d in 2 3 4 5 6; do
    day=$(TZ=Europe/London date -d "+$d days" +%F)
    if [[ $(date -d "$day" +%u) -le 5 ]]; then break; fi
  done
  START=$(date -u -d "TZ=\"Europe/London\" $day 10:00" +%Y-%m-%dT%H:%M:%SZ)
fi
START_PLUS_30=$(date -u -d "@$(( $(date -d "$START" +%s) + 1800 ))" +%Y-%m-%dT%H:%M:%SZ)

RESPONSE=$(mktemp)
HEADERS=$(mktemp)
trap 'rm -f "$RESPONSE" "$HEADERS"' EXIT

# call EXPECTED_STATUS METHOD PATH JSON_BODY|"" [extra curl args...]: prints the status,
# exits 1 if it is not the expected one. Body in $RESPONSE, headers in $HEADERS.
call() {
  local expected=$1 method=$2 path=$3 data=$4
  shift 4
  local args=(-s -o "$RESPONSE" -D "$HEADERS" -w '%{http_code}' -X "$method" "$@")
  if [[ -n "$data" ]]; then args+=(-H 'Content-Type: application/json' -d "$data"); fi
  local status
  status=$(curl "${args[@]}" "$BASE_URL$path")
  echo "   HTTP $status (expected $expected)"
  if [[ "$status" != "$expected" ]]; then
    jq . "$RESPONSE" 2>/dev/null || cat "$RESPONSE"
    if [[ "$path" == /appointments && "$status" == 409 ]]; then
      echo "The demo slot $START is already taken (the demo was run before on this data)." >&2
      echo "Reset: docker compose down -v && docker compose up -d --build && docker compose run --rm seed" >&2
      echo "Or pick another slot: START=<future weekday, 15-min grid, UTC> $0" >&2
    fi
    exit 1
  fi
}

booking() {  # service_type_id vehicle_id customer_id start
  jq -nc --argjson s "$1" --argjson v "$2" --argjson c "$3" --arg t "$4" \
    '{customer_id: $c, vehicle_id: $v, dealership_id: 1, service_type_id: $s, start: $t}'
}

echo "Waiting for $BASE_URL/health"
curl -s -f -o /dev/null --retry 30 --retry-all-errors --retry-delay 1 "$BASE_URL/health"
echo "Slot: $START"

echo
echo "1. Book brake pads for vehicle 1 (sends X-Request-ID: demo-step-1)"
call 201 POST /appointments "$(booking $BRAKES 1 1 "$START")" -H 'X-Request-ID: demo-step-1'
echo "   $(grep -i '^x-request-id' "$HEADERS" | tr -d '\r')"
jq -c '{id, status, start, end, reserved_until, bay: .bay.name, technician: .technician.name}' "$RESPONSE"
FIRST_ID=$(jq -r .id "$RESPONSE")
FIRST_BAY=$(jq -r .bay.id "$RESPONSE")
FIRST_TECH=$(jq -r .technician.id "$RESPONSE")

echo
echo "2. Read appointment $FIRST_ID"
call 200 GET "/appointments/$FIRST_ID" ""
jq -c '{id, status, bay: .bay.name, technician: .technician.name}' "$RESPONSE"

echo
echo "3. Book an oil change for vehicle 1 at $START_PLUS_30 (overlaps step 1)"
call 409 POST /appointments "$(booking $OIL 1 1 "$START_PLUS_30")"
jq -c '{reason, appointment_id, detail}' "$RESPONSE"
if [[ $(jq -r .reason "$RESPONSE") != vehicle_busy || $(jq -r .appointment_id "$RESPONSE") != "$FIRST_ID" ]]; then
  echo "expected vehicle_busy with appointment_id $FIRST_ID" >&2; exit 1
fi

echo
echo "4a. EV battery check for vehicle 2 at $START"
call 201 POST /appointments "$(booking $EV 2 1 "$START")"
jq -c '{id, bay: .bay.name, technician: .technician.name}' "$RESPONSE"
echo "4b. EV battery check for vehicle 3 at the same time (Chloe is the only EV technician)"
call 409 POST /appointments "$(booking $EV 3 2 "$START")"
jq -c '{reason, detail}' "$RESPONSE"
if [[ $(jq -r .reason "$RESPONSE") != no_technician ]]; then
  echo "expected no_technician" >&2; exit 1
fi

echo
echo "5. Cancel appointment $FIRST_ID"
call 200 POST "/appointments/$FIRST_ID/cancel" ""
jq -c '{id, status}' "$RESPONSE"
if [[ $(jq -r .status "$RESPONSE") != cancelled ]]; then echo "expected cancelled" >&2; exit 1; fi

echo
echo "6. Book brake pads for vehicle 1 at $START again (the slot is free now)"
call 201 POST /appointments "$(booking $BRAKES 1 1 "$START")"
jq -c '{id, status, bay: .bay.name, technician: .technician.name}' "$RESPONSE"
if [[ $(jq -r .bay.id "$RESPONSE") != "$FIRST_BAY" || $(jq -r .technician.id "$RESPONSE") != "$FIRST_TECH" ]]; then
  echo "expected the same bay and technician as step 1" >&2; exit 1
fi

echo
echo "Readiness and metrics"
call 200 GET /ready ""
jq -c . "$RESPONSE"
curl -s "$BASE_URL/metrics" | grep -E '^appointments_(booked|cancelled)_total|^booking_rejected_total' || true

echo
echo "Demo finished: every step returned the expected status."
