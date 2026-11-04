# Shared terminal formatting for the demo scripts (source it, don't run it).
# Colours only when stdout is a terminal and NO_COLOR is unset.

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  BOLD=$'\e[1m' DIM=$'\e[2m' RED=$'\e[31m' GREEN=$'\e[32m' YELLOW=$'\e[33m' CYAN=$'\e[36m'
  RESET=$'\e[0m'
else
  BOLD='' DIM='' RED='' GREEN='' YELLOW='' CYAN='' RESET=''
fi

title() {  # big banner at the start of a script
  local line
  line=$(printf '═%.0s' $(seq 1 72))
  printf '\n%s%s\n  %s\n%s%s\n' "$BOLD$CYAN" "$line" "$1" "$line" "$RESET"
}

heading() {  # one step or section
  printf '\n%s▶ %s%s\n' "$BOLD" "$1" "$RESET"
}

note() {  # grey explanation under a heading
  printf '  %s%s%s\n' "$DIM" "$1" "$RESET"
}

status_text() {  # HTTP code [width] -> coloured "201 Created", name padded to width
  local code=$1 width=${2:-0} name colour
  case $code in
    200) name=OK ;; 201) name=Created ;; 404) name='Not Found' ;; 409) name=Conflict ;;
    422) name='Unprocessable Content' ;; 503) name='Service Unavailable' ;; *) name='' ;;
  esac
  case $code in 2*) colour=$GREEN ;; 4*) colour=$YELLOW ;; *) colour=$RED ;; esac
  printf '%s%s%s %-*s%s' "$BOLD" "$colour" "$code" "$width" "$name" "$RESET"
}

kv() {  # aligned "key   value" line
  printf '    %s%-15s%s %s\n' "$DIM" "$1" "$RESET" "$2"
}

ok() { printf '  %s✔ %s%s\n' "$GREEN" "$1" "$RESET"; }
fail() { printf '  %s✘ %s%s\n' "$RED" "$1" "$RESET" >&2; }

local_time() {  # UTC ISO time -> "Thu 05 Nov 10:00" in Europe/London (the seed dealership)
  TZ=Europe/London date -d "$1" '+%a %d %b %H:%M'
}
