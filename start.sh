#!/bin/bash
# Launch a checkout or installed deployment with explicit Compose options.
set -euo pipefail

directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
backup=none
foreground=false
pull=false
project=
usage() {
    cat <<'HELP'
Usage: bash start.sh [OPTIONS]

  --backup MODE      none (default), s3, or local
  --foreground       Attach to logs instead of starting in the background
  --pull             Pull images before starting
  --project-name NAME  Override the Compose project name
  -h, --help         Show this help

Keep this script beside compose.yaml and configure .env there before starting.
HELP
}
fail() { printf '%s\n' "$1" >&2; exit 1; }
while (( $# )); do
    case "$1" in
        --backup|--project-name)
            (( $# >= 2 )) && [[ -n $2 && $2 != --* ]] || fail "Missing value for $1"
            case "$1" in
                --backup) backup=$2 ;;
                --project-name) project=$2 ;;
            esac
            shift 2 ;;
        --foreground) foreground=true; shift ;;
        --pull) pull=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unknown option: $1 (use --help)" ;;
    esac
done
case "$backup" in none|s3|local) ;; *) fail 'Backup mode must be none, s3, or local' ;; esac
cd -- "$directory"
[[ -f .env ]] || fail 'Configure .env in the deployment directory before starting.'
command=(docker compose --project-directory "$PWD" --env-file "$PWD/.env")
[[ -z $project ]] || command+=(--project-name "$project")
files=(compose.yaml)
case "$backup" in
    s3) files+=(compose.backup.yaml) ;;
    local) files+=(compose.backup-local.yaml) ;;
esac
for file in "${files[@]}"; do
    [[ -f $file ]] || fail "Required Compose file is missing: $PWD/$file"
    command+=(-f "$PWD/$file")
done
command+=(up)
if ! $foreground; then command+=(-d); fi
if $pull; then command+=(--pull always); fi
exec "${command[@]}"
