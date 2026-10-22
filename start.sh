#!/bin/bash
# Launch a checkout or installed deployment with explicit Compose options.
set -euo pipefail

directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
backup=none
foreground=false
pull=false
project=
action=start
archive=
volume=
caller_directory=$PWD
if (( $# )) && [[ $1 != -* ]]; then action=$1; shift; fi
usage() {
    cat <<'HELP'
Usage: bash start.sh [start|backup|backups|restore] [OPTIONS]

  start              Start services (default)
  backup             Run a backup now; requires --backup s3 or local
  backups            List local backup archives; requires --backup local
  restore            Restore --archive FILE into --volume NEW_OR_EMPTY_VOLUME

  --backup MODE      none (default), s3, or local
  --foreground       Attach to logs instead of starting in the background
  --pull             Pull images before starting
  --project-name NAME  Override the Compose project name
  --archive FILE     Downloaded .tar.gz archive to restore
  --volume NAME      New or empty Docker volume to restore into
  -h, --help         Show this help

Keep this script beside compose.yaml and configure .env there before starting.
HELP
}
fail() { printf '%s\n' "$1" >&2; exit 1; }
while (( $# )); do
    case "$1" in
        --backup|--project-name|--archive|--volume)
            (( $# >= 2 )) && [[ -n $2 && $2 != --* ]] || fail "Missing value for $1"
            case "$1" in
                --backup) backup=$2 ;;
                --project-name) project=$2 ;;
                --archive) archive=$2 ;;
                --volume) volume=$2 ;;
            esac
            shift 2 ;;
        --foreground) foreground=true; shift ;;
        --pull) pull=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unknown option: $1 (use --help)" ;;
    esac
done
case "$action" in start|backup|backups|restore) ;; *) fail "Unknown command: $action" ;; esac
case "$backup" in none|s3|local) ;; *) fail 'Backup mode must be none, s3, or local' ;; esac
if [[ $action != start ]] && { $foreground || $pull; }; then
    fail '--foreground and --pull are only supported by the start command'
fi
if [[ $action == restore ]]; then
    [[ -n $archive && -n $volume ]] || fail 'restore requires --archive FILE and --volume NAME'
    [[ $backup == none && -z $project ]] || fail 'restore targets an explicit volume; omit --backup and --project-name'
    [[ $archive == /* ]] || archive="$caller_directory/$archive"
    helper="$directory/restore-home.sh"
    [[ -f $helper ]] || helper="$directory/build/tools/restore-home.sh"
    [[ -f $helper ]] || fail 'Restore helper is missing; reinstall this release.'
    exec bash "$helper" "$archive" "$volume"
fi
[[ -z $archive && -z $volume ]] || fail '--archive and --volume are only supported by restore'
[[ $action != backup || $backup != none ]] || fail 'backup requires --backup s3 or local'
[[ $action != backups || $backup == local ]] || fail 'backups requires --backup local; use your storage provider to list S3 archives'
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
case "$action" in
    start)
        command+=(up)
        if ! $foreground; then command+=(-d); fi
        if $pull; then command+=(--pull always); fi
        ;;
    backup) command+=(run --rm --no-deps --entrypoint backup backup) ;;
    backups)
        command+=(run --rm --no-deps --entrypoint /bin/sh backup -ec
            'find /archive -maxdepth 1 -type f -name "*.tar.gz" -exec ls -lh {} \;')
        ;;
esac
exec "${command[@]}"
