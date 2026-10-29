#pragma once

#include "aoa_ml3.hpp"
#include "tcp_receiver.h"
#include <array>
#include <cstdint>
#include <functional>
#include <fstream>
#include <limits>
#include <string>
#include <vector>

namespace aoa_app {
constexpr std::size_t channels = 6, fft_length = 4096, adc_samples = 16384;
constexpr std::size_t slot_bytes = 64, adc_payload_bytes = 12;
constexpr std::size_t fft_records = channels * fft_length;
constexpr std::size_t fft_bytes = fft_records * slot_bytes;
constexpr std::size_t input_bytes = adc_samples * slot_bytes;
constexpr std::size_t adc_bytes = adc_samples * adc_payload_bytes;
constexpr std::size_t npu_rounds = 4, pha_samples = 4096, pha_offset = 16;
constexpr std::size_t pha_sample_bytes = 8, pha_bytes = pha_samples * pha_sample_bytes;
constexpr double light_speed = 299792458.0;
constexpr double pi = 3.14159265358979323846;

struct Antenna {
    double x = 0, y = 0;
    AoaComplex correction{1, 0};
};
// [波束控制新增] 一个四轮批次冻结同一组权值；每轮 PL 复位后重写。
struct BeamWeights {
    bool enabled=false;
    std::string mode="equal", command_id;
    double angle_deg=0;
    std::array<double,channels> phase_rad{};
    std::array<std::uint64_t,channels> words{};
};
struct Options {
    BeamWeights beam;
    // [合成IQ观测新增] 独立控制显示负载，不改变采集、波束或NPU输入。
    bool observe_waveform = false, observe_spectrum = false;
    // [六天线测角新增] 独立 PS 模式；原两组三天线算法和 PL 均不改变。
    std::string processing_mode = "position";
    bool scan_verify = true;
    double scan_candidate_gap_db = 1.5, scan_min_snr_db = 8;
    std::size_t scan_max_checks = 2;
    // Zero preserves scan-only behavior. Fusion uses the same four snapshots.
    double scan_ml_weight = 0;
    int joint_paths = 3;
    bool joint_gated_selection = true;
    // Experimental selection is opt-in: offline replay exposed branch switches.
    bool joint_quality_selection = false;
    // [上位机新增] 仅在遥测启用时保留显示网格，不改变 ML 搜索和最优解。
    bool console_enabled = false;
    bool save_results = false, once = false, ipc_enabled = true;
    // [定位优先调度新增] 仅精简重复文本导出，保留原始 ADC/FFT/PHA；搜索精度不变。
    bool compact_results = false;
    bool record_raw = false;
    std::size_t npu_every = 1;
    std::size_t max_batches = 0, interval_ms = 0, retry_delay_ms = 500;
    std::string ipc_name = "/icraft_pha4";
    std::string prefix = "results/aoa", fft_raw, adc_raw, array_file, compensation_file;
    std::vector<std::string> fft_sources, adc_sources;
    std::size_t capture_rounds = npu_rounds;
    bool capture_rounds_explicit = false;
    tcp_receiver::Options tcp;
    bool big_endian = false, offset_binary = true, dry_run = false;
    // [7100实采接口修正] 12/14位原码高位补零，送PL前扩展为有符号16位。
    unsigned adc_bits = 16;
    // Physical ADC DC is removed before the DDC; never subtract the IQ mean.
    bool adc_remove_dc = false;
    std::size_t ml_startup_samples = 0;
    bool ml_fft_hann = false;
    // Offline FFT files lack registers; declare the stored payload explicitly.
    std::string fft_preprocessing = "raw";
    double rf_hz = 433e6, sample_rate = 100e6, dds_hz = 12.5e6;
    double timeout = 30, fft_scale = 1.0 / fft_length;
    double angle_start = -80, angle_step = 1;
    std::size_t angle_count = 161, auto_bins = 1;
    int paths = 2, phase_sign = 1;
    double band_min = -250e3, band_max = 250e3;
    double min_bin_power = 0, ambiguity_tolerance = 1e-6, min_path_ratio = 0.1;
    double max_bearing_residual = 0.1, min_crossing_angle_deg = 1;
    std::vector<std::size_t> bins;
    std::array<Antenna, channels> antenna;
    std::array<std::array<std::size_t, 3>, 2> groups{{{{0, 1, 2}}, {{3, 4, 5}}}};
};
struct CaptureState {
    std::int64_t started_unix_ns=0,finished_unix_ns=0;
    // 仅 AXI 配置寄存器读回，不能冒充旋转器内部权值/实际方向图读回。
    bool beam_registers_verified=false;
    std::string mode, error;
    std::string prefix, adc_source, fft_source;
    bool ready = false, complete = false, guard_checked = false, guard_ok = false;
    std::size_t written_slots = 0, adc_received_bytes = 0;
    std::uint32_t pl_version = 0;
    std::uint64_t input_address = 0, output_address = 0;
    std::size_t pha_written_samples = 0, pha_format_errors = 0;
    bool pha_saved = false, pha_collected = false;
    bool adc_dc_applied = false, ml_preprocessing_applied = false;
    std::string ml_preprocessing_backend = "none";
    std::string fft_preprocessing = "unknown";
    std::array<std::int32_t,channels> adc_dc_removed{};
    double ml_preprocessing_ms = 0;
    double elapsed_ms = 0;
};
struct CaptureBatch {
    std::string run_id;
    std::size_t completed = 0;
    bool npu_ready = false;
    std::string handoff_status = "not_ready";
    std::vector<CaptureState> rounds;
    std::vector<std::uint8_t> pha;
    // Corrected independent-channel FFTs, in acquisition order within this batch.
    std::vector<std::vector<AoaComplex>> fft_snapshots;
    // Only the last original round is reused for candidate checks. Never append
    // these checks to the four frozen-beam PHA frames delivered to the NPU.
    std::vector<std::uint8_t> scan_input;
};
struct FftData {
    std::vector<AoaComplex> raw, corrected;
    std::vector<std::uint8_t> valid;
    std::vector<std::vector<AoaComplex>> snapshots;
};
struct GroupEstimate {
    bool raw_validated=false;
    double raw_residual_ratio=1,latest_residual_ratio=1;
    // [上位机新增] 显示网格 i*size+j 对应 (theta1_i,theta2_j)。最大 161x161。
    std::vector<double> display_angles;
    std::vector<std::uint8_t> display_score, display_invalid;
    int id = 0;
    std::vector<std::size_t> channel;
    bool has_best = false, ambiguous = false;
    int core_status = -3;
    std::size_t near_equal_candidates = 0;
    double energy = 0, residual = 0;
    double response_independence = std::numeric_limits<double>::quiet_NaN();
    double steering_condition = std::numeric_limits<double>::quiet_NaN();
    std::vector<double> angle, amplitude_rms, phase_first_bin_deg;
    std::vector<bool> active;
};
struct Localization {
    bool valid = false;
    std::string status = "not_run";
    double x = std::numeric_limits<double>::quiet_NaN();
    double y = std::numeric_limits<double>::quiet_NaN();
    std::array<double, 2> angle{{std::numeric_limits<double>::quiet_NaN(),
                                 std::numeric_limits<double>::quiet_NaN()}};
    std::array<double, 2> range{{std::numeric_limits<double>::quiet_NaN(),
                                 std::numeric_limits<double>::quiet_NaN()}};
    double crossing_angle_deg = std::numeric_limits<double>::quiet_NaN();
    double geometry_condition = std::numeric_limits<double>::quiet_NaN();
};
struct JointQuality {
    int paths=0;
    bool passed=false;
    std::string reason="no_candidate";
    double raw_residual=1, latest_residual=1, contribution=0;
    std::vector<double> angles, amplitudes;
};
struct JointCandidate {
    int model_paths=0, path_index=-1;
    double angle=0, score=0;
    std::vector<int> support_models, support_paths;
};
struct ScanCandidate {
    std::size_t id=0;
    double angle_deg=0, normalized_power=0, relative_db=0;
};
struct ScanCheck {
    std::size_t candidate_id=0;
    std::string capture_id, fft_file, pha_file, source_input_file, error;
    double angle_deg=0, expected_power=0, observed_power=0;
    double relative_error=1, input_shape_error=1;
    bool registers_verified=false, valid=false;
};
struct ScanFusion {
    bool enabled=false,valid=false,ml_available=false,ml_qualified=false;
    std::string status="disabled";
    double requested_ml_weight=0,effective_ml_weight=0,ml_elapsed_ms=0;
    double scan_angle_deg=std::numeric_limits<double>::quiet_NaN();
    double ml_angle_deg=std::numeric_limits<double>::quiet_NaN();
    double angle_deg=std::numeric_limits<double>::quiet_NaN();
    GroupEstimate ml_model;
    bool ml_selection_accepted=false;
    std::string ml_selection_status="not_run";
    int ml_path_index=-1,ml_model_paths=0;
};
struct BeamScan {
    bool valid=false, ambiguous=false, verification_enabled=false, restore_verified=false;
    std::string status="not_run", verification_status="not_run";
    std::vector<double> angles_deg, normalized_power, relative_db;
    std::vector<ScanCandidate> candidates;
    std::vector<ScanCheck> checks;
    std::size_t frame_count=0, source_round=0;
    double peak_angle_deg=0, peak_normalized_power=0, peak_gap_db=0;
    double input_power=0, local_snr_db=0, global_peak_frequency_hz=0;
    double elapsed_ms=0, verification_ms=0;
    ScanFusion fusion;
};
// Per-batch decisions; fallback candidates never acquire quality-qualified status.
struct JointSelection {
    bool enabled = false, accepted = false;
    bool quality_cascade=false, fallback=false;
    std::vector<JointQuality> models;
    std::vector<JointCandidate> candidates;
    double selected_score=0;
    std::string status = "not_run";
    int path_index = -1, model_paths = 3;
    // Attempted models in descending order; the last entry describes this frame's result.
    std::vector<std::string> stages;
    double single_angle = 0, lower = 0, upper = 0;
    double half_width = 10, dual_tolerance = 5, amplitude_ratio = 0.1;
    double residual_budget = 0.001;
    double global_residual = 0, residual_gap = 0, dual_distance = 0;
    std::vector<double> dual_angles, dual_amplitudes;
};
// V1.2 is an additive board result. Scores never use secondary diagnostics.
struct V12Intersection {
    std::string id;
    std::array<int,2> paths{};
    Localization fix;
    double bearing_deg=std::numeric_limits<double>::quiet_NaN();
    double energy_share=std::numeric_limits<double>::quiet_NaN();
    double angle_error_deg=std::numeric_limits<double>::quiet_NaN();
    std::array<double,2> direction_residuals{{std::numeric_limits<double>::quiet_NaN(),
                                            std::numeric_limits<double>::quiet_NaN()}};
    double direction_residual_ratio=std::numeric_limits<double>::quiet_NaN();
    double angle_cost=std::numeric_limits<double>::quiet_NaN();
    double selection_cost=std::numeric_limits<double>::quiet_NaN();
};
struct V12Result {
    BeamScan scan;
    bool enabled=false, valid=false, position_energy=false;
    double angle_deg=std::numeric_limits<double>::quiet_NaN();
    double coarse_angle_deg=std::numeric_limits<double>::quiet_NaN();
    double local_angle_deg=std::numeric_limits<double>::quiet_NaN();
    double secondary_angle_deg=std::numeric_limits<double>::quiet_NaN();
    double secondary_ml_angle_deg=std::numeric_limits<double>::quiet_NaN();
    double secondary_error_deg=std::numeric_limits<double>::quiet_NaN();
    double secondary_score=std::numeric_limits<double>::quiet_NaN();
    double residual_ratio=std::numeric_limits<double>::quiet_NaN();
    double score=0,main_match=0;
    std::array<std::size_t,3> pool_counts{};
    int selected_rank=0;
    std::string selected_id;
    std::vector<V12Intersection> intersections;
};
struct Analysis {
    bool complete = false;
    std::string status = "not_run";
    std::vector<std::size_t> bins;
    std::vector<GroupEstimate> groups;
    // [六天线测角新增] 不放入 groups，不产生交点/虚构距离。
    GroupEstimate joint;
    JointSelection selection;
    BeamScan beam_scan;
    V12Result v12;
    bool joint_raw_validated=false;
    JointQuality joint_raw_quality;
    std::vector<double> joint_profile_angles, joint_profile_db;
    // [联合评分显示新增] 严格递增 K 元角度索引的词典序组合；遥测或保存结果时保留。
    // 0..254 对应 1-J/E，255 表示退化组合；只量化显示，不参与最优解/波束控制。
    std::vector<std::uint8_t> joint_score;
    Localization localization;
    double elapsed_ms = 0;
    std::size_t direction_frames=1;
    double covariance_concentration=1;
};

void usage(const char* program);
Options parse_options(int argc, char** argv);
std::uint32_t dds_word(const Options& options);
double bin_hz(std::size_t bin, double sample_rate);
bool slot_valid(const std::uint8_t* slot);
std::vector<std::uint8_t> load_exact(const std::string& path, std::size_t bytes);
std::vector<std::uint8_t> adc_to_slots(const Options&, const std::vector<std::uint8_t>&,
                                    std::array<std::int32_t,channels>* removed_dc = nullptr);
void capture_fft(const Options&, const std::vector<std::uint8_t>& input,
                 std::vector<std::uint8_t>& output, CaptureState&,
                 const std::function<bool()>& cancelled);
void restore_beam(const BeamWeights&, const std::function<bool()>& cancelled);
FftData decode_fft(const Options&, const std::vector<std::uint8_t>&, CaptureState&);
void initialize_batch(const Options&, CaptureBatch&);
void collect_batch(const Options&, CaptureBatch&, CaptureState&, std::vector<std::uint8_t>&,
                   const std::function<bool()>& cancelled);
void publish_npu_input(const Options&, CaptureBatch&);
std::string npu_metadata(const Options&, const CaptureBatch&, const std::string& transport);
Analysis analyze(const Options&, const FftData&, const std::function<bool()>& cancelled);
std::array<std::array<double, 2>, 2> group_centres(const Options&);
Localization localize(const Options&, const std::vector<GroupEstimate>&);
void localize_v12(const Options&, Analysis&);
void localize_v12(const Options&, const FftData& observation, Analysis&);
std::string v12_json(const Analysis&);

std::ofstream text_file(const std::string& path, bool append = false);
std::ofstream result_file(const Options&, const std::string& suffix, bool append = false);
void save_bytes(const std::string& path, const std::vector<std::uint8_t>& data,
                std::size_t count);
void initialize_results(const Options&);
void publish_results(const Options&);
void export_fft(const Options&, const FftData&);
void export_directions(const Options&, const Analysis&);
void export_metadata(const Options&, const CaptureState&, const Analysis&, const CaptureBatch&);
double relative_path_power_db(const GroupEstimate&, std::size_t path);
} // namespace aoa_app
