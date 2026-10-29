#include "app.hpp"
#include "beam_scan.hpp"
#include "joint_result.hpp"
#include "recording.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <iomanip>
#include <locale>
#include <sstream>
#include <stdexcept>

namespace aoa_app {
namespace {
std::string quoted(const std::string& value) {
    std::ostringstream out; out<<'"';
    for(unsigned char c:value) {
        if(c=='"' || c=='\\') out<<'\\'<<c;
        else if(c<0x20) out<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<static_cast<unsigned>(c)<<std::dec;
        else out<<c;
    }
    out<<'"'; return out.str();
}
template<class T> void json_vector(std::ostream& out,const std::vector<T>& list) {
    out<<'['; for(std::size_t i=0;i<list.size();++i) { if(i) out<<','; out<<list[i]; } out<<']';
}
void json_number(std::ostream& out,double value) { if(std::isfinite(value)) out<<value; else out<<"null"; }
std::string absolute_file(const std::string& path) {
    return std::filesystem::absolute(path).lexically_normal().string();
}
void preprocessing_info(std::ostream& out,const Options& o,const CaptureState& capture) {
    out<<"{\"adc_dc_requested\":"<<o.adc_remove_dc
       <<",\"adc_dc_applied\":"<<capture.adc_dc_applied<<",\"adc_dc_removed_codes\":[";
    for(std::size_t ch=0;ch<channels;++ch) {if(ch) out<<',';out<<capture.adc_dc_removed[ch];}
    out<<"],\"adc_dc_method\":\"per_channel_rounded_signed_frame_mean_before_PL\""
       <<",\"ml_applied\":"<<capture.ml_preprocessing_applied
       <<",\"ml_backend\":"<<quoted(capture.ml_preprocessing_backend)
       <<",\"fft_payload_preprocessing\":"<<quoted(capture.fft_preprocessing)
       <<",\"ml_startup_samples\":"<<o.ml_startup_samples
       <<",\"ml_retained_samples\":"<<fft_length-o.ml_startup_samples
       <<",\"ml_fft_length\":"<<fft_length
       <<",\"ml_window\":"<<quoted(o.ml_fft_hann?"periodic_hann":"none")
       <<",\"ml_coherent_gain_compensated\":true,\"ml_iq_mean_removed\":false"
       <<",\"ml_processing_ms\":"<<capture.ml_preprocessing_ms
       <<",\"raw_adc_and_pl_fft_preserved\":true,\"pha_startup_handling\":\"unchanged\"}";
}
void capture_list(std::ostream& out,const CaptureBatch& batch) {
    out<<'[';
    for(std::size_t i=0;i<batch.rounds.size();++i) {
        if(i) out<<',';
        const auto& round=batch.rounds[i];
        out<<"{\"index\":"<<i<<",\"prefix\":"<<quoted(absolute_file(round.prefix))
           <<",\"mode\":"<<quoted(round.mode)<<",\"complete\":"<<round.complete
           <<",\"error\":"<<quoted(round.error)<<",\"adc_received_bytes\":"<<round.adc_received_bytes
           <<",\"written_slots\":"<<round.written_slots<<",\"guard_checked\":"<<round.guard_checked
           <<",\"guard_ok\":"<<round.guard_ok<<",\"pha_written_samples\":"<<round.pha_written_samples
           <<",\"pha_format_errors\":"<<round.pha_format_errors<<",\"pha_saved\":"<<round.pha_saved
           <<",\"pha_collected\":"<<round.pha_collected
           <<",\"elapsed_ms\":"<<round.elapsed_ms<<",\"adc_source\":"<<quoted(round.adc_source)
           <<",\"adc_dc_applied\":"<<round.adc_dc_applied<<",\"adc_dc_removed_codes\":[";
        for(std::size_t ch=0;ch<channels;++ch) {if(ch) out<<',';out<<round.adc_dc_removed[ch];}
        out<<"],\"fft_source\":"<<quoted(round.fft_source)
           <<",\"adc_file\":"<<quoted(absolute_file(round.prefix+"_adc.raw"))
           <<",\"input_slots_file\":"<<quoted(absolute_file(round.prefix+"_input_slots.raw"))
           <<",\"fft_slots_file\":"<<quoted(absolute_file(round.prefix+"_fft_slots.raw"))
           <<",\"pha_file\":"<<quoted(absolute_file(round.prefix+"_pha.raw"))<<'}';
    }
    out<<']';
}
std::int64_t load_pha_component(const std::uint8_t* bytes) {
    std::uint32_t value=0;
    for(unsigned b=0;b<4;++b) value|=static_cast<std::uint32_t>(bytes[b])<<(8*b);
    return value>=0x80000000u?static_cast<std::int64_t>(value)-0x100000000LL:value;
}
}
std::ofstream text_file(const std::string& path,bool append) {
    const auto parent=std::filesystem::path(path).parent_path();
    if(!parent.empty()) std::filesystem::create_directories(parent);
    const bool csv=path.size()>=4 && path.compare(path.size()-4,4,".csv")==0;
    std::ofstream out(csv?path+".tmp":path,append?std::ios::app:std::ios::trunc);
    out.exceptions(std::ios::failbit|std::ios::badbit);
    out.imbue(std::locale::classic()); out<<std::setprecision(17); return out;
}
std::ofstream result_file(const Options& o,const std::string& suffix,bool append) {
    if(!o.save_results) return {};
    if(o.compact_results && (suffix=="_fft.csv" || suffix.find("_grid.csv")!=std::string::npos ||
                            suffix.find("_scores.csv")!=std::string::npos)) return {};
    return text_file(o.prefix+suffix,append);
}
void save_bytes(const std::string& path,const std::vector<std::uint8_t>& data,std::size_t count) {
    if(count>data.size()) throw std::invalid_argument("Binary export exceeds buffer");
    const auto parent=std::filesystem::path(path).parent_path();
    if(!parent.empty()) std::filesystem::create_directories(parent);
    std::ofstream out(path,std::ios::binary|std::ios::trunc); out.exceptions(std::ios::failbit|std::ios::badbit);
    out.write(reinterpret_cast<const char*>(data.data()),static_cast<std::streamsize>(count)); out.flush();
}
void initialize_results(const Options& o) {
    if(!o.save_results) return;
    const std::array<std::pair<const char*,const char*>,7> files{{
        {"_fft.csv","record,channel,bin,baseband_hz,fft_real,fft_imag,corrected_real,corrected_imag,transport_valid\n"},
        {"_selected.csv","bin,channel,baseband_hz,fft_real,fft_imag,corrected_real,corrected_imag,mean_bin_power\n"},
        {"_ml3_grid.csv","group_id,bin,i,j,theta1_deg,theta2_deg,alpha1_real,alpha1_imag,alpha2_real,alpha2_imag,residual,independence,status\n"},
        {"_ml3_scores.csv","group_id,i,j,theta1_deg,theta2_deg,residual_sum,normalized_residual,independence,status\n"},
        {"_fit.csv","group_id,bin,path_index,angle_deg,alpha_real,alpha_imag,amplitude,phase_deg\n"},
        {"_directions.csv","group_id,antenna_count,model_paths,path_index,angle_deg,amplitude_rms,phase_first_bin_deg,relative_amplitude,active,normalized_residual,ambiguous,selected_bin_count,relative_power_db,response_independence,steering_condition,path_role\n"},
        {"_localization.csv","valid,status,x_m,y_m,group0_x_m,group0_y_m,group1_x_m,group1_y_m,group0_angle_deg,group1_angle_deg,group0_range_m,group1_range_m,crossing_angle_deg,geometry_condition\n"}
    }};
    for(const auto& file:files) {
        auto out=result_file(o,file.first);if(out.is_open()) {out<<file.second;out.flush();}
    }
}
void publish_results(const Options& o) {
    if(!o.save_results) return;
    const std::array<const char*,7> suffixes{{"_fft.csv","_selected.csv","_ml3_grid.csv","_ml3_scores.csv","_fit.csv","_directions.csv","_localization.csv"}};
    for(const auto* suffix:suffixes) {
        const auto path=o.prefix+suffix;
        if(std::filesystem::exists(path+".tmp")) std::filesystem::rename(path+".tmp",path);
    }
}
void export_fft(const Options& o,const FftData& fft) {
    if(!o.save_results || o.compact_results) return;
    if(fft.raw.size()!=fft_records || fft.corrected.size()!=fft_records || fft.valid.size()!=fft_records)
        throw std::runtime_error("FFT export requires all six channel frames");
    // Write a complete table; publish_results runs after all CSV writers close.
    const auto path=o.prefix+"_fft.csv";
    auto out=text_file(path);
    out<<"record,channel,bin,baseband_hz,fft_real,fft_imag,corrected_real,corrected_imag,transport_valid\n";
    for(std::size_t r=0;r<fft_records;++r) {
        out<<r<<','<<r/fft_length<<','<<r%fft_length<<','<<bin_hz(r%fft_length,o.sample_rate)<<','
           <<fft.raw[r].real()<<','<<fft.raw[r].imag()<<','<<fft.corrected[r].real()<<','
           <<fft.corrected[r].imag()<<','<<static_cast<int>(fft.valid[r])<<'\n';
    }
    out.flush(); out.close();
}
void export_directions(const Options& o,const Analysis& result) {
    if(!o.save_results) return;
    auto out=text_file(o.prefix+"_directions.csv",true);
    for(const auto& group:result.groups) {
        if(!group.has_best) continue;
        double maximum=0; for(auto amplitude:group.amplitude_rms) maximum=std::max(maximum,amplitude);
        for(std::size_t path=0;path<group.angle.size();++path)
            out<<group.id<<','<<group.channel.size()<<','<<group.angle.size()<<','<<path<<','<<group.angle[path]<<','
               <<group.amplitude_rms[path]<<','<<group.phase_first_bin_deg[path]<<','
               <<(maximum>0?group.amplitude_rms[path]/maximum:0)<<','<<group.active[path]<<','
               <<group.residual/group.energy<<','<<group.ambiguous<<','<<result.bins.size()<<','
               <<relative_path_power_db(group,path)<<','<<group.response_independence<<','
               <<group.steering_condition<<','<<(group.angle.size()==2?"unassigned":"single_wave")<<'\n';
    }
    out.flush();
    const auto& fix=result.localization;
    const auto centre=group_centres(o);
    auto location=text_file(o.prefix+"_localization.csv",true);
    location<<fix.valid<<','<<fix.status<<','<<fix.x<<','<<fix.y<<','
            <<centre[0][0]<<','<<centre[0][1]<<','<<centre[1][0]<<','<<centre[1][1]<<','
            <<fix.angle[0]<<','<<fix.angle[1]<<','<<fix.range[0]<<','<<fix.range[1]<<','
            <<fix.crossing_angle_deg<<','<<fix.geometry_condition<<'\n';
    location.flush();
}
std::string npu_metadata(const Options& o,const CaptureBatch& batch,const std::string& transport) {
    // [部署核验修正] 文件回放与在线 IPC 使用同一 v6 采样率契约，不能生成伪 ready 清单。
    if(o.sample_rate!=100e6) throw std::runtime_error("Current v6 NPU requires 25MS/s PHA");
    if(o.capture_rounds!=npu_rounds || batch.completed!=npu_rounds ||
       batch.pha.size()!=npu_rounds*pha_bytes || batch.rounds.size()!=npu_rounds)
        throw std::runtime_error("Four complete PHA rounds are required before NPU handoff");
    for(const auto& round:batch.rounds)
        if(round.mode!="hardware" || !round.complete || !round.pha_collected ||
           round.pha_written_samples!=pha_samples || round.pha_format_errors)
            throw std::runtime_error("An incomplete PHA round cannot be handed to NPU");
    std::ostringstream out;out<<std::boolalpha;
    out<<"{\n  \"format_version\": 1,\n  \"ready\": true,\n  \"run_id\": "<<quoted(batch.run_id)
       <<",\n  \"transport\": "<<quoted(transport)
       <<",\n  \"npu_started\": false,\n  \"capture_round_count\": "<<npu_rounds
       <<",\n  \"samples_per_round\": "<<pha_samples<<",\n  \"total_complex_samples\": "<<npu_rounds*pha_samples
       <<",\n  \"byte_count\": "<<batch.pha.size()<<",\n  \"shape\": [4,4096,2],"
       <<"\n  \"dtype\": \"int32\",\n  \"byte_order\": \"little\",\n  \"component_order\": [\"I\",\"Q\"],"
       <<"\n  \"storage_order\": \"round, sample, I/Q; I/Q varies fastest\","
       <<"\n  \"sample_rate_hz\": "<<o.sample_rate/4
       <<",\n  \"continuous_between_rounds\": false,\n  \"round_gap_seconds\": null,"
       <<"\n  \"pha_validity_basis\": \"paired_slot_structure; no independent PHA count or hardware sample timestamps\","
       <<"\n  \"raw_file\": "<<quoted(absolute_file(o.prefix+"_pha4.raw"))
       <<",\n  \"csv_file\": "<<(o.compact_results?"null":quoted(absolute_file(o.prefix+"_pha4.csv")))
       <<",\n  \"direction_round_index\": 3,\n  \"direction_round_indices\": [0,1,2,3],\n  \"last_fft_file\": "<<quoted(absolute_file(o.prefix+"_fft_slots.raw"))
       <<",\n  \"captures\": ";
    capture_list(out,batch); out<<"\n}\n"; return out.str();
}

void publish_npu_input(const Options& o,CaptureBatch& batch) {
    // [部署核验修正] --no-ipc 允许其他采样率的 AOA 研究，仍保存各轮原始数据，
    // 但这不是当前模型可消费的输入，不发布 NPU_READY 文件或 ready 标志。
    if(o.sample_rate!=100e6) {
        batch.npu_ready=false;batch.handoff_status="unsupported_sample_rate";return;
    }
    const auto metadata=npu_metadata(o,batch,"file");batch.npu_ready=true;
    if(!o.save_results) return;
    const auto raw_path=o.prefix+"_pha4.raw";
    save_bytes(raw_path+".tmp",batch.pha,batch.pha.size());
    std::filesystem::rename(raw_path+".tmp",raw_path);
    if(!o.compact_results) {
    const auto csv_path=o.prefix+"_pha4.csv";
    auto csv=text_file(csv_path);
    csv<<"round_index,sample_index,concatenated_index,time_within_round_s,pha_i,pha_q\n";
    for(std::size_t r=0;r<npu_rounds;++r) for(std::size_t s=0;s<pha_samples;++s) {
        const auto* sample=batch.pha.data()+r*pha_bytes+s*pha_sample_bytes;
        csv<<r<<','<<s<<','<<r*pha_samples+s<<','<<s/(o.sample_rate/4)<<','
           <<load_pha_component(sample)<<','<<load_pha_component(sample+4)<<'\n';
    }
    csv.flush(); csv.close(); std::filesystem::rename(csv_path+".tmp",csv_path);
    }
    // This marker is committed last; the NPU consumer must check ready/run_id.
    const auto path=o.prefix+"_npu_ready.json";
    auto out=text_file(path+".tmp");out<<metadata;out.flush();out.close();
    std::filesystem::rename(path+".tmp",path);
}

void export_metadata(const Options& o,const CaptureState& capture,const Analysis& analysis,const CaptureBatch& batch) {
    if(!o.save_results) return;
    const auto path=o.prefix+"_metadata.json";
    auto out=text_file(path+".tmp"); out<<std::boolalpha;
    const auto unix_time=std::chrono::duration_cast<std::chrono::seconds>(std::chrono::system_clock::now().time_since_epoch()).count();
    out<<"{\n  \"format_version\": 1,\n  \"record_raw\": "<<o.record_raw
       <<",\n  \"unix_time_s\": "<<unix_time
       <<",\n  \"capture_mode\": "<<quoted(capture.mode)<<",\n  \"capture_complete\": "<<capture.complete
       <<",\n  \"processing_complete\": "<<analysis.complete<<",\n  \"run_status\": "<<quoted(analysis.status)
       <<",\n  \"error\": "<<quoted(capture.error)<<",\n  \"written_slots\": "<<capture.written_slots
       <<",\n  \"adc_received_bytes\": "<<capture.adc_received_bytes
       <<",\n  \"guard_checked\": "<<capture.guard_checked<<",\n  \"guard_ok\": "<<capture.guard_ok
       <<",\n  \"pl_version\": "<<capture.pl_version<<",\n  \"input_ddr_address\": "<<capture.input_address
       <<",\n  \"output_ddr_address\": "<<capture.output_address
       <<",\n  \"channel_tags_captured\": false,\n  \"channel_order\": \"inferred: CH0 full frame through CH5 full frame\","
       <<"\n  \"pha_processed\": "<<!batch.pha.empty()
       <<",\n  \"pha_collection_enabled\": "<<(o.capture_rounds==npu_rounds && !o.dry_run)
       <<",\n  \"ignored_slot_bytes\": "<<(o.capture_rounds==npu_rounds?"[]":"[16,23]")<<','
       <<"\n  \"run_id\": "<<quoted(batch.run_id)<<",\n  \"capture_round_count\": "<<o.capture_rounds
       <<",\n  \"completed_capture_rounds\": "<<batch.completed
       <<",\n  \"batch_capture_complete\": "<<(batch.completed==o.capture_rounds)
       <<",\n  \"direction_round_index\": "<<o.capture_rounds-1
       <<",\n  \"direction_frames\": "<<(analysis.complete?analysis.direction_frames:0)
       <<",\n  \"direction_method\": "<<quoted(o.processing_mode=="scan6"?"four_frame_beam_power_average":analysis.direction_frames>1?"normalized_covariance_principal_vector":"single_frame")
       <<",\n  \"covariance_concentration\": "<<analysis.covariance_concentration
       <<",\n  \"save_results\": "<<o.save_results
       <<",\n  \"compact_results\": "<<o.compact_results<<",\n  \"npu_every\": "<<o.npu_every
       <<",\n  \"npu_handoff_status\": "<<quoted(batch.handoff_status)
       <<",\n  \"npu_input_ready\": "<<batch.npu_ready
       <<",\n  \"npu_manifest\": "<<quoted(absolute_file(o.prefix+"_npu_ready.json"))
       <<",\n  \"pha_samples_per_round\": "<<pha_samples
       <<",\n  \"pha_collected_samples\": "<<batch.pha.size()/pha_sample_bytes
       <<",\n  \"pha_byte_order\": \"little\",\n  \"pha_component_bits\": 32,\n  \"pha_slot_offset\": 16,"
       <<"\n  \"pha_validity_basis\": \"paired_slot_structure; no independent PHA count\","
       <<"\n  \"captures\": ";
    capture_list(out,batch);
    out<<",\n  \"preprocessing\": "; preprocessing_info(out,o,capture);
    out<<','
       <<"\n  \"fft_slot_bytes\": 64,\n  \"fft_payload_bytes\": 10,\n  \"fft_component_bits\": 39,"
       <<"\n  \"fft_length\": 4096,\n  \"fft_channels\": 6,\n  \"adc_samples\": 16384,"
       <<"\n  \"sample_rate_hz\": "<<o.sample_rate<<",\n  \"baseband_sample_rate_hz\": "<<o.sample_rate/4
       <<",\n  \"dds_requested_hz\": "<<o.dds_hz<<",\n  \"dds_phase_increment\": "<<dds_word(o)
       <<",\n  \"dds_programmed_hz\": "<<dds_word(o)*o.sample_rate/4294967296.0
       <<",\n  \"rf_carrier_hz\": "<<o.rf_hz
       <<",\n  \"steering_frequency_model\": \"narrowband: same RF carrier for every selected bin\","
       <<"\n  \"adc_encoding\": "<<quoted(o.offset_binary?"offset-binary":"twos-complement")
       <<",\n  \"adc_bits\": "<<o.adc_bits
       <<",\n  \"adc_endian\": "<<quoted(o.big_endian?"big":"little")
       <<",\n  \"input_slots_encoding\": \"little-endian signed16; CH0 lowest lane\","
       <<"\n  \"tcp_host\": "<<quoted(o.tcp.host)<<",\n  \"tcp_port\": "<<o.tcp.port
       <<",\n  \"adc_raw_source\": "<<quoted(o.adc_raw)<<",\n  \"fft_raw_source\": "<<quoted(o.fft_raw)
       <<",\n  \"array_source\": "<<quoted(o.array_file.empty()?"user layout: two 3-antenna groups; 433MHz half-wave spacing; CH2-CH3 gap 0.5m":o.array_file)
       <<",\n  \"angle_convention\": \"0 toward physical +Y; positive toward +X; degrees\","
       <<"\n  \"core_coordinate_mapping\": \"core_x=physical_y; core_y=physical_x\","
       <<"\n  \"phase_sign\": "<<o.phase_sign<<",\n  \"model_paths\": "
       <<(analysis.v12.position_energy?2:analysis.v12.enabled?analysis.selection.model_paths:o.processing_mode=="scan6"?0:o.processing_mode=="angle6"?(analysis.selection.enabled?analysis.selection.model_paths:o.joint_paths):o.paths)
       <<",\n  \"estimation_mode\": "<<quoted(analysis.v12.position_energy?"two_triad_ml_energy":analysis.v12.enabled?"original_ml_top30_softmatch_v1.2":o.processing_mode=="scan6"?"phase_only_beam_power_scan":o.processing_mode=="angle6"?"six_element_joint_ml":o.paths==1?"two_triad_bearings":"two_wave_per_triad")
       <<",\n  \"direction_model\": "<<quoted(analysis.v12.position_energy?"two independent triad ML fits; valid intersections ranked by path energy":analysis.v12.enabled?"full-complex uniform scan selects an original exhaustive ML Top30 path":o.processing_mode=="scan6"?
           "same-batch phase-only receive beam power":"far-field plane waves; narrowband complex ML/least squares")
       <<",\n  \"path_order\": "<<quoted(o.processing_mode=="scan6"?"descending beam power; not physical path components":o.processing_mode=="angle6"?"ascending angle across six-element fit":"ascending angle within each triad")
       <<",\n  \"path_association\": \"not_performed; path indices do not identify direct/reflected rays\""
       <<",\n  \"bearing_reference\": "<<quoted(o.processing_mode!="position"?"six-element geometric centroid; global broadside angle":"triad geometric centroid; global broadside angle")
       <<",\n  \"max_bearing_residual\": "<<o.max_bearing_residual
       <<",\n  \"min_crossing_angle_deg\": "<<o.min_crossing_angle_deg
       <<",\n  \"angle_start_deg\": "<<o.angle_start<<",\n  \"angle_step_deg\": "<<o.angle_step
       <<",\n  \"angle_count\": "<<o.angle_count<<",\n  \"fft_scale\": "<<o.fft_scale
       <<",\n  \"bin_selection\": "<<quoted(o.bins.empty()?"common calibrated power peak":"explicit natural bins")
       <<",\n  \"band_min_hz\": "<<o.band_min<<",\n  \"band_max_hz\": "<<o.band_max
       <<",\n  \"min_bin_power\": "<<o.min_bin_power
       <<",\n  \"signal_threshold_configured\": "<<(o.min_bin_power>0)
       <<",\n  \"ambiguity_tolerance\": "<<o.ambiguity_tolerance
       <<",\n  \"min_path_ratio\": "<<o.min_path_ratio<<",\n  \"selected_bins\": ";
    json_vector(out,analysis.bins);
    out<<",\n  \"positions_x_m\": [";
    for(std::size_t ch=0;ch<channels;++ch) { if(ch) out<<','; out<<o.antenna[ch].x; }
    out<<"],\n  \"positions_y_m\": [";
    for(std::size_t ch=0;ch<channels;++ch) { if(ch) out<<','; out<<o.antenna[ch].y; }
    out<<"],\n  \"compensation_file\": "<<quoted(o.compensation_file)
       <<",\n  \"correction_real\": [";
    for(std::size_t ch=0;ch<channels;++ch) { if(ch) out<<','; out<<o.antenna[ch].correction.real(); }
    out<<"],\n  \"correction_imag\": [";
    for(std::size_t ch=0;ch<channels;++ch) { if(ch) out<<','; out<<o.antenna[ch].correction.imag(); }
    out<<"],\n  \"triad_channels\": [";
    for(std::size_t g=0;g<2;++g) { if(g) out<<','; json_vector(out,std::vector<std::size_t>(o.groups[g].begin(),o.groups[g].end())); }
    out<<"],\n  \"analysis_ms_including_csv\": "<<analysis.elapsed_ms<<",\n  \"groups\": [";
    for(std::size_t g=0;g<analysis.groups.size();++g) {
        if(g) out<<',';
        const auto& group=analysis.groups[g];
        out<<"\n    {\"group_id\":"<<group.id<<",\"channels\":"; json_vector(out,group.channel);
        out<<",\"has_best\":"<<group.has_best<<",\"core_status\":"<<group.core_status
           <<",\"ambiguous\":"<<group.ambiguous<<",\"near_equal_candidates\":"<<group.near_equal_candidates
           <<",\"input_energy\":"<<group.energy<<",\"residual_sum\":";
        if(group.has_best) json_number(out,group.residual); else out<<"null";
        out<<",\"normalized_residual\":";
        if(group.has_best && group.energy>0) json_number(out,group.residual/group.energy); else out<<"null";
        out<<",\"response_independence\":"; json_number(out,group.response_independence);
        out<<",\"steering_condition\":"; json_number(out,group.steering_condition);
        out<<",\"angles_deg\":"; json_vector(out,group.angle);
        out<<",\"paths\":[";
        double maximum=0; for(auto value:group.amplitude_rms) maximum=std::max(maximum,value);
        for(std::size_t path_index=0;path_index<group.angle.size();++path_index) {
            if(path_index) out<<',';
            out<<"{\"path_index\":"<<path_index<<",\"angle_deg\":"; json_number(out,group.angle[path_index]);
            out<<",\"amplitude_rms\":"; json_number(out,group.amplitude_rms[path_index]);
            out<<",\"phase_first_bin_deg\":"; json_number(out,group.phase_first_bin_deg[path_index]);
            out<<",\"relative_amplitude\":";
            json_number(out,maximum>0?group.amplitude_rms[path_index]/maximum:0);
            out<<",\"relative_power_db\":"; json_number(out,relative_path_power_db(group,path_index));
            out<<",\"active\":"<<group.active[path_index]
               <<",\"path_role\":"<<quoted(group.angle.size()==2?"unassigned":"single_wave")<<'}';
        }
        out<<"]}";
    }
    const auto centre=group_centres(o);
    const auto& fix=analysis.localization;
    out<<"\n  ],\n  \"localization\": {\"valid\":"<<fix.valid<<",\"status\":"<<quoted(fix.status)
       <<",\"group_centres_x_m\":["<<centre[0][0]<<','<<centre[1][0]
       <<"],\"group_centres_y_m\":["<<centre[0][1]<<','<<centre[1][1]<<"],\"bearing_angles_deg\":[";
    json_number(out,fix.angle[0]); out<<','; json_number(out,fix.angle[1]);
    out<<"],\"x_m\":"; json_number(out,fix.x);
    out<<",\"y_m\":"; json_number(out,fix.y);
    out<<",\"ranges_m\":["; json_number(out,fix.range[0]); out<<','; json_number(out,fix.range[1]);
    out<<"],\"crossing_angle_deg\":"; json_number(out,fix.crossing_angle_deg);
    out<<",\"geometry_condition\":"; json_number(out,fix.geometry_condition);
    out<<"}";
    // [六天线测角新增] 连同原始 FFT 保存完整联合结果/剖面，旧定位字段原样保留。
    out<<",\n  \"processing_mode\": "<<quoted(o.processing_mode);
    if(o.processing_mode=="scan6") out<<",\n  \"beam_scan\": "<<beam_scan_packet(analysis.beam_scan).dump();
    if(o.processing_mode=="angle6" || analysis.v12.enabled) out<<",\n  \"joint\": "<<joint_packet(o,analysis).dump();
    if(analysis.v12.enabled) out<<",\n  \"v12\": "<<v12_json(analysis);
    out<<"\n}\n"; out.flush(); out.close();
    if(o.record_raw && analysis.complete) recording_sync_file(path+".tmp");
    std::filesystem::rename(path+".tmp",path);
    if(o.record_raw && analysis.complete) recording_sync_directory(std::filesystem::path(path).parent_path());
}
// The same additive object is appended to live and recorded result packets.
std::string v12_json(const Analysis& a) {
    using aoa_console::Json;
    const auto& v=a.v12;
    Json p={{"version","1.2"},
        {"scan",{{"angle_deg",v.scan.valid?Json(v.scan.peak_angle_deg):Json(nullptr)},{"coarse_angle_deg",v.coarse_angle_deg},
            {"local_angle_deg",v.local_angle_deg},{"secondary_angle_deg",v.secondary_angle_deg},
            {"valid",v.scan.valid},{"model","uniform_full_complex"},
            {"angles_deg",v.scan.angles_deg},{"relative_db",v.scan.relative_db}}},
        {"joint",{{"valid",v.valid},{"angle_deg",v.angle_deg},{"model_paths",a.selection.model_paths},
            {"path_index",v.valid?Json(a.selection.path_index):Json(nullptr)},{"angles",a.joint.angle},{"residual_ratio",v.residual_ratio},
            {"score",v.score},{"main_match",v.main_match},{"pool_counts",v.pool_counts},{"top30_rank",v.valid?Json(v.selected_rank):Json(nullptr)},
            {"secondary",{{"angle_deg",v.secondary_angle_deg},{"ml_angle_deg",v.secondary_ml_angle_deg},
                {"error_deg",v.secondary_error_deg},{"score",v.secondary_score},{"display_only",true}}}}},
        {"localization",{{"selected_id",v.selected_id.empty()?Json(nullptr):Json(v.selected_id)},
            {"reference_angle_deg",v.position_energy?Json(nullptr):Json(v.angle_deg)},{"candidates",Json::array()}}}};
    if(v.position_energy) p["localization"]["selection_method"]="triad_ml_energy";
    for(const auto& c:v.intersections) {
        Json candidate={
        {"id",c.id},{"paths",c.paths},{"valid",c.fix.valid},{"status",c.fix.status},
        {"x",c.fix.x},{"y",c.fix.y},{"bearing_deg",c.bearing_deg},{"angle_error_deg",c.angle_error_deg},
        {"angles_deg",c.fix.angle},{"ranges_m",c.fix.range},{"crossing_deg",c.fix.crossing_angle_deg},
        {"geometry_condition",c.fix.geometry_condition},
        {"direction_residuals",c.direction_residuals},{"direction_residual_ratio",c.direction_residual_ratio},
        {"angle_cost",c.angle_cost},{"selection_cost",c.selection_cost}};
        if(v.position_energy) candidate["energy_share"]=c.energy_share;
        p["localization"]["candidates"].push_back(std::move(candidate));
    }
    return p.dump();
}

double relative_path_power_db(const GroupEstimate& group,std::size_t path) {
    double maximum=0; for(auto value:group.amplitude_rms) maximum=std::max(maximum,value);
    const double amplitude=group.amplitude_rms.at(path);
    if(!(amplitude>0) || !(maximum>0)) return std::numeric_limits<double>::quiet_NaN();
    return 20*(std::log10(amplitude)-std::log10(maximum));
}
} // namespace aoa_app
