#pragma once
// Frozen per-batch evidence, independent of optional browser preview switches.
#include "app.hpp"
#include "console/json.hpp"
#include <filesystem>
#include <cstdlib>
#include <stdexcept>
#include <fcntl.h>
#ifdef _WIN32
#include <io.h>
#else
#include <unistd.h>
#endif

namespace aoa_app {
using RecordingJson=nlohmann::json;
inline void recording_sync_file(const std::filesystem::path& path) {
#ifdef _WIN32
    const int fd=::_wopen(path.wstring().c_str(),_O_RDWR|_O_BINARY);
    if(fd<0) throw std::runtime_error("Cannot open recording for durable commit");
    const int status=::_commit(fd);::_close(fd);
#else
    const int fd=::open(path.c_str(),O_RDONLY|O_CLOEXEC);
    if(fd<0) throw std::runtime_error("Cannot open recording for durable commit");
    const int status=::fsync(fd);::close(fd);
#endif
    if(status) throw std::runtime_error("Cannot fsync recording evidence");
}
inline void recording_sync_directory(const std::filesystem::path& path) {
#ifndef _WIN32
    const auto folder=path.empty()?std::filesystem::path("."):path;
    const int fd=::open(folder.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC);
    if(fd<0) throw std::runtime_error("Cannot open recording directory");
    const int status=::fsync(fd);::close(fd);
    if(status) throw std::runtime_error("Cannot fsync recording directory");
#else
    (void)path; // Windows file handles are committed individually.
#endif
}
inline RecordingJson recording_options(const Options& o) {
    RecordingJson antennas=RecordingJson::array();
    for(const auto& ant:o.antenna) antennas.push_back({ant.x,ant.y,ant.correction.real(),ant.correction.imag()});
    return {{"processing_mode",o.processing_mode},{"joint_paths",o.joint_paths},
        {"scan_verify",o.scan_verify},{"scan_candidate_gap_db",o.scan_candidate_gap_db},
        {"scan_min_snr_db",o.scan_min_snr_db},{"scan_max_checks",o.scan_max_checks},
        {"scan_ml_weight",o.scan_ml_weight},
        {"joint_gated_selection",o.joint_gated_selection},{"joint_quality_selection",o.joint_quality_selection},
        {"paths",o.paths},{"phase_sign",o.phase_sign},{"groups",o.groups},{"antenna",antennas},
        {"rf_hz",o.rf_hz},{"sample_rate",o.sample_rate},{"dds_hz",o.dds_hz},{"fft_scale",o.fft_scale},
        {"bins",o.bins},{"auto_bins",o.auto_bins},{"band_min",o.band_min},{"band_max",o.band_max},
        {"min_bin_power",o.min_bin_power},{"angle_start",o.angle_start},{"angle_step",o.angle_step},
        {"angle_count",o.angle_count},{"ambiguity_tolerance",o.ambiguity_tolerance},
        {"min_path_ratio",o.min_path_ratio},{"max_bearing_residual",o.max_bearing_residual},
        {"min_crossing_angle_deg",o.min_crossing_angle_deg},{"adc_bits",o.adc_bits},
        {"big_endian",o.big_endian},{"offset_binary",o.offset_binary},{"adc_remove_dc",o.adc_remove_dc},
        {"ml_startup_samples",o.ml_startup_samples},{"ml_fft_hann",o.ml_fft_hann},
        {"capture_rounds",o.capture_rounds},{"console_enabled",o.console_enabled},
        {"beam",{{"enabled",o.beam.enabled},{"mode",o.beam.mode},{"angle_deg",o.beam.angle_deg},
                 {"phase_rad",o.beam.phase_rad},{"words",o.beam.words},{"command_id",o.beam.command_id}}}};
}

inline Options recording_replay_options(const RecordingJson& config) {
    Options o;
#define AOA_RESTORE(field) config.at(#field).get_to(o.field)
    if(config.contains("scan_verify")) {
        AOA_RESTORE(scan_verify);AOA_RESTORE(scan_candidate_gap_db);AOA_RESTORE(scan_min_snr_db);AOA_RESTORE(scan_max_checks);
    }
    if(config.contains("scan_ml_weight")) AOA_RESTORE(scan_ml_weight);
    AOA_RESTORE(processing_mode);AOA_RESTORE(joint_paths);AOA_RESTORE(joint_gated_selection);
    AOA_RESTORE(joint_quality_selection);AOA_RESTORE(paths);AOA_RESTORE(phase_sign);AOA_RESTORE(groups);
    AOA_RESTORE(rf_hz);AOA_RESTORE(sample_rate);AOA_RESTORE(dds_hz);AOA_RESTORE(fft_scale);
    AOA_RESTORE(bins);AOA_RESTORE(auto_bins);AOA_RESTORE(band_min);AOA_RESTORE(band_max);
    AOA_RESTORE(min_bin_power);AOA_RESTORE(angle_start);AOA_RESTORE(angle_step);AOA_RESTORE(angle_count);
    AOA_RESTORE(ambiguity_tolerance);AOA_RESTORE(min_path_ratio);AOA_RESTORE(max_bearing_residual);
    AOA_RESTORE(min_crossing_angle_deg);AOA_RESTORE(adc_bits);AOA_RESTORE(big_endian);AOA_RESTORE(offset_binary);
    AOA_RESTORE(adc_remove_dc);AOA_RESTORE(ml_startup_samples);AOA_RESTORE(ml_fft_hann);
    AOA_RESTORE(capture_rounds);AOA_RESTORE(console_enabled);
#undef AOA_RESTORE
    if(config.at("antenna").size()!=channels) throw std::runtime_error("Recording needs six antennas");
    for(std::size_t ch=0;ch<channels;++ch) {
        const auto& a=config.at("antenna").at(ch);
        o.antenna[ch].x=a.at(0);o.antenna[ch].y=a.at(1);o.antenna[ch].correction={a.at(2).get<double>(),a.at(3).get<double>()};
    }
    const auto& b=config.at("beam");o.beam.enabled=b.at("enabled");o.beam.mode=b.at("mode");
    o.beam.angle_deg=b.at("angle_deg");b.at("phase_rad").get_to(o.beam.phase_rad);b.at("words").get_to(o.beam.words);
    o.beam.command_id=b.at("command_id");o.save_results=false;o.ipc_enabled=false;
    return o;
}

inline RecordingJson recording_localization(const Analysis& a) {
    const auto& f=a.localization;
    return {{"valid",f.valid},{"status",f.status},{"x_m",f.x},{"y_m",f.y},
            {"crossing_angle_deg",f.crossing_angle_deg},{"geometry_condition",f.geometry_condition}};
}

inline void record_batch(const Options& o,const CaptureBatch& batch,const RecordingJson& result,
                         const Analysis& analysis) {
    if(!o.record_raw) return;
    RecordingJson rounds=RecordingJson::array();
    const auto root=std::filesystem::path(o.prefix).parent_path();
    for(std::size_t index=0;index<batch.rounds.size();++index) {
        const auto& r=batch.rounds[index];RecordingJson files=RecordingJson::object();
        for(const auto& artifact:std::array<std::pair<const char*,const char*>,4>{{
                {"adc","_adc.raw"},{"pl_input","_input_slots.raw"},{"pl_output","_fft_slots.raw"},{"pha","_pha.raw"}}}) {
            const auto path=std::filesystem::path(r.prefix+artifact.second);
            if(path.parent_path()!=root) throw std::runtime_error("Recording artifact outside batch folder");
            files[artifact.first]={{"name",path.filename().string()},
                {"bytes",std::filesystem::exists(path)?RecordingJson(std::filesystem::file_size(path)):RecordingJson(nullptr)}};
            if(std::filesystem::exists(path)) recording_sync_file(path);
        }
        rounds.push_back({{"index",index},{"run_id",batch.run_id},{"mode",r.mode},{"complete",r.complete},
            {"started_unix_ns",r.started_unix_ns},{"finished_unix_ns",r.finished_unix_ns},
            {"pl_version",r.pl_version},{"fft_preprocessing",r.fft_preprocessing},
            {"adc_dc_removed",r.adc_dc_removed},{"adc_dc_applied",r.adc_dc_applied},
            {"beam_registers_verified",r.beam_registers_verified},{"guard_checked",r.guard_checked},
            {"guard_ok",r.guard_ok},{"error",r.error},{"files",files}});
    }
    // Commit raw directory entries before publishing the capture manifest.
    recording_sync_directory(root);
    RecordingJson context=nullptr;
    if(const auto* file=std::getenv("AOA_RECORDING_CONTEXT")) {
        std::ifstream input(file);if(!input) throw std::runtime_error("Missing recording session context");input>>context;
    }
    RecordingJson record={{"schema","aoa.raw_capture.v1"},{"run_id",batch.run_id},
        {"completed_rounds",batch.completed},{"expected_rounds",o.capture_rounds},
        {"options",recording_options(o)},{"rounds",rounds},{"result",result},{"context",context},
        {"npu",{{"run_id",batch.run_id},{"handoff_status",batch.handoff_status},{"input_ready",batch.npu_ready}}},
        {"layout",{{"adc_samples",adc_samples},{"channels",channels},{"adc_bytes",adc_bytes},
            {"slot_bytes",slot_bytes},{"pl_input_bytes",input_bytes},{"pl_output_bytes",fft_bytes},
            {"fft_samples",fft_length},{"fft_component_bits",39},{"pha_samples",pha_samples},
            {"pha_offset",pha_offset},{"pha_sample_bytes",pha_sample_bytes},{"rounds_continuous",false}}},
        {"integrity","host_serial_START_receive_PL; hardware provides no independent frame/channel tags"}};
    if(o.processing_mode=="scan6") {
        record["scan_checks"]=result.at("beam_scan").at("verification");
        for(const auto& c:analysis.beam_scan.checks) for(const auto& name:{c.fft_file,c.pha_file})
            if(!name.empty() && std::filesystem::exists(root/name)) recording_sync_file(root/name);
    }
    record["result"]["localization_result"]=recording_localization(analysis);
    record["runtime"]={{"tcp_host",o.tcp.host},{"tcp_port",o.tcp.port},{"tcp_trigger",o.tcp.trigger},
        {"observe_waveform",o.observe_waveform},{"observe_spectrum",o.observe_spectrum},
        {"compact_results",o.compact_results},{"npu_every",o.npu_every},{"ipc_enabled",o.ipc_enabled},
        {"interval_ms",o.interval_ms},{"dry_run",o.dry_run}};
    record["capture_complete"]=batch.completed==o.capture_rounds;
    record["processing_complete"]=analysis.complete;
    record["record_status"]=batch.completed==o.capture_rounds && analysis.complete?"complete":"partial";
    const auto path=o.prefix+"_capture.json";
    auto out=text_file(path+".tmp");out<<record.dump(2)<<'\n';out.flush();out.close();
    recording_sync_file(path+".tmp");
    std::filesystem::rename(path+".tmp",path);
    recording_sync_directory(root);
}
} // namespace aoa_app
