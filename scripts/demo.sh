#!/usr/bin/env bash
# The demo story against a running, freshly seeded API (ids from scripts/seed.py):
#   1 book brake pads   2 read it   3 same vehicle again (vehicle_busy)
#   4 two EV checks at once (no_technician)   5 cancel step 1   6 rebook its slot
# Needs curl, jq and GNU date. BASE_URL defaults to http://localhost:8000.
# START (UTC, e.g. 2026-11-10T10:00:00Z) defaults to the next weekday at least 2 days
# ahead, 10:00 Europe/London.
set -euo pipefail
source "$(dirname "$0")/lib/pretty.sh"

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
  printf '  %s%-6s %s%s  →  %s\n' "$CYAN" "$method" "$path" "$RESET" "$(status_text "$status")"
  if [[ "$status" != "$expected" ]]; then
    fail "expected $expected"
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

show_booking() {  # the fields of an appointment in $RESPONSE
  kv appointment "#$(jq -r .id "$RESPONSE")  ($(jq -r .status "$RESPONSE"))"
  kv bay "$(jq -r .bay.name "$RESPONSE")"
  kv technician "$(jq -r .technician.name "$RESPONSE")"
  kv start "$(local_time "$(jq -r .start "$RESPONSE")") (London)"
  kv "work ends" "$(local_time "$(jq -r .end "$RESPONSE")")"
  kv "bay free" "$(local_time "$(jq -r .reserved_until "$RESPONSE")")  (after the cleanup buffer)"
}

show_conflict() {  # the reason of a problem+json 409 in $RESPONSE
  kv reason "$BOLD$(jq -r .reason "$RESPONSE")$RESET"
  if [[ $(jq -r '.appointment_id // empty' "$RESPONSE") ]]; then
    kv "clashes with" "appointment #$(jq -r .appointment_id "$RESPONSE")"
  fi
  kv detail "$(jq -r .detail "$RESPONSE")"
}

title "Unified Service Scheduler: the booking story"
curl -s -f -o /dev/null --retry 30 --retry-all-errors --retry-delay 1 "$BASE_URL/health"
note "API $BASE_URL is up. Slot: $(local_time "$START") London ($START)"

heading "1. A customer books brake pads for vehicle 1"
note "The API picks a free lift and a qualified technician (brake pads need the general skill) for the whole job."
call 201 POST /appointments "$(booking $BRAKES 1 1 "$START")" -H 'X-Request-ID: demo-step-1'
show_booking
kv "request id" "$(grep -i '^x-request-id' "$HEADERS" | cut -d' ' -f2 | tr -d '\r')"
FIRST_ID=$(jq -r .id "$RESPONSE")
FIRST_BAY=$(jq -r .bay.id "$RESPONSE")
FIRST_TECH=$(jq -r .technician.id "$RESPONSE")

heading "2. Read the booking back"
call 200 GET "/appointments/$FIRST_ID" ""
show_booking

heading "3. The same vehicle, 30 minutes later: an oil change"
note "The car is already on a lift then, so this must be refused."
call 409 POST /appointments "$(booking $OIL 1 1 "$START_PLUS_30")"
show_conflict
if [[ $(jq -r .reason "$RESPONSE") != vehicle_busy || $(jq -r .appointment_id "$RESPONSE") != "$FIRST_ID" ]]; then
  fail "expected vehicle_busy with appointment_id $FIRST_ID"; exit 1
fi

heading "4. Two EV battery checks at the same time"
note "Only one technician (Chloe) has the EV high-voltage skill."
call 201 POST /appointments "$(booking $EV 2 1 "$START")"
show_booking
call 409 POST /appointments "$(booking $EV 3 2 "$START")"
show_conflict
if [[ $(jq -r .reason "$RESPONSE") != no_technician ]]; then
  fail "expected no_technician"; exit 1
fi

heading "5. Cancel the brake booking #$FIRST_ID"
call 200 POST "/appointments/$FIRST_ID/cancel" ""
kv appointment "#$(jq -r .id "$RESPONSE")  ($(jq -r .status "$RESPONSE"))"
if [[ $(jq -r .status "$RESPONSE") != cancelled ]]; then fail "expected cancelled"; exit 1; fi

heading "6. Book brake pads at the same time again"
note "The cancelled booking freed its lift and technician, so the new one gets them."
call 201 POST /appointments "$(booking $BRAKES 1 1 "$START")"
show_booking
if [[ $(jq -r .bay.id "$RESPONSE") != "$FIRST_BAY" || $(jq -r .technician.id "$RESPONSE") != "$FIRST_TECH" ]]; then
  fail "expected the same bay and technician as step 1"; exit 1
fi

heading "Health and metrics"
call 200 GET /ready ""
curl -s "$BASE_URL/metrics" \
  | awk '/^appointments_booked_total/ {b += $2}
         /^appointments_cancelled_total/ {c += $2}
         /^booking_rejected_total/ {match($0, /reason="[^"]*"/); r[substr($0, RSTART + 8, RLENGTH - 9)] += $2}
         END {printf "%s\t%d\n%s\t%d\n", "booked", b, "cancelled", c
              for (k in r) printf "rejected\t%d × %s\n", r[k], k}' \
  | while IFS=$'\t' read -r name value; do kv "$name" "$value"; done || fail "metrics unavailable"

printf '\n'
ok "Demo finished: every step returned the expected status."
