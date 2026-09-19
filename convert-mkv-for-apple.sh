#!/usr/bin/env -S zsh --no-rcs

#=======================# Streamkeeper compatibility launcher #=======================#
#
# Keeps the familiar shell entry point while policy moves to the tested Python package.
# Conversion execution remains locked until the parity gate is accepted. Dry runs work.
#
# Usage:
#   ./convert-mkv-for-apple.sh --path "/path/to/file-or-directory" --dry-run
#
#===============================# Variables #================================#

SCRIPT_DIR="${0:A:h}"
SCRIPT_NAME="${0:t}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MEDIA_PATH=""
DRY_RUN=0
LIBRARY_TYPE="mixed"
OUTPUT_FORMAT="text"

#===============================# Functions #================================#

print_help() {
  print -- "Usage: $SCRIPT_NAME --path PATH [--dry-run] [--library-type movie|tv|mixed] [--format text|json]"
  print -- ""
  print -- "Options:"
  print -- "  -p, --path PATH          File or directory to plan"
  print -- "  -d, --dry-run            Show planned work without changing media"
  print -- "      --library-type TYPE  movie, tv, or mixed (default: mixed)"
  print -- "      --format FORMAT      text or json (default: text)"
  print -- "  -h, --help               Show this help"
}

die() {
  print -u2 -- "$SCRIPT_NAME: $1"
  exit 2
}

#==================================# Main #===================================#

while (( $# )); do
  case "$1" in
    -p|--path)
      (( $# >= 2 )) || die "$1 requires a path"
      MEDIA_PATH="$2"
      shift 2
      ;;
    -d|--dry-run)
      DRY_RUN=1
      shift
      ;;
    --library-type)
      (( $# >= 2 )) || die "$1 requires movie, tv, or mixed"
      LIBRARY_TYPE="$2"
      shift 2
      ;;
    --format)
      (( $# >= 2 )) || die "$1 requires text or json"
      OUTPUT_FORMAT="$2"
      shift 2
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

[[ -n "$MEDIA_PATH" ]] || die "--path is required"

typeset -a command
command=("$PYTHON_BIN" -m streamkeeper.cli convert --path "$MEDIA_PATH" --library-type "$LIBRARY_TYPE" --format "$OUTPUT_FORMAT")
(( DRY_RUN )) && command+=(--dry-run)

if [[ -d "$SCRIPT_DIR/src/streamkeeper" ]]; then
  export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
fi

exec "${command[@]}"
