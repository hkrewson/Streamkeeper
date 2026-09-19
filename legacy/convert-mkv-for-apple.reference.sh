#!/usr/bin/env bash

#====================# Convert Media To MKV For Apple #====================#
#
# AUDIO:
#  All audio streams are preserved in a new MKV container.
#  If no existing AppleTV audio stream is available, the best available
#       audio stream is converted to an appropriate format.
#
#  Audio source / condition              Compatibility choice
#  ------------------------------------  ---------------------------------
#  Eligible conversion sources           TrueHD/Atmos, DTS-HD/X/DTS, FLAC,
#                                          PCM/LPCM, E-AC3 above 5.1, and
#                                          other non-Apple audio codecs
#  Existing E-AC3 5.1                    Preserve; select as default
#  Eligible source, 6+ channels          Add E-AC3 5.1 at 640 kbps
#  Eligible source, 3-5 channels         Add E-AC3 with the same channel
#                                          count, capped at 640 kbps
#  Eligible stereo source                Add AAC 2.0 at 192 kbps
#  Eligible mono source                  Add AAC 1.0 at 96 kbps
#  Existing AC3 5.1                      Preserve; default if no E-AC3 5.1
#  Existing AAC 5.1                      Preserve; next default fallback
#  Existing AAC 2.0                      Preserve; final compatible fallback
#  Commentary/descriptive audio          Preserve; never use as conversion
#                                          source or automatic default
#  All conversions                       Never upmix; use the best eligible
#                                          original track as the source
#
# Compatible Video streams are preserved and copied over. Incompatible
#   video streams are converted to a best image option.
#
# INPUTS AND SUBTITLES:
#  MKV, AVI, TS, M2TS, MTS, MP4, and M4V files are accepted. The validated
#  output is always MKV, and the source container is retained as an Original.
#  ASS/SSA subtitles are retained and receive an additional SRT fallback.
#  MOV_TEXT subtitles are converted to SRT for MKV compatibility. Bitmap
#  subtitles (PGS/VobSub) are retained, but cannot be converted to text
#  reliably without language-aware OCR and are called out in the evidence.
#
#===========================# VARIABLES #============================#

set -u
set -o pipefail

INPUT_PATH=''
DRY_RUN=false
CRF=18
PRESET='slow'
FALLBACK_LANGUAGE='eng'
SCRIPT_VERSION='2.0'
TEMP_FILES=('')

#==========================# FUNCTIONS #=============================#

usage() {
    cat <<EOF
Usage: $(basename "$0") --path FILE_OR_DIRECTORY [options]

Options:
  -p, --path PATH       Media file or directory to process recursively (required)
  -d, --dry-run         Probe files and print planned changes without writing anything
      --crf NUMBER      HEVC quality for video transcodes (default: 18)
      --preset NAME     libx265 preset for video transcodes (default: slow)
      --fallback-language CODE
                       Language used when a track has no language tag (default: eng)
  -h, --help            Show this help

Existing streams are retained. When conversion is necessary, a compatible audio
stream is appended and selected as the default according to the configured policy.
Supported inputs: MKV, AVI, TS, M2TS, MTS, MP4, and M4V. Output is always MKV.
A .conversion.txt evidence record is written beside each successful conversion.
EOF
}

supported_input_file() {
    case "$1" in
        *.[mM][kK][vV]|*.[aA][vV][iI]|*.[tT][sS]|*.[mM]2[tT][sS]|*.[mM][tT][sS]|*.[mM][pP]4|*.[mM]4[vV]) return 0 ;;
        *) return 1 ;;
    esac
}

cleanup_temp_files() {
    local temp_file

    for temp_file in "${TEMP_FILES[@]}"; do
        if [[ -n "$temp_file" && -e "$temp_file" ]]; then
            rm -f "$temp_file"
        fi
    done
}

handle_signal() {
    local exit_status=$1

    trap - EXIT HUP INT TERM
    cleanup_temp_files
    exit "$exit_status"
}

language_name() {
    case "$1" in
        en|eng) printf 'English' ;;
        es|spa) printf 'Spanish' ;;
        fr|fre|fra) printf 'French' ;;
        de|ger|deu) printf 'German' ;;
        it|ita) printf 'Italian' ;;
        ja|jpn) printf 'Japanese' ;;
        ko|kor) printf 'Korean' ;;
        zh|chi|zho) printf 'Chinese' ;;
        pt|por) printf 'Portuguese' ;;
        ru|rus) printf 'Russian' ;;
        ar|ara) printf 'Arabic' ;;
        hi|hin) printf 'Hindi' ;;
        nl|dut|nld) printf 'Dutch' ;;
        sv|swe) printf 'Swedish' ;;
        no|nor) printf 'Norwegian' ;;
        da|dan) printf 'Danish' ;;
        fi|fin) printf 'Finnish' ;;
        pl|pol) printf 'Polish' ;;
        tr|tur) printf 'Turkish' ;;
        cs|cze|ces) printf 'Czech' ;;
        hu|hun) printf 'Hungarian' ;;
        he|heb) printf 'Hebrew' ;;
        th|tha) printf 'Thai' ;;
        vi|vie) printf 'Vietnamese' ;;
        id|ind) printf 'Indonesian' ;;
        und|'') printf 'Unknown' ;;
        *) printf '%s' "$1" ;;
    esac
}

resolved_language_code() {
    local audio_json=$1
    local language_code

    language_code=$(jq -r '.tags.language // "und"' <<< "$audio_json")
    if [[ -z "$language_code" || "$language_code" == 'und' ]]; then
        printf '%s' "$FALLBACK_LANGUAGE"
    else
        printf '%s' "$language_code"
    fi
}

plex_extra_type() {
    local directory_name=$1
    local normalized

    normalized=$(printf '%s' "$directory_name" | tr '[:upper:]' '[:lower:]')
    case "$normalized" in
        'behind the scenes'|behindthescenes) printf 'behindthescenes' ;;
        'deleted scenes'|deleted) printf 'deleted' ;;
        featurettes|featurette) printf 'featurette' ;;
        interviews|interview) printf 'interview' ;;
        scenes|scene) printf 'scene' ;;
        shorts|short) printf 'short' ;;
        trailers|trailer) printf 'trailer' ;;
        other|extras) printf 'other' ;;
        *) printf '' ;;
    esac
}

channel_name() {
    case "$1" in
        1) printf '1.0' ;;
        2) printf '2.0' ;;
        3) printf '3.0' ;;
        4) printf '4.0' ;;
        5) printf '5.0' ;;
        6) printf '5.1' ;;
        7) printf '6.1' ;;
        8) printf '7.1' ;;
        *) printf '%s ch' "$1" ;;
    esac
}

codec_name() {
    local codec=$1
    local profile=$2
    local title=$3

    case "$codec" in
        truehd|mlp)
            if printf '%s %s' "$profile" "$title" | grep -Eiq 'atmos'; then
                printf 'TrueHD Atmos'
            else
                printf 'TrueHD'
            fi
            ;;
        dts)
            if printf '%s %s' "$profile" "$title" | grep -Eiq 'dts[: -]*x'; then
                printf 'DTS:X'
            elif printf '%s' "$profile" | grep -Eiq 'master|ma'; then
                printf 'DTS-HD MA'
            elif printf '%s' "$profile" | grep -Eiq 'high.resolution|hra'; then
                printf 'DTS-HD HRA'
            else
                printf 'DTS'
            fi
            ;;
        eac3)
            if printf '%s %s' "$profile" "$title" | grep -Eiq 'atmos|joc'; then
                printf 'E-AC3 Atmos'
            else
                printf 'E-AC3'
            fi
            ;;
        ac3) printf 'AC3' ;;
        aac) printf 'AAC' ;;
        pcm_*|pcm) printf 'PCM' ;;
        *) printf '%s' "$codec" | tr '[:lower:]' '[:upper:]' ;;
    esac
}

