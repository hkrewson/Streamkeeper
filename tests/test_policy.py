from streamkeeper.models import ProbeSnapshot
from streamkeeper.policy import findings_for, plan_audio, planned_audio_labels, video_mode


def snapshot(*streams, peak=None):
    return ProbeSnapshot("/media/Test.mkv", "2026-01-01T00:00:00Z", {"duration": "60"}, list(streams), peak_bitrate_bps=peak)


def video(codec="hevc", transfer="bt709", side_data=None):
    return {"codec_type": "video", "codec_name": codec, "color_transfer": transfer, "side_data_list": side_data or [], "disposition": {}}


def audio(codec, channels, bitrate=0, title="", language="eng", default=0):
    return {"codec_type": "audio", "codec_name": codec, "channels": channels, "bit_rate": str(bitrate), "tags": {"title": title, "language": language}, "disposition": {"default": default}}


def test_truehd_generates_eac3_without_removing_or_upmixing():
    plan = plan_audio(snapshot(video(), audio("truehd", 8, 4_000_000)))
    assert (plan.action, plan.codec, plan.channels, plan.bitrate) == ("transcode", "eac3", 6, 640_000)


def test_stereo_pcm_generates_aac_stereo():
    plan = plan_audio(snapshot(video(), audio("pcm_s24le", 2, 2_304_000, language="und")))
    assert (plan.codec, plan.channels, plan.bitrate, plan.language) == ("aac", 2, 192_000, "eng")


def test_existing_target_prevents_duplicate():
    plan = plan_audio(snapshot(video(), audio("truehd", 8), audio("eac3", 6, 640_000)))
    assert plan.action == "none"


def test_aac_7_1_is_retained_and_supplies_eac3_5_1_fallback():
    plan = plan_audio(snapshot(video(), audio("aac", 8, 768_000, default=1)))
    assert (plan.action, plan.source_ordinal) == ("transcode", 0)
    assert (plan.codec, plan.channels, plan.bitrate) == ("eac3", 6, 640_000)
    assert plan.default_ordinal == 1
    assert plan.label == "English E-AC3 5.1"


def test_aac_5_1_remains_a_compatible_default_without_duplication():
    plan = plan_audio(snapshot(video(), audio("aac", 6, 512_000, default=1)))
    assert plan.action == "none"
    assert plan.default_ordinal == 0


def test_commentary_is_not_used_as_best_source():
    plan = plan_audio(snapshot(video(), audio("truehd", 8, title="Director commentary"), audio("flac", 2)))
    assert plan.source_ordinal == 1
    assert plan.codec == "aac"


def test_audio_labels_include_format_channels_and_special_original_title():
    labels = planned_audio_labels(snapshot(video(), audio("truehd", 8, title="Director Commentary", language="und")))
    assert labels[0]["language"] == "eng"
    assert labels[0]["label"] == "English TrueHD 7.1 - Director Commentary"


def test_dolby_vision_seven_uses_metadata_conversion():
    mode, _, _, tools = video_mode(snapshot(video(side_data=[{"side_data_type": "DOVI configuration record", "dv_profile": 7}])))
    assert mode == "dovi_convert"
    assert "dovi_tool" in tools


def test_peak_bitrate_and_bitmap_subtitles_create_findings():
    item = snapshot(video(), audio("eac3", 6), {"codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"}, peak=950_000_000)
    assert {finding.rule_id for finding in findings_for(item)} == {"subtitle.bitmap", "network.peak"}
