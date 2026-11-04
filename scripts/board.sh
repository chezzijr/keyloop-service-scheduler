#!/usr/bin/env bash
# The workshop board: every appointment in the database, read directly with psql, with
# names instead of ids (vehicles keep their id, as race.sh prints it) and times in each dealership's local time zone.
#   start      booked start
#   work_ends  planned end of the work
#   bay_free   end of the buffer: the bay and technician are reserved until here
# Cancelled rows stay listed with their status; they no longer block their slot.
# Needs docker compose with the db service running.
set -euo pipefail
cd "$(dirname "$0")/.."  # docker compose needs the repo root
source scripts/lib/pretty.sh

title "Workshop board: every appointment in the database"
note "Times are local to the dealership. bay_free = end of work + cleanup buffer."
echo

docker compose exec -T db psql -U scheduler -d scheduler -P footer=off -P border=2 -P linestyle=unicode -c "
SELECT a.id,
       a.status,
       to_char(lower(a.during) AT TIME ZONE d.timezone, 'Dy DD Mon HH24:MI') AS start,
       to_char(a.ends_at AT TIME ZONE d.timezone, 'HH24:MI') AS work_ends,
       to_char(upper(a.during) AT TIME ZONE d.timezone, 'HH24:MI') AS bay_free,
       b.name AS bay,
       t.name AS technician,
       s.name AS service,
       '#' || v.id AS vehicle
FROM appointment a
JOIN dealership d ON d.id = a.dealership_id
JOIN service_bay b ON b.id = a.service_bay_id
JOIN technician t ON t.id = a.technician_id
JOIN service_type s ON s.id = a.service_type_id
JOIN vehicle v ON v.id = a.vehicle_id
ORDER BY lower(a.during), b.name, a.id" \
  | sed -e "/ cancelled /s/.*/$DIM&$RESET/" -e "s/ confirmed / $GREEN""confirmed$RESET /"
