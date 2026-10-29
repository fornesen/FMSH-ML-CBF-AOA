#pragma once
// [上位机新增] PS 遥测适配。保留原始角度、单位、run_id 和路径未配对语义。
#include "app.hpp"
#include "beam_scan.hpp"
#include "joint_result.hpp"
#include "console/console_transport.hpp"
namespace aoa_app {
inline aoa_console::Json console_packet(const Options& o,const CaptureState& capture,
    const CaptureBatch& batch,const Analysis& analysis) {
    using aoa_console::Json;
    double capture_total=0;for(const auto& round:batch.rounds) capture_total+=round.elapsed_ms;
    Json packet={{"type","aoa"},{"run_id",batch.run_id},{"status",analysis.status},
        {"complete",analysis.complete},{"capture_mode",capture.mode},{"error",capture.error},
        {"handoff",batch.handoff_status},{"rounds",batch.completed},{"direction_round",o.capture_rounds-1},
        {"npu_every",o.npu_every},{"compact_results",o.compact_results},
        {"sample_rate_hz",o.sample_rate},{"baseband_sample_rate_hz",o.sample_rate/4},
        {"adc_bits",o.adc_bits},{"adc_encoding",o.offset_binary?"offset-binary":"twos-complement"},
        {"tcp_host",o.tcp.host},{"tcp_port",o.tcp.port},
        {"rf_hz",o.rf_hz},{"dds_hz",o.dds_hz},{"phase_sign",o.phase_sign},
        {"search",{{"start",o.angle_start},{"step",o.angle_step},{"count",o.angle_count}}},
        {"min_crossing_angle_deg",o.min_crossing_angle_deg},{"max_bearing_residual",o.max_bearing_residual},
        {"min_bin_power",o.min_bin_power},{"selected_bins",analysis.bins},
        {"analysis_ms",analysis.elapsed_ms},{"capture_ms",capture_total},
        {"capture_last_round_ms",capture.elapsed_ms},
        {"beam_weights_verified",false},{"groups",Json::array()},{"antennas",Json::array()},
        // [部署核验新增] 原槽协议未携带 PHA 独立 valid/count/overflow；不得据格式完整推断采样完整。
        {"pha_integrity","slot_format_only_no_hardware_validity"},
        {"centres",group_centres(o)},{"pl_version",capture.pl_version},
        {"written_slots",capture.written_slots},{"guard_checked",capture.guard_checked},{"guard_ok",capture.guard_ok}};
    packet["preprocessing"]={{"adc_dc_applied",capture.adc_dc_applied},
        {"adc_dc_removed_codes",capture.adc_dc_removed},
        {"ml_applied",capture.ml_preprocessing_applied},{"ml_startup_samples",o.ml_startup_samples},
        {"ml_backend",capture.ml_preprocessing_backend},
        {"fft_payload_preprocessing",capture.fft_preprocessing},
        {"ml_retained_samples",fft_length-o.ml_startup_samples},
        {"ml_window",o.ml_fft_hann?"periodic_hann":"none"},{"ml_processing_ms",capture.ml_preprocessing_ms}};
    packet["direction_frames"]=analysis.complete?analysis.direction_frames:0;
    packet["direction_rounds"]=Json::array();
    if(analysis.complete) for(std::size_t t=0;t<analysis.direction_frames;++t) packet["direction_rounds"].push_back(t);
    packet["direction_method"]=o.processing_mode=="scan6"?"four_frame_beam_power_average":analysis.direction_frames>1?"normalized_covariance_principal_vector":"single_frame";
    packet["covariance_concentration"]=analysis.covariance_concentration;
    packet["raw_validation"]=analysis.v12.position_energy?
        analysis.groups.size()==2 && analysis.groups[0].raw_validated && analysis.groups[1].raw_validated:
        analysis.joint_raw_validated;
    for(const auto& a:o.antenna) packet["antennas"].push_back({a.x,a.y});
    for(const auto& g:analysis.groups) {
        Json group={{"id",g.id},{"channels",g.channel},{"has_best",g.has_best},{"ambiguous",g.ambiguous},
            {"energy",g.energy},{"residual_ratio",g.energy>0?Json(g.residual/g.energy):Json(nullptr)},
            {"condition",g.steering_condition},{"paths",Json::array()}};
        if(g.raw_validated) group["raw_validation"]={{"available",true},
            {"passed",g.raw_residual_ratio<=o.max_bearing_residual && g.latest_residual_ratio<=o.max_bearing_residual},
            {"raw_residual_ratio",g.raw_residual_ratio},{"latest_residual_ratio",g.latest_residual_ratio},
            {"basis","original_snapshots_fixed_angles"}};
        for(std::size_t p=0;p<g.angle.size();++p) group["paths"].push_back({
            {"id",p},{"angle_deg",g.angle[p]},{"amplitude_rms",g.amplitude_rms[p]},
            {"relative_power_db",relative_path_power_db(g,p)},{"active",g.active[p]}});
        if(!g.display_angles.empty()) group["grid"]={{"angles_deg",g.display_angles},
            {"score_u8",aoa_console::base64(g.display_score)},
            {"invalid_u8",aoa_console::base64(g.display_invalid)},
            {"order","theta1,theta2"},{"score_definition","clamp(1-J/E,0,1)"}};
        packet["groups"].push_back(std::move(group));
    }
    // [六天线测角新增] 默认旧包仍可读取；联合结果独立，不冒充两个三天线组。
    packet["processing_mode"]=o.processing_mode;
    if(o.processing_mode=="angle6" || analysis.v12.enabled) packet["joint"]=joint_packet(o,analysis);
    if(analysis.v12.enabled) packet["v12"]=Json::parse(v12_json(analysis));
    if(o.processing_mode=="scan6") packet["beam_scan"]=beam_scan_packet(analysis.beam_scan);
    return packet;
}
} // namespace aoa_app