audio_label() {
    local audio_json=$1
    local language_code profile codec channels old_title language format layout base

    language_code=$(resolved_language_code "$audio_json")
    profile=$(jq -r '.profile // ""' <<< "$audio_json")
    codec=$(jq -r '.codec_name // "unknown"' <<< "$audio_json")
    channels=$(jq -r '.channels // 0' <<< "$audio_json")
    old_title=$(jq -r '.tags.title // ""' <<< "$audio_json")
    language=$(language_name "$language_code")
    format=$(codec_name "$codec" "$profile" "$old_title")
    layout=$(channel_name "$channels")
    base="$language $format $layout"

    if [[ -n "$old_title" && "$old_title" != "$base" ]] && \
        printf '%s' "$old_title" | grep -Eiq 'commentary|descriptive|description|original|dub|director|isolated|narration|alternate'; then
        printf '%s - %s' "$base" "$old_title"
    else
        printf '%s' "$base"
    fi
}

print_command() {
    local arg

    printf '  Command:'
    for arg in "$@"; do
        printf ' %q' "$arg"
    done
    printf '\n'
}

require_dovi_tool() {
    if ! command -v dovi_tool >/dev/null 2>&1; then
        printf 'Dolby Vision/HDR10+ normalization requires dovi_tool: %s\n' \
            'https://github.com/quietvoid/dovi_tool' >&2
        return 1
    fi
}

file_mode() {
    local file=$1

    if stat -f '%Lp' "$file" 2>/dev/null; then
        return 0
    fi

    stat -c '%a' -- "$file" 2>/dev/null
}

matching_nfo_path() {
    local directory=$1
    local source_stem=$2
    local output_stem=$3
    local candidate root_type media_file_count

    for candidate in "$directory/$source_stem.nfo" "$directory/$output_stem.nfo"; do
        if [[ -f "$candidate" ]]; then
            root_type=$(nfo_root_type "$candidate")
            if [[ "$root_type" == 'movie' || "$root_type" == 'episodedetails' ]]; then
                printf '%s' "$candidate"
                return 0
            fi
        fi
    done

    candidate="$directory/movie.nfo"
    media_file_count=$(find "$directory" -maxdepth 1 -type f \
        \( -iname '*.mkv' -o -iname '*.avi' -o -iname '*.ts' -o -iname '*.m2ts' \
           -o -iname '*.mts' -o -iname '*.mp4' -o -iname '*.m4v' \) \
        ! -name '._*' ! -iname '* Original.*' -print0 \
        | perl -0ne '$count++; END { print $count // 0 }')
    if [[ "$media_file_count" == '1' && -f "$candidate" && "$(nfo_root_type "$candidate")" == 'movie' ]]; then
        printf '%s' "$candidate"
        return 0
    fi

    printf ''
}

nfo_root_type() {
    local nfo_path=$1

    perl -0777 -ne '
        if (/<movie\b/i) {
            print "movie";
        }
        elsif (/<episodedetails\b/i) {
            print "episodedetails";
        }
    ' "$nfo_path"
}

prepare_nfo_update() {
    local source_nfo=$1
    local temp_nfo=$2
    local note=$3
    local streamdetails_file=$4
    local nfo_mode

    nfo_mode=$(file_mode "$source_nfo") || return 1
    cp "$source_nfo" "$temp_nfo" || return 1

    LOCALMOVIESCANNER_NOTE=$note LOCALMOVIESCANNER_STREAMDETAILS=$streamdetails_file perl -0777 -pi -e '
        my $note = $ENV{"LOCALMOVIESCANNER_NOTE"};
        my $streamdetails_path = $ENV{"LOCALMOVIESCANNER_STREAMDETAILS"};
        my $newline = /\r\n/ ? "\r\n" : "\n";
        my ($root) = /<(movie|episodedetails)\b/i;
        die "NFO root must be <movie> or <episodedetails>\n" unless defined $root;
        $root = lc $root;
        my $closing_root = qr{</\Q$root\E\s*>}i;
        open my $streamdetails_fh, "<", $streamdetails_path
            or die "Cannot read generated stream details: $!\n";
        local $/;
        my $fileinfo = <$streamdetails_fh>;
        close $streamdetails_fh;
        $fileinfo =~ s/\r?\n/$newline/g;
        $fileinfo =~ s/\Q$newline\E\z//;

        $note =~ s/&/&amp;/g;
        $note =~ s/</&lt;/g;
        $note =~ s/>/&gt;/g;
        $note =~ s/"/&quot;/g;
        $note =~ s/'"'"'/&apos;/g;

        if (s{<user_note\s*/>}{<user_note>$note</user_note>}s) {
            # Replaced an empty tinyMediaManager user-note element.
        }
        elsif (s{<user_note>(.*?)</user_note>}{
            my $existing = $1;
            my $combined = $existing eq "" ? $note : "$existing$newline$note";
            "<user_note>$combined</user_note>";
        }se) {
            # Appended while preserving existing content.
        }
        elsif (s{$closing_root}{"  <user_note>$note</user_note>$newline</$root>"}e) {
            # Added the field to a movie or episode NFO without a user-note element.
        }
        else {
            die "NFO does not contain a closing </$root> element\n";
        }

        if (s{\s*<fileinfo\b[^>]*>.*?</fileinfo>}{$newline$fileinfo}s) {
            # Replaced stale technical stream data with data from the validated output.
        }
        elsif (s{$closing_root}{$fileinfo$newline</$root>}s) {
            # Added technical stream data when the NFO did not already have it.
        }
        else {
            die "NFO does not contain a closing </$root> element\n";
        }
    ' "$temp_nfo" || return 1

    chmod "$nfo_mode" "$temp_nfo"
}

write_nfo_streamdetails() {
    local output_probe=$1
    local destination=$2

    jq -r '
        def xml:
            tostring
            | gsub("&"; "&amp;")
            | gsub("<"; "&lt;")
            | gsub(">"; "&gt;")
            | gsub("\\\""; "&quot;")
            | gsub("\\u0027"; "&apos;");
        def element($indent; $name; $value):
            ($indent + "<" + $name + ">" + (($value // "") | xml) + "</" + $name + ">");
        def primary_video:
            [.streams[] | select(.codec_type == "video" and ((.disposition.attached_pic // 0) == 0))][0];
        def aspect($video):
            if (($video.display_aspect_ratio // "") | test("^[0-9]+:[0-9]+$")) then
                (($video.display_aspect_ratio | split(":")) as $parts
                 | (($parts[0] | tonumber) / ($parts[1] | tonumber) * 100 | round) / 100)
            elif (($video.width // 0) > 0 and ($video.height // 0) > 0) then
                ((($video.width / $video.height) * 100 | round) / 100)
            else "" end;
        def resolution($video):
            if ($video.width // 0) >= 3840 then 2160
            elif ($video.width // 0) >= 1920 then 1080
            elif ($video.width // 0) >= 1280 then 720
            else ($video.height // "") end;
        def hdr_type($video):
            if any($video.side_data_list[]?; (.side_data_type // "") | test("DOVI"; "i")) then "dolbyvision"
            elif ($video.color_transfer // "") == "smpte2084" then "hdr10"
            elif ($video.color_transfer // "") == "arib-std-b67" then "hlg"
            else "" end;
        def audio_codec:
            ((.profile // "") + " " + (.tags.title // "")) as $description
            | if (.codec_name == "truehd" or .codec_name == "mlp") and ($description | test("atmos"; "i")) then "truehd_atmos"
              elif (.codec_name == "truehd" or .codec_name == "mlp") then "truehd"
              elif .codec_name == "eac3" and ($description | test("atmos|joc"; "i")) then "eac3_ddp_atmos"
              elif .codec_name == "dts" and ($description | test("master|ma|dts[: -]*x"; "i")) then "dtshd_ma"
              elif .codec_name == "dts" and ($description | test("high.resolution|hra"; "i")) then "dtshd_hra"
              elif .codec_name == "dts" then "dca"
              else (.codec_name // "unknown") end;
        primary_video as $video
        | ([
            "  <fileinfo>",
            "    <streamdetails>",
            "      <video>",
            element("        "; "codec"; ($video.codec_name // "unknown")),
            element("        "; "aspect"; aspect($video)),
            element("        "; "width"; ($video.width // "")),
            element("        "; "height"; ($video.height // "")),
            element("        "; "resolution"; resolution($video)),
            element("        "; "durationinseconds"; (((.format.duration // $video.duration // "0") | tonumber) | round)),
            element("        "; "stereomode"; ($video.tags.stereo_mode // "")),
            element("        "; "hdrtype"; hdr_type($video)),
            "      </video>"
          ]
          + ([.streams[] | select(.codec_type == "audio")
              | "      <audio>",
                element("        "; "codec"; audio_codec),
                element("        "; "language"; (.tags.language // "und")),
                element("        "; "channels"; (.channels // "")),
                "      </audio>"])
          + ([.streams[] | select(.codec_type == "subtitle")
              | "      <subtitle>",
                element("        "; "language"; (.tags.language // "und")),
                "      </subtitle>"])
          + ["    </streamdetails>", "  </fileinfo>"])
        | .[]
    ' <<< "$output_probe" > "$destination"
}

process_file() {
    local input=$1
    local directory directory_name filename stem input_extension desired_stem output_path original_path existing_original original_candidate evidence_path nfo_path nfo_root extra_type
    local probe video video_index video_codec
    local pixel_format transfer primaries color_space dovi_profile dovi_compatibility hdr10plus hdr_mode
    local video_action video_reason source audio_count generated_codec generated_channels
    local generated_bitrate generated_source_ordinal generated_source_index generated_language
    local generated_label generated_ordinal target_exists default_ordinal default_description
    local source_bitrate audio_item audio_language
    local i audio old_label default_candidate temp_output evidence_temp nfo_temp streamdetails_temp raw_source raw_processed input_number
    local master_display max_cll x265_params expected_streams actual_streams output_video_codec
    local output_dovi_profile output_dovi_compatibility output_hdr10plus output_transfer output_pixel_format
    local generated_check input_mode frame_rate timestamp nfo_note output_probe
    local stream_item stream_type stream_specifier stream_ordinal disposition_spec audio_disposition
    local video_ordinal subtitle_ordinal data_ordinal attachment_ordinal
    local disposition_type disposition_count expected_dispositions actual_dispositions
    local attached_picture_count attached_picture_item attached_picture_index attachment_filename attachment_mimetype
    local existing_attachment_count attachment_output_ordinal attachment_codec attachment_extension attachment_temp j
    local subtitle subtitle_count subtitle_item subtitle_codec subtitle_language subtitle_title
    local text_fallback_count bitmap_subtitle_count mov_text_count data_stream_count
    local generated_subtitle_ordinal generated_subtitle_check
    local -a ffmpeg_command
    local -a demux_command
    local -a dovi_command
    local -a attachment_extract_command
    local -a attachment_indexes
    local -a attachment_files
    local -a attachment_filenames
    local -a attachment_mimetypes
    local -a attachment_command_log
    local -a text_fallback_ordinals
    local -a text_fallback_languages
    local -a text_fallback_titles

    directory=${input%/*}
    [[ "$directory" != "$input" ]] || directory='.'
    filename=${input##*/}
    stem=${filename%.*}
    input_extension=${filename##*.}

    if [[ "$filename" == ._* ]]; then
        printf '\nSKIP: %s (AppleDouble metadata sidecar, not a media file)\n' "$input"
        return 0
    fi

    if [[ "$stem" == *' Original' ]]; then
        printf '\nSKIP: %s (original backup)\n' "$input"
        return 0
    fi

    directory_name=${directory##*/}
    extra_type=$(plex_extra_type "$directory_name")
    desired_stem=$stem
    if [[ -n "$extra_type" && "$stem" != *-"$extra_type" ]]; then
        desired_stem="$stem-$extra_type"
    fi

    output_path="$directory/$desired_stem.mkv"
    original_path="$directory/$desired_stem Original.$input_extension"
    evidence_path="$directory/$desired_stem.conversion.txt"
    nfo_path=$(matching_nfo_path "$directory" "$stem" "$desired_stem")
    nfo_root=''
    [[ -n "$nfo_path" ]] && nfo_root=$(nfo_root_type "$nfo_path")
    existing_original=''
    for original_candidate in \
        "$directory/$desired_stem Original."[mM][kK][vV] \
        "$directory/$desired_stem Original."[aA][vV][iI] \
        "$directory/$desired_stem Original."[tT][sS] \
        "$directory/$desired_stem Original."[mM]2[tT][sS] \
        "$directory/$desired_stem Original."[mM][tT][sS] \
        "$directory/$desired_stem Original."[mM][pP]4 \
        "$directory/$desired_stem Original."[mM]4[vV]; do
        if [[ -e "$original_candidate" ]]; then
            existing_original=$original_candidate
            break
        fi
    done
    if [[ -n "$existing_original" ]]; then
        printf '\nSKIP: %s (already has %s)\n' "$input" "${existing_original##*/}"
        return 0
    fi
    if [[ "$output_path" != "$input" && -e "$output_path" ]]; then
        printf '\nSKIP: %s (Plex-named output already exists: %s)\n' "$input" "${output_path##*/}"
        return 0
    fi
    if [[ -e "$evidence_path" ]]; then
        printf '\nSKIP: %s (conversion evidence already exists: %s)\n' "$input" "${evidence_path##*/}"
        return 0
    fi

    printf '\nAnalyze: %s\n' "$input"

    if ! probe=$(ffprobe -v error -show_streams -show_format -print_format json "$input"); then
        printf 'ERROR: ffprobe could not read %s\n' "$input" >&2
        return 1
    fi

    video=$(jq -c '[.streams[] | select(.codec_type == "video" and ((.disposition.attached_pic // 0) == 0))][0] // empty' <<< "$probe")
    if [[ -z "$video" ]]; then
        printf 'ERROR: no primary video stream found.\n' >&2
        return 1
    fi

    video_index=$(jq -r '.index' <<< "$video")
    video_codec=$(jq -r '.codec_name // "unknown"' <<< "$video")
    pixel_format=$(jq -r '.pix_fmt // "unknown"' <<< "$video")
    transfer=$(jq -r '.color_transfer // "unknown"' <<< "$video")
    primaries=$(jq -r '.color_primaries // "unknown"' <<< "$video")
    color_space=$(jq -r '.color_space // "unknown"' <<< "$video")
    frame_rate=$(jq -r '.avg_frame_rate // .r_frame_rate // "24000/1001"' <<< "$video")
    if [[ -z "$frame_rate" || "$frame_rate" == '0/0' ]]; then
        frame_rate=$(jq -r '.r_frame_rate // "24000/1001"' <<< "$video")
    fi
    [[ -z "$frame_rate" || "$frame_rate" == '0/0' ]] && frame_rate='24000/1001'
    dovi_profile=$(jq -r '[.side_data_list[]? | select((.side_data_type // "") | test("DOVI"; "i"))][0].dv_profile // empty' <<< "$video")
    dovi_compatibility=$(jq -r '[.side_data_list[]? | select((.side_data_type // "") | test("DOVI"; "i"))][0].dv_bl_signal_compatibility_id // empty' <<< "$video")
    hdr10plus=$(jq -r 'any(.side_data_list[]?; (.side_data_type // "") | test("HDR10\\+|SMPTE2094-40"; "i"))' <<< "$video")

    hdr_mode='SDR'
    if [[ -n "$dovi_profile" ]]; then
        hdr_mode="Dolby Vision profile $dovi_profile"
        [[ -n "$dovi_compatibility" ]] && hdr_mode="$hdr_mode (compatibility $dovi_compatibility)"
    elif [[ "$transfer" == 'smpte2084' ]]; then
        hdr_mode='HDR10'
    elif [[ "$transfer" == 'arib-std-b67' ]]; then
        hdr_mode='HLG'
    fi
    [[ "$hdr10plus" == true ]] && hdr_mode="$hdr_mode + HDR10+"

    video_action='copy'
    video_reason='already Apple-compatible'

    if [[ "$video_codec" == 'hevc' ]]; then
        if [[ "$dovi_profile" == '7' && "$hdr10plus" == true ]]; then
            video_action='dovi_convert_strip_hdr10plus'
            video_reason='convert Dolby Vision 7 to 8.1 and remove HDR10+ metadata'
        elif [[ "$dovi_profile" == '7' ]]; then
            video_action='dovi_convert'
            video_reason='convert Dolby Vision 7 to 8.1 without re-encoding the base layer'
        elif [[ "$hdr10plus" == true ]]; then
            video_action='strip_hdr10plus'
            video_reason='retain the HDR10 base and remove HDR10+ metadata'
        fi
    elif [[ "$video_codec" == 'h264' && "$hdr_mode" == 'SDR' ]]; then
        video_action='copy'
        video_reason='SDR H.264 is already Apple-compatible'
    else
        video_action='transcode_hevc'
        video_reason="transcode $video_codec to HEVC"
    fi

    if [[ "$dovi_profile" == '8' && "$dovi_compatibility" != '1' && "$dovi_compatibility" != '4' ]]; then
        printf 'ERROR: Dolby Vision profile 8 compatibility ID %s is not safely convertible to 8.1/8.4 by metadata rewriting alone.\n' "${dovi_compatibility:-unknown}" >&2
        printf '       This file was left unchanged.\n' >&2
        return 1
    fi
    if [[ -n "$dovi_profile" && "$dovi_profile" != '5' && "$dovi_profile" != '7' && "$dovi_profile" != '8' ]]; then
        printf 'ERROR: unsupported Dolby Vision profile %s; file left unchanged.\n' "$dovi_profile" >&2
        return 1
    fi

    audio=$(jq -c '[.streams[] | select(.codec_type == "audio")]' <<< "$probe")
    audio_count=$(jq 'length' <<< "$audio")
    subtitle=$(jq -c '[.streams[] | select(.codec_type == "subtitle")]' <<< "$probe")
    subtitle_count=$(jq 'length' <<< "$subtitle")
    data_stream_count=$(jq '[.streams[] | select(.codec_type == "data")] | length' <<< "$probe")
    text_fallback_ordinals=()
    text_fallback_languages=()
    text_fallback_titles=()
    text_fallback_count=0
    bitmap_subtitle_count=0
    mov_text_count=0

    i=0
    while (( i < subtitle_count )); do
        subtitle_item=$(jq -c ".[$i]" <<< "$subtitle")
        subtitle_codec=$(jq -r '.codec_name // "unknown"' <<< "$subtitle_item")
        subtitle_language=$(jq -r '.tags.language // "und"' <<< "$subtitle_item")
        [[ -n "$subtitle_language" && "$subtitle_language" != 'und' ]] || subtitle_language=$FALLBACK_LANGUAGE
        subtitle_title=$(jq -r '.tags.title // ""' <<< "$subtitle_item")
        case "$subtitle_codec" in
            ass|ssa)
                text_fallback_ordinals+=("$i")
                text_fallback_languages+=("$subtitle_language")
                if [[ -n "$subtitle_title" ]]; then
                    text_fallback_titles+=("$subtitle_title - SRT Compatibility")
                else
                    text_fallback_titles+=("$(language_name "$subtitle_language") SRT Compatibility")
                fi
                text_fallback_count=$((text_fallback_count + 1))
                ;;
            mov_text)
                mov_text_count=$((mov_text_count + 1))
                ;;
            hdmv_pgs_subtitle|dvd_subtitle)
                bitmap_subtitle_count=$((bitmap_subtitle_count + 1))
                ;;
        esac
        i=$((i + 1))
    done

    source=$(jq -c '
        def excluded:
            ((.tags.title // "") | test("commentary|descriptive|description|narration"; "i"))
            or ((.disposition.visual_impaired // 0) == 1);
        def quality:
            if (.codec_name == "truehd" or .codec_name == "mlp") then 600
            elif (.codec_name == "dts" and (((.profile // "") + " " + (.tags.title // "")) | test("master|ma|dts[: -]*x"; "i"))) then 550
            elif .codec_name == "flac" then 525
            elif ((.codec_name // "") | startswith("pcm_")) then 500
            elif (.codec_name == "eac3" and (.channels // 0) > 6) then 450
            elif .codec_name == "dts" then 400
            elif ((.codec_name // "unknown") | IN("aac", "ac3", "eac3", "mp3", "alac", "unknown") | not) then 300
            else 0 end;
        def channel_class:
            if (.channels // 0) >= 6 then 3
            elif (.channels // 0) >= 3 then 2
            else 1 end;
        [to_entries[] | .value + {audio_ordinal: .key}]
        | map(select((excluded | not) and quality > 0))
        | if length == 0 then empty
          else max_by([channel_class, quality, (.channels // 0), ((.bit_rate // "0") | tonumber), (.disposition.default // 0), (-.audio_ordinal)])
          end
    ' <<< "$audio")

    generated_codec=''
    generated_channels=0
    generated_bitrate=0
    generated_source_ordinal=''
    generated_source_index=''
    generated_language='und'
    generated_label=''
    target_exists=false

    if [[ -n "$source" ]]; then
        generated_source_ordinal=$(jq -r '.audio_ordinal' <<< "$source")
        generated_source_index=$(jq -r '.index' <<< "$source")
        generated_channels=$(jq -r '.channels // 0' <<< "$source")
        generated_language=$(resolved_language_code "$source")

        if (( generated_channels >= 6 )); then
            generated_codec='eac3'
            generated_channels=6
            generated_bitrate=640000
            target_exists=$(jq -r 'any(.[]; .codec_name == "eac3" and (.channels // 0) == 6 and (((.tags.title // "") | test("commentary|descriptive|description|narration"; "i")) | not) and ((.disposition.visual_impaired // 0) != 1))' <<< "$audio")
        elif (( generated_channels >= 3 )); then
            generated_codec='eac3'
            source_bitrate=$(jq -r '(.bit_rate // "0") | tonumber' <<< "$source")
            if (( source_bitrate > 0 && source_bitrate < 640000 )); then
                generated_bitrate=$source_bitrate
            else
                generated_bitrate=640000
            fi
            target_exists=$(jq -r --argjson channels "$generated_channels" 'any(.[]; .codec_name == "eac3" and (.channels // 0) == $channels and (((.tags.title // "") | test("commentary|descriptive|description|narration"; "i")) | not) and ((.disposition.visual_impaired // 0) != 1))' <<< "$audio")
        elif (( generated_channels == 2 )); then
            generated_codec='aac'
            generated_channels=2
            generated_bitrate=192000
            target_exists=$(jq -r 'any(.[]; .codec_name == "aac" and (.channels // 0) == 2 and (((.tags.title // "") | test("commentary|descriptive|description|narration"; "i")) | not) and ((.disposition.visual_impaired // 0) != 1))' <<< "$audio")
        elif (( generated_channels == 1 )); then
            generated_codec='aac'
            generated_channels=1
            generated_bitrate=96000
            target_exists=$(jq -r 'any(.[]; .codec_name == "aac" and (.channels // 0) == 1 and (((.tags.title // "") | test("commentary|descriptive|description|narration"; "i")) | not) and ((.disposition.visual_impaired // 0) != 1))' <<< "$audio")
        else
            generated_codec=''
            target_exists=true
        fi

        if [[ "$target_exists" == true ]]; then
            generated_codec=''
        else
            generated_label="$(language_name "$generated_language") $(codec_name "$generated_codec" '' '') $(channel_name "$generated_channels")"
        fi
    fi

    generated_ordinal=$audio_count
    default_ordinal=$(jq -r '
        def eligible:
            ((((.tags.title // "") | test("commentary|descriptive|description|narration"; "i")) | not)
            and ((.disposition.visual_impaired // 0) != 1));
        [to_entries[] | select(.value | eligible) | .key as $n | .value + {audio_ordinal: $n}]
        | ([.[] | select(.codec_name == "eac3" and (.channels // 0) == 6)][0]
          // [.[] | select(.codec_name == "ac3" and (.channels // 0) == 6)][0]
          // [.[] | select(.codec_name == "aac" and (.channels // 0) == 6)][0]
          // [.[] | select(.codec_name == "aac" and (.channels // 0) == 2)][0]
          // [.[] | select((.disposition.default // 0) == 1)][0]
          // .[0]
          // {audio_ordinal: 0})
        | .audio_ordinal
    ' <<< "$audio")

    if [[ -n "$generated_codec" ]]; then
        if [[ "$generated_codec" == 'eac3' && "$generated_channels" == '6' ]]; then
            default_ordinal=$generated_ordinal
        elif ! jq -e 'any(.[];
            ((.codec_name == "eac3" or .codec_name == "ac3" or .codec_name == "aac") and (.channels // 0) == 6)
            and ((((.tags.title // "") | test("commentary|descriptive|description|narration"; "i")) | not))
            and ((.disposition.visual_impaired // 0) != 1)
        )' <<< "$audio" >/dev/null; then
            default_ordinal=$generated_ordinal
        fi
    fi

    if [[ -n "$generated_codec" && "$default_ordinal" == "$generated_ordinal" ]]; then
        default_description="$generated_label (new)"
    elif (( audio_count > 0 )); then
        default_candidate=$(jq -c ".[$default_ordinal]" <<< "$audio")
        default_description=$(audio_label "$default_candidate")
    else
        default_description='none'
    fi

    printf '  Video: %s, %s, %s\n' "$video_codec" "$pixel_format" "$hdr_mode"
    printf '  Video action: %s (%s)\n' "$video_action" "$video_reason"
    printf '  Existing audio tracks: %s (all retained)\n' "$audio_count"
    if (( subtitle_count > 0 )); then
        printf '  Existing subtitle tracks: %s\n' "$subtitle_count"
    fi
    if (( text_fallback_count > 0 )); then
        printf '  New subtitle fallbacks: %s SRT track(s) from ASS/SSA\n' "$text_fallback_count"
    fi
    if (( mov_text_count > 0 )); then
        printf '  Subtitle conversion: %s MOV_TEXT track(s) converted to SRT for MKV\n' "$mov_text_count"
    fi
    if (( bitmap_subtitle_count > 0 )); then
        printf '  Subtitle warning: %s bitmap track(s) retained; automatic OCR is not reliable\n' "$bitmap_subtitle_count"
    fi
    if (( data_stream_count > 0 )); then
        printf '  Container data: %s non-playback data stream(s) omitted from the MKV; retained in Original\n' "$data_stream_count"
    fi
    if (( audio_count == 0 )); then
        printf '  Audio warning: source has no audio stream; no replacement can be generated\n'
    fi

    if [[ -n "$generated_codec" ]]; then
        printf '  New audio: %s at %s kbps, sourced from track %s\n' \
            "$generated_label" "$((generated_bitrate / 1000))" "$((generated_source_ordinal + 1))"
    elif [[ -n "$source" && "$target_exists" == true ]]; then
        printf '  New audio: none (matching compatibility stream already exists)\n'
    else
        printf '  New audio: none (no eligible conversion source)\n'
    fi

    printf '  Default audio: %s\n' "$default_description"
    printf '  Backup name: %s\n' "${original_path##*/}"
    printf '  Output name: %s\n' "${output_path##*/}"
    printf '  Evidence file: %s\n' "${evidence_path##*/}"
    if [[ -n "$nfo_path" ]]; then
        printf '  NFO update: %s (<%s> root; <fileinfo> and <user_note>)\n' "${nfo_path##*/}" "$nfo_root"
    else
        printf '  NFO update: none found (TXT evidence only)\n'
    fi

    if [[ "$video_action" == 'dovi_convert' || "$video_action" == 'dovi_convert_strip_hdr10plus' || "$video_action" == 'strip_hdr10plus' ]]; then
        if command -v dovi_tool >/dev/null 2>&1; then
            printf '  Additional tool: dovi_tool is available\n'
        else
            printf '  Additional tool: dovi_tool is required but not currently installed\n'
        fi
    fi

    i=0
    while (( i < audio_count )); do
        old_label=$(audio_label "$(jq -c ".[$i]" <<< "$audio")")
        printf '  Label %s: %s\n' "$((i + 1))" "$old_label"
        i=$((i + 1))
    done
    if [[ -n "$generated_codec" ]]; then
        printf '  Label %s: %s\n' "$((generated_ordinal + 1))" "$generated_label"
    fi

    if [[ "$DRY_RUN" == true ]]; then
        printf '  DRY RUN: no encode, remux, or rename performed.\n'
        return 0
    fi

    TEMP_FILES=('')
    input_mode=$(file_mode "$input") || {
        printf 'ERROR: could not determine source file permissions.\n' >&2
        return 1
    }
    temp_output=$(mktemp "$directory/.$stem.convert.XXXXXX") || return 1
    TEMP_FILES+=("$temp_output")
    input_number=0

    attachment_indexes=()
    attachment_files=()
    attachment_filenames=()
    attachment_mimetypes=()
    attachment_command_log=()
    attached_picture_count=$(jq '[.streams[] | select(.codec_type == "video" and ((.disposition.attached_pic // 0) == 1))] | length' <<< "$probe")
    existing_attachment_count=$(jq '[.streams[] | select(.codec_type == "attachment")] | length' <<< "$probe")
    j=0
    while (( j < attached_picture_count )); do
        attached_picture_item=$(jq -c "[.streams[] | select(.codec_type == \"video\" and ((.disposition.attached_pic // 0) == 1))][$j]" <<< "$probe")
        attached_picture_index=$(jq -r '.index' <<< "$attached_picture_item")
        attachment_codec=$(jq -r '.codec_name // "unknown"' <<< "$attached_picture_item")
        attachment_filename=$(jq -r '.tags.filename // .tags.FILENAME // empty' <<< "$attached_picture_item")
        attachment_mimetype=$(jq -r '.tags.mimetype // .tags.MIMETYPE // empty' <<< "$attached_picture_item")
        case "$attachment_codec" in
            mjpeg) attachment_extension='jpg'; [[ -n "$attachment_mimetype" ]] || attachment_mimetype='image/jpeg' ;;
            png) attachment_extension='png'; [[ -n "$attachment_mimetype" ]] || attachment_mimetype='image/png' ;;
            webp) attachment_extension='webp'; [[ -n "$attachment_mimetype" ]] || attachment_mimetype='image/webp' ;;
            *) attachment_extension="$attachment_codec"; [[ -n "$attachment_mimetype" ]] || attachment_mimetype='application/octet-stream' ;;
        esac
        attachment_filename=${attachment_filename##*/}
        [[ -n "$attachment_filename" ]] || attachment_filename="cover-$((j + 1)).$attachment_extension"
        attachment_temp=$(mktemp "$directory/.$stem.attachment.XXXXXX") || return 1
        TEMP_FILES+=("$attachment_temp")
        attachment_extract_command=(ffmpeg -hide_banner -nostdin -y -i "$input" -map "0:$attached_picture_index" -frames:v 1 -c copy -update 1 -f image2 "$attachment_temp")
        print_command "${attachment_extract_command[@]}"
        attachment_command_log+=("$(print_command "${attachment_extract_command[@]}")")
        "${attachment_extract_command[@]}" || return 1
        attachment_indexes+=("$attached_picture_index")
        attachment_files+=("$attachment_temp")
        attachment_filenames+=("$attachment_filename")
        attachment_mimetypes+=("$attachment_mimetype")
        j=$((j + 1))
    done

    if [[ "$video_action" == 'dovi_convert' || "$video_action" == 'dovi_convert_strip_hdr10plus' || "$video_action" == 'strip_hdr10plus' ]]; then
        require_dovi_tool || return 1
        raw_source=$(mktemp "$directory/.$stem.source.XXXXXX") || return 1
        raw_processed=$(mktemp "$directory/.$stem.processed.XXXXXX") || return 1
        TEMP_FILES+=("$raw_source" "$raw_processed")

        demux_command=(ffmpeg -hide_banner -nostdin -y -i "$input" -map 0:v:0 -c:v copy -bsf:v hevc_mp4toannexb -an -sn -dn -f hevc "$raw_source")
        print_command "${demux_command[@]}"
        "${demux_command[@]}" || return 1

        dovi_command=(dovi_tool)
        if [[ "$video_action" == 'dovi_convert_strip_hdr10plus' || "$video_action" == 'strip_hdr10plus' ]]; then
            dovi_command+=(--drop-hdr10plus)
        fi
        if [[ "$video_action" == 'dovi_convert' || "$video_action" == 'dovi_convert_strip_hdr10plus' ]]; then
            dovi_command+=(-m 2 convert --discard "$raw_source" -o "$raw_processed")
        else
            dovi_command+=(convert "$raw_source" -o "$raw_processed")
        fi
        print_command "${dovi_command[@]}"
        "${dovi_command[@]}" || return 1

        ffmpeg_command=(ffmpeg -hide_banner -nostdin -y -r "$frame_rate" -f hevc -i "$raw_processed" -i "$input" -map 0:v:0 -map 1 -map -1:v:0)
        input_number=1
    else
        ffmpeg_command=(ffmpeg -hide_banner -nostdin -y -i "$input" -map 0)
    fi

    j=0
    while (( j < attached_picture_count )); do
        ffmpeg_command+=(-map -"$input_number:${attachment_indexes[$j]}")
        j=$((j + 1))
    done

    if (( data_stream_count > 0 )); then
        ffmpeg_command+=(-map -"$input_number:d")
    fi

    j=0
    while (( j < text_fallback_count )); do
        ffmpeg_command+=(-map "$input_number:s:${text_fallback_ordinals[$j]}")
        j=$((j + 1))
    done

    if [[ -n "$generated_codec" ]]; then
        ffmpeg_command+=(-map "$input_number:a:$generated_source_ordinal")
    fi

    ffmpeg_command+=(-map_metadata "$input_number" -map_chapters "$input_number" -c copy)

    # FFmpeg may silently clear dispositions during an MKV remux (notably
    # attached_pic on embedded cover art), so explicitly reproduce them.
    video_ordinal=0
    subtitle_ordinal=0
    data_ordinal=0
    attachment_ordinal=0
    while IFS= read -r stream_item; do
        stream_type=$(jq -r '.codec_type' <<< "$stream_item")
        case "$stream_type" in
            video)
                stream_specifier='v'
                stream_ordinal=$video_ordinal
                video_ordinal=$((video_ordinal + 1))
                ;;
            subtitle)
                stream_specifier='s'
                stream_ordinal=$subtitle_ordinal
                subtitle_ordinal=$((subtitle_ordinal + 1))
                ;;
            data)
                if (( data_stream_count > 0 )); then
                    continue
                fi
                stream_specifier='d'
                stream_ordinal=$data_ordinal
                data_ordinal=$((data_ordinal + 1))
                ;;
            attachment)
                stream_specifier='t'
                stream_ordinal=$attachment_ordinal
                attachment_ordinal=$((attachment_ordinal + 1))
                ;;
            *)
                continue
                ;;
        esac
        disposition_spec=$(jq -r '
            (.disposition // {})
            | to_entries
            | map(select(.value == 1) | .key)
            | join("+")
        ' <<< "$stream_item")
        [[ -n "$disposition_spec" ]] || disposition_spec='0'
        ffmpeg_command+=(-disposition:"$stream_specifier":"$stream_ordinal" "$disposition_spec")
    done < <(jq -c '.streams[] | select(.codec_type != "audio")' <<< "$probe")

    if [[ "$video_action" == 'transcode_hevc' ]]; then
        x265_params="repeat-headers=1"
        ffmpeg_command+=(-c:v:0 libx265 -preset:v:0 "$PRESET" -crf:v:0 "$CRF")

        if [[ -n "$dovi_profile" ]]; then
            x265_params="$x265_params:dolby-vision-profile=8.1"
            ffmpeg_command+=(-dolbyvision:v:0 1 -pix_fmt:v:0 yuv420p10le)
        elif [[ "$transfer" == 'smpte2084' || "$hdr10plus" == true ]]; then
            x265_params="$x265_params:hdr10=1:hdr10-opt=1"
            ffmpeg_command+=(-pix_fmt:v:0 yuv420p10le -color_primaries:v:0 bt2020 -color_trc:v:0 smpte2084 -colorspace:v:0 bt2020nc)
        elif [[ "$transfer" == 'arib-std-b67' ]]; then
            x265_params="$x265_params:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc"
            ffmpeg_command+=(-pix_fmt:v:0 yuv420p10le -color_primaries:v:0 bt2020 -color_trc:v:0 arib-std-b67 -colorspace:v:0 bt2020nc)
        else
            ffmpeg_command+=(-pix_fmt:v:0 yuv420p)
        fi

        master_display=$(jq -r '
            def rat: split("/") | ((.[0] | tonumber) / (.[1] | tonumber));
            [.side_data_list[]? | select((.side_data_type // "") | test("Mastering display"; "i"))][0]
            | if . == null then empty else
                "G(" + (((.green_x | rat) * 50000 | round) | tostring) + "," + (((.green_y | rat) * 50000 | round) | tostring) + ")" +
                "B(" + (((.blue_x | rat) * 50000 | round) | tostring) + "," + (((.blue_y | rat) * 50000 | round) | tostring) + ")" +
                "R(" + (((.red_x | rat) * 50000 | round) | tostring) + "," + (((.red_y | rat) * 50000 | round) | tostring) + ")" +
                "WP(" + (((.white_point_x | rat) * 50000 | round) | tostring) + "," + (((.white_point_y | rat) * 50000 | round) | tostring) + ")" +
                "L(" + (((.max_luminance | rat) * 10000 | round) | tostring) + "," + (((.min_luminance | rat) * 10000 | round) | tostring) + ")"
              end
        ' <<< "$video")
        max_cll=$(jq -r '
            [.side_data_list[]? | select((.side_data_type // "") | test("Content light level"; "i"))][0]
            | if . == null then empty else ((.max_content // 0) | tostring) + "," + ((.max_average // 0) | tostring) end
        ' <<< "$video")
        [[ -n "$master_display" ]] && x265_params="$x265_params:master-display=$master_display"
        [[ -n "$max_cll" ]] && x265_params="$x265_params:max-cll=$max_cll"
        ffmpeg_command+=(-x265-params:v:0 "$x265_params")
    fi

    if [[ -n "$generated_codec" ]]; then
        ffmpeg_command+=(-c:a:"$generated_ordinal" "$generated_codec" -b:a:"$generated_ordinal" "$generated_bitrate")
        if (( generated_channels >= 6 || generated_channels <= 2 )); then
            ffmpeg_command+=(-ac:a:"$generated_ordinal" "$generated_channels")
        fi
    fi

    i=0
    while (( i < subtitle_count )); do
        subtitle_codec=$(jq -r ".[${i}].codec_name // \"unknown\"" <<< "$subtitle")
        if [[ "$subtitle_codec" == 'mov_text' ]]; then
            ffmpeg_command+=(-c:s:"$i" srt)
        fi
        i=$((i + 1))
    done

    j=0
    while (( j < text_fallback_count )); do
        generated_subtitle_ordinal=$((subtitle_count + j))
        ffmpeg_command+=(-c:s:"$generated_subtitle_ordinal" srt
            -metadata:s:s:"$generated_subtitle_ordinal" "title=${text_fallback_titles[$j]}"
            -metadata:s:s:"$generated_subtitle_ordinal" "language=${text_fallback_languages[$j]}"
            -disposition:s:"$generated_subtitle_ordinal" 0)
        j=$((j + 1))
    done

    j=0
    while (( j < attached_picture_count )); do
        attachment_output_ordinal=$((existing_attachment_count + j))
        ffmpeg_command+=(-attach "${attachment_files[$j]}"
            -metadata:s:t:"$attachment_output_ordinal" "filename=${attachment_filenames[$j]}"
            -metadata:s:t:"$attachment_output_ordinal" "mimetype=${attachment_mimetypes[$j]}")
        j=$((j + 1))
    done

    i=0
    while (( i < audio_count )); do
        audio_item=$(jq -c ".[$i]" <<< "$audio")
        old_label=$(audio_label "$audio_item")
        audio_language=$(resolved_language_code "$audio_item")
        ffmpeg_command+=(-metadata:s:a:"$i" "title=$old_label" -metadata:s:a:"$i" "language=$audio_language")
        audio_disposition=$(jq -r --argjson make_default "$([[ "$i" == "$default_ordinal" ]] && printf true || printf false)" '
            (.disposition // {})
            | .default = (if $make_default then 1 else 0 end)
            | to_entries
            | map(select(.value == 1) | .key)
            | join("+")
        ' <<< "$audio_item")
        [[ -n "$audio_disposition" ]] || audio_disposition='0'
        ffmpeg_command+=(-disposition:a:"$i" "$audio_disposition")
        i=$((i + 1))
    done

    if [[ -n "$generated_codec" ]]; then
        ffmpeg_command+=(-metadata:s:a:"$generated_ordinal" "title=$generated_label" -metadata:s:a:"$generated_ordinal" "language=$generated_language")
        if [[ "$generated_ordinal" == "$default_ordinal" ]]; then
            ffmpeg_command+=(-disposition:a:"$generated_ordinal" default)
        else
            ffmpeg_command+=(-disposition:a:"$generated_ordinal" 0)
        fi
    fi

    ffmpeg_command+=(-max_muxing_queue_size 4096 -f matroska "$temp_output")
    print_command "${ffmpeg_command[@]}"
    "${ffmpeg_command[@]}" || return 1

    expected_streams=$(jq '.streams | length' <<< "$probe")
    expected_streams=$((expected_streams - data_stream_count + text_fallback_count))
    [[ -n "$generated_codec" ]] && expected_streams=$((expected_streams + 1))
    if ! output_probe=$(ffprobe -v error -show_streams -show_format -print_format json "$temp_output"); then
        printf 'ERROR: ffprobe could not validate the converted file. Original was not renamed.\n' >&2
        return 1
    fi
    actual_streams=$(jq '.streams | length' <<< "$output_probe")
    output_video_codec=$(jq -r '[.streams[] | select(.codec_type == "video" and ((.disposition.attached_pic // 0) == 0))][0].codec_name // empty' <<< "$output_probe")
    output_dovi_profile=$(jq -r '[.streams[] | select(.codec_type == "video" and ((.disposition.attached_pic // 0) == 0))][0] | [.side_data_list[]? | select((.side_data_type // "") | test("DOVI"; "i"))][0].dv_profile // empty' <<< "$output_probe")
    output_dovi_compatibility=$(jq -r '[.streams[] | select(.codec_type == "video" and ((.disposition.attached_pic // 0) == 0))][0] | [.side_data_list[]? | select((.side_data_type // "") | test("DOVI"; "i"))][0].dv_bl_signal_compatibility_id // empty' <<< "$output_probe")
    output_hdr10plus=$(jq -r '[.streams[] | select(.codec_type == "video" and ((.disposition.attached_pic // 0) == 0))][0] | any(.side_data_list[]?; (.side_data_type // "") | test("HDR10\\+|SMPTE2094-40"; "i"))' <<< "$output_probe")
    output_transfer=$(jq -r '[.streams[] | select(.codec_type == "video" and ((.disposition.attached_pic // 0) == 0))][0].color_transfer // empty' <<< "$output_probe")
    output_pixel_format=$(jq -r '[.streams[] | select(.codec_type == "video" and ((.disposition.attached_pic // 0) == 0))][0].pix_fmt // empty' <<< "$output_probe")

    if [[ "$actual_streams" != "$expected_streams" ]]; then
        printf 'ERROR: validation found %s streams; expected %s. Original was not renamed.\n' "$actual_streams" "$expected_streams" >&2
        return 1
    fi
    while IFS= read -r disposition_type; do
        disposition_count=$(jq --arg type "$disposition_type" '[.streams[] | select(.codec_type == $type)] | length' <<< "$probe")
        expected_dispositions=$(jq -S -c --arg type "$disposition_type" '
            [.streams[] | select(.codec_type == $type)
             | (.disposition // {})
             | if $type == "audio" then del(.default) else . end
             | with_entries(select(.value == 1))]
        ' <<< "$probe")
        actual_dispositions=$(jq -S -c --arg type "$disposition_type" --argjson count "$disposition_count" '
            [.streams[] | select(.codec_type == $type)][0:$count]
            | map((.disposition // {})
                  | if $type == "audio" then del(.default) else . end
                  | with_entries(select(.value == 1)))
        ' <<< "$output_probe")
        if [[ "$actual_dispositions" != "$expected_dispositions" ]]; then
            printf 'ERROR: %s stream dispositions were not preserved. Original was not renamed.\n' "$disposition_type" >&2
            return 1
        fi
    done < <(jq -r --argjson drop_data "$([[ "$data_stream_count" -gt 0 ]] && printf true || printf false)" '
        [.streams[].codec_type]
        | unique
        | if $drop_data then map(select(. != "data")) else . end
        | .[]
    ' <<< "$probe")
    if [[ "$output_video_codec" != 'h264' && "$output_video_codec" != 'hevc' ]]; then
        printf 'ERROR: output video codec is %s. Original was not renamed.\n' "$output_video_codec" >&2
        return 1
    fi
    if [[ "$video_action" == 'dovi_convert' || "$video_action" == 'dovi_convert_strip_hdr10plus' ]]; then
        if [[ "$output_dovi_profile" != '8' || "$output_dovi_compatibility" != '1' ]]; then
            printf 'ERROR: Dolby Vision validation expected profile 8.1; file left unchanged.\n' >&2
            return 1
        fi
    fi
    if [[ "$video_action" == 'strip_hdr10plus' || "$video_action" == 'dovi_convert_strip_hdr10plus' ]]; then
        if [[ "$output_hdr10plus" == true ]]; then
            printf 'ERROR: HDR10+ metadata remains after normalization; file left unchanged.\n' >&2
            return 1
        fi
    fi
    if [[ "$video_action" == 'transcode_hevc' && "$hdr_mode" != 'SDR' ]]; then
        if [[ "$output_pixel_format" != *10* && "$output_pixel_format" != *12* ]]; then
            printf 'ERROR: HDR output is not 10/12-bit; file left unchanged.\n' >&2
            return 1
        fi
        if [[ "$transfer" == 'smpte2084' && "$output_transfer" != 'smpte2084' ]]; then
            printf 'ERROR: HDR10 transfer metadata was not preserved; file left unchanged.\n' >&2
            return 1
        fi
        if [[ "$transfer" == 'arib-std-b67' && "$output_transfer" != 'arib-std-b67' ]]; then
            printf 'ERROR: HLG transfer metadata was not preserved; file left unchanged.\n' >&2
            return 1
        fi
    fi
    if [[ -n "$generated_codec" ]]; then
        generated_check=$(ffprobe -v error -select_streams "a:$generated_ordinal" -show_entries stream=codec_name,channels -of json "$temp_output")
        if [[ "$(jq -r '.streams[0].codec_name // empty' <<< "$generated_check")" != "$generated_codec" || \
              "$(jq -r '.streams[0].channels // 0' <<< "$generated_check")" != "$generated_channels" ]]; then
            printf 'ERROR: generated compatibility audio failed validation; file left unchanged.\n' >&2
            return 1
        fi
    fi
    j=0
    while (( j < text_fallback_count )); do
        generated_subtitle_ordinal=$((subtitle_count + j))
        generated_subtitle_check=$(ffprobe -v error -select_streams "s:$generated_subtitle_ordinal" -show_entries stream=codec_name -of json "$temp_output")
        if [[ "$(jq -r '.streams[0].codec_name // empty' <<< "$generated_subtitle_check")" != 'subrip' ]]; then
            printf 'ERROR: generated SRT subtitle fallback failed validation; file left unchanged.\n' >&2
            return 1
        fi
        j=$((j + 1))
    done

    if ! chmod "$input_mode" "$temp_output"; then
        printf 'ERROR: could not preserve source file permissions; original was not renamed.\n' >&2
        return 1
    fi

    evidence_temp=$(mktemp "$directory/.$desired_stem.evidence.XXXXXX") || return 1
    TEMP_FILES+=("$evidence_temp")
    timestamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    if [[ -n "$generated_codec" ]]; then
        nfo_note="LocalMovieScanner $SCRIPT_VERSION converted $timestamp; video=$video_action; added_audio=$generated_label; evidence=${evidence_path##*/}"
    else
        nfo_note="LocalMovieScanner $SCRIPT_VERSION converted $timestamp; video=$video_action; added_audio=none; evidence=${evidence_path##*/}"
    fi

    {
        printf 'Media-to-MKV conversion evidence\n'
        printf '================================\n'
        printf 'Status: validated\n'
        printf 'Timestamp (UTC): %s\n' "$timestamp"
        printf 'Script: %s\n' "$0"
        printf 'Script version: %s\n' "$SCRIPT_VERSION"
        printf 'Source: %s\n' "$input"
        printf 'Original backup: %s\n' "$original_path"
        printf 'Output: %s\n' "$output_path"
        if [[ -n "$nfo_path" ]]; then
            printf 'NFO metadata update: %s\n' "$nfo_path"
        else
            printf 'NFO metadata update: no matching NFO found\n'
        fi
        printf '\nDecision summary\n'
        printf '%s\n' '----------------'
        printf 'Video before: %s, %s, %s\n' "$video_codec" "$pixel_format" "$hdr_mode"
        printf 'Video action: %s (%s)\n' "$video_action" "$video_reason"
        printf 'Video after: %s, %s, transfer=%s\n' "$output_video_codec" "$output_pixel_format" "${output_transfer:-unknown}"
        printf 'Input streams: %s\n' "$(jq '.streams | length' <<< "$probe")"
        printf 'Output streams: %s\n' "$actual_streams"
        printf 'Existing audio tracks retained: %s\n' "$audio_count"
        printf 'Existing subtitle tracks: %s\n' "$subtitle_count"
        printf 'SRT fallbacks added from ASS/SSA: %s\n' "$text_fallback_count"
        printf 'MOV_TEXT tracks converted to SRT: %s\n' "$mov_text_count"
        printf 'Bitmap subtitle tracks retained without OCR: %s\n' "$bitmap_subtitle_count"
        printf 'Non-playback data streams omitted: %s\n' "$data_stream_count"
        printf 'Embedded cover attachments retained: %s\n' "$attached_picture_count"
        if (( audio_count == 0 )); then
            printf 'Warning: source contains no audio; no replacement audio was generated.\n'
        fi
        if (( bitmap_subtitle_count > 0 )); then
            printf 'Warning: PGS/VobSub subtitles remain image based and may require transcoding on Apple clients.\n'
        fi
        if [[ -n "$generated_codec" ]]; then
            printf 'New audio: %s at %s kbps, sourced from audio track %s\n' \
                "$generated_label" "$((generated_bitrate / 1000))" "$((generated_source_ordinal + 1))"
        elif [[ -n "$source" && "$target_exists" == true ]]; then
            printf 'New audio: none (matching compatibility stream already exists)\n'
        else
            printf 'New audio: none (no eligible conversion source)\n'
        fi
        printf 'Default audio: %s\n' "$default_description"
        printf '\nAudio labels\n'
        printf '%s\n' '------------'
        i=0
        while (( i < audio_count )); do
            audio_item=$(jq -c ".[$i]" <<< "$audio")
            printf 'Track %s: %s\n' "$((i + 1))" "$(audio_label "$audio_item")"
            i=$((i + 1))
        done
        if [[ -n "$generated_codec" ]]; then
            printf 'Track %s: %s (new)\n' "$((generated_ordinal + 1))" "$generated_label"
        fi
        printf '\nCommands\n'
        printf '%s\n' '--------'
        if [[ "$video_action" == 'dovi_convert' || "$video_action" == 'dovi_convert_strip_hdr10plus' || "$video_action" == 'strip_hdr10plus' ]]; then
            print_command "${demux_command[@]}"
            print_command "${dovi_command[@]}"
        fi
        if (( attached_picture_count > 0 )); then
            printf '%s\n' "${attachment_command_log[@]}"
        fi
        print_command "${ffmpeg_command[@]}"
    } > "$evidence_temp" || {
        printf 'ERROR: could not write conversion evidence; original was not renamed.\n' >&2
        return 1
    }

    if ! chmod 644 "$evidence_temp"; then
        printf 'ERROR: could not set evidence-file permissions; original was not renamed.\n' >&2
        return 1
    fi

    nfo_temp=''
    if [[ -n "$nfo_path" ]]; then
        streamdetails_temp=$(mktemp "$directory/.$desired_stem.streamdetails.XXXXXX") || return 1
        TEMP_FILES+=("$streamdetails_temp")
        if ! write_nfo_streamdetails "$output_probe" "$streamdetails_temp"; then
            printf 'ERROR: could not generate NFO stream details; original was not renamed.\n' >&2
            return 1
        fi
        nfo_temp=$(mktemp "$directory/.$desired_stem.nfo.XXXXXX") || return 1
        TEMP_FILES+=("$nfo_temp")
        if ! prepare_nfo_update "$nfo_path" "$nfo_temp" "$nfo_note" "$streamdetails_temp"; then
            printf 'ERROR: could not prepare the NFO update; original was not renamed.\n' >&2
            return 1
        fi
    fi

    if ! mv "$input" "$original_path"; then
        printf 'ERROR: could not rename original file.\n' >&2
        return 1
    fi
    if ! mv "$temp_output" "$output_path"; then
        printf 'ERROR: could not install converted file; restoring original filename.\n' >&2
        mv "$original_path" "$input" || true
        return 1
    fi

    if ! mv "$evidence_temp" "$evidence_path"; then
        printf 'ERROR: could not install evidence file; restoring the original media file.\n' >&2
        mv "$output_path" "$temp_output" || true
        mv "$original_path" "$input" || true
        return 1
    fi

    if [[ -n "$nfo_path" ]]; then
        if mv "$nfo_temp" "$nfo_path"; then
            printf 'NFO: %s\n' "$nfo_path"
        else
            printf 'WARNING: conversion succeeded, but the NFO metadata could not be updated: %s\n' "$nfo_path" >&2
        fi
    fi

    printf 'DONE: %s\n' "$output_path"
    printf 'EVIDENCE: %s\n' "$evidence_path"
    cleanup_temp_files
    TEMP_FILES=('')
    return 0
}

#=========================# MAIN #==========================#

while (( $# > 0 )); do
    case "$1" in
        -p|--path)
            if (( $# < 2 )); then
                printf 'Missing path after %s\n' "$1" >&2
                usage >&2
                exit 2
            fi
            INPUT_PATH=$2
            shift 2
            ;;
        -d|--dry-run)
            DRY_RUN=true
            shift
            ;;
        --crf)
            if (( $# < 2 )) || [[ ! "$2" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
                printf 'Invalid or missing CRF value.\n' >&2
                exit 2
            fi
            CRF=$2
            shift 2
            ;;
        --preset)
            if (( $# < 2 )); then
                printf 'Missing preset after --preset.\n' >&2
                exit 2
            fi
            PRESET=$2
            shift 2
            ;;
        --fallback-language)
            if (( $# < 2 )) || [[ -z "$2" ]]; then
                printf 'Missing language code after --fallback-language.\n' >&2
                exit 2
            fi
            FALLBACK_LANGUAGE=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -* )
            printf 'Unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
        *)
            printf 'Unexpected argument: %s (use --path PATH)\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -z "$INPUT_PATH" ]]; then
    printf 'A file or directory is required. Use --path PATH.\n' >&2
    usage >&2
    exit 2
fi

if [[ ! -e "$INPUT_PATH" ]]; then
    printf 'Path not found: %s\n' "$INPUT_PATH" >&2
    exit 1
fi

export LC_ALL=C

for command_name in cp date ffmpeg ffprobe jq find mktemp perl sort stat tr; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf 'Required command not found: %s\n' "$command_name" >&2
        exit 1
    fi
done

if ! ffmpeg -hide_banner -encoders 2>/dev/null | grep 'libx265' >/dev/null; then
    printf 'This FFmpeg build does not include the libx265 encoder.\n' >&2
    exit 1
fi

if ! ffmpeg -hide_banner -encoders 2>/dev/null | grep ' eac3 ' >/dev/null; then
    printf 'This FFmpeg build does not include the E-AC3 encoder.\n' >&2
    exit 1
fi

trap cleanup_temp_files EXIT
trap 'handle_signal 129' HUP
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM

failure_count=0
processed_count=0
media_files=()

if [[ -f "$INPUT_PATH" ]]; then
    if ! supported_input_file "$INPUT_PATH"; then
        printf 'Unsupported input file: %s\n' "$INPUT_PATH" >&2
        printf 'Supported extensions: MKV, AVI, TS, M2TS, MTS, MP4, M4V\n' >&2
        exit 1
    fi
    process_file "$INPUT_PATH" || failure_count=$((failure_count + 1))
    processed_count=1
else
    while IFS= read -r -d '' media_file; do
        media_files+=("$media_file")
    done < <(find "$INPUT_PATH" -type f \
        \( -iname '*.mkv' -o -iname '*.avi' -o -iname '*.ts' -o -iname '*.m2ts' \
           -o -iname '*.mts' -o -iname '*.mp4' -o -iname '*.m4v' \) \
        ! -name '._*' ! -iname '* Original.*' -print0 | sort -z)

    for media_file in "${media_files[@]}"; do
        process_file "$media_file" || failure_count=$((failure_count + 1))
        processed_count=$((processed_count + 1))
    done
fi

if (( processed_count == 0 )); then
    printf 'No supported media files found under: %s\n' "$INPUT_PATH"
fi

if (( failure_count > 0 )); then
    printf '%s file(s) failed.\n' "$failure_count" >&2
    exit 1
fi

if [[ "$DRY_RUN" == true ]]; then
    printf '\nDry run complete. No files were changed.\n'
fi
