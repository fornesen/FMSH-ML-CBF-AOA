#include "app.hpp"
#include "joint_ml.hpp"
#include "pl_fft_input.hpp"
#include "ipc_name.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <limits>
#include <set>
#include <sstream>
#include <stdexcept>

namespace aoa_app {
namespace {
double number(const std::string& text) {
    std::size_t used = 0;
    const double value = std::stod(text, &used);
    if (used != text.size() || !std::isfinite(value))
        throw std::invalid_argument("Expected a finite number: " + text);
    return value;
}
std::size_t integer(const std::string& text, std::size_t minimum, std::size_t maximum) {
    const double value = number(text);
    if (std::trunc(value) != value || value < static_cast<double>(minimum) ||
        value > static_cast<double>(maximum))
        throw std::invalid_argument("Integer outside allowed range: " + text);
    return static_cast<std::size_t>(value);
}
std::vector<std::size_t> indices(const std::string& text, std::size_t maximum) {
    std::vector<std::size_t> result;
    std::istringstream in(text);
    std::string item;
    if (text.empty() || text.back() == ',') throw std::invalid_argument("Empty index list entry");
    while (std::getline(in, item, ',')) result.push_back(integer(item, 0, maximum));
    std::set<std::size_t> unique(result.begin(), result.end());
    if (result.empty() || unique.size() != result.size())
        throw std::invalid_argument("Index list must be nonempty with no duplicates");
    return result;
}
void load_array(Options& o) {
    std::ifstream file(o.array_file);
    if (!file) throw std::runtime_error("Cannot open array CSV: " + o.array_file);
    std::string line;
    if (!std::getline(file, line)) throw std::invalid_argument("Empty array CSV");
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line != "channel,x_m,y_m,correction_real,correction_imag")
        throw std::invalid_argument("Array CSV header must be channel,x_m,y_m,correction_real,correction_imag");
    std::array<bool, channels> seen{};
    std::size_t rows = 0;
    while (std::getline(file, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        std::istringstream row(line);
        std::array<std::string, 5> field;
        for (auto& value : field)
            if (!std::getline(row, value, ',')) throw std::invalid_argument("Array CSV needs five columns");
        std::string extra;
        if (std::getline(row, extra, ',') || line.back() == ',')
            throw std::invalid_argument("Array CSV has extra/empty column");
        const auto ch = integer(field[0], 0, channels - 1);
        if (seen[ch]) throw std::invalid_argument("Duplicate array channel");
        seen[ch] = true; ++rows;
        o.antenna[ch] = {number(field[1]), number(field[2]), {number(field[3]), number(field[4])}};
    }
    if (rows != channels) throw std::invalid_argument("Array CSV must contain all six physical channels");
}
void load_compensation(Options& o) {
    std::ifstream file(o.compensation_file);
    if (!file) throw std::runtime_error("Cannot open channel compensation CSV: " + o.compensation_file);
    std::string line;
    if (!std::getline(file, line)) throw std::invalid_argument("Empty channel compensation CSV");
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line != "channel,rf_hz,correction_real,correction_imag,phase_center_dx_m")
        throw std::invalid_argument("Compensation CSV header must be channel,rf_hz,correction_real,correction_imag,phase_center_dx_m");
    std::array<AoaComplex, channels> correction{};
    std::array<double, channels> phase_center_dx{};
    std::array<bool, channels> seen{};
    std::size_t rows = 0;
    while (std::getline(file, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        std::istringstream row(line);
        std::array<std::string, 5> field;
        for (auto& value : field)
            if (!std::getline(row, value, ',')) throw std::invalid_argument("Compensation CSV needs five columns");
        std::string extra;
        if (std::getline(row, extra, ',') || line.back() == ',')
            throw std::invalid_argument("Compensation CSV has extra/empty column");
        const auto ch = integer(field[0], 0, channels - 1);
        if (seen[ch]) throw std::invalid_argument("Duplicate compensation channel");
        const double rf_hz = number(field[1]);
        if (rf_hz <= 0 || std::abs(rf_hz - o.rf_hz) > 1.0)
            throw std::invalid_argument("Compensation RF must match --rf-hz within 1 Hz");
        correction[ch] = {number(field[2]), number(field[3])};
        phase_center_dx[ch] = number(field[4]);
        seen[ch] = true; ++rows;
    }
    if (rows != channels) throw std::invalid_argument("Compensation CSV must contain all six physical channels");
    // Keep the source array unchanged. All steering and beam paths share the
    // effective phase centres; legacy gains must not be applied a second time.
    for (std::size_t ch = 0; ch < channels; ++ch) {
        o.antenna[ch].correction = correction[ch];
        o.antenna[ch].x += phase_center_dx[ch];
    }
}
} // namespace

void usage(const char* program) {
    std::cout << "Usage: " << program << " [options]\n"
      "  Default: continuous capture, no result files, nonblocking PHA IPC.\n"
      "  --save-results        Enable raw/CSV/JSON results. Default: no disk output.\n"
      "  --compact-results     Keep raw/results, omit exhaustive FFT/grid/PHA CSV copies.\n"
      "  --npu-every N         Publish NPU input every N batches (default1); DOA runs every batch.\n"
      "  --once                Process one batch and exit.\n"
      "  --max-batches N       Stop after N attempted batches; default0 is unlimited.\n"
      "  --interval-ms N       Delay between successful batches (default0).\n"
      "  --retry-delay-ms N    Delay after capture/analysis failure (default500).\n"
      "  --ipc-name /NAME      Shared RAM handoff name (default /icraft_pha4).\n"
      "  --no-ipc              Disable live handoff, e.g. for isolated offline replay.\n"
      "  --output-prefix PATH   Result prefix (default results/aoa).\n"
      "  --fft-raw FILE         Offline 24576 x 64-byte FFT slots; no board/TCP access.\n"
      "  --fft-preprocessing TYPE  Offline payload: raw (default) or pl-hann64.\n"
      "  --adc-raw FILE         Packed 16384 x 6 x 16-bit ADC input instead of TCP.\n"
      "                         Repeat --fft-raw/--adc-raw four times for a four-round replay.\n"
      "  --capture-rounds 1|4   Default 4 for TCP/hardware; file count selects replay count.\n"
      "                         Online locate/identify both use four FFT snapshots; identify also hands PHA to NPU.\n"
      "  --dry-run              Receive/convert ADC only; no board access.\n"
      "  --tcp-host HOST        Default 192.168.10.50; --tcp-port PORT defaults to 7.\n"
      "  --tcp-trigger TEXT     Default START; --tcp-timeout SECONDS defaults to 10.\n"
      "  --adc-encoding TYPE    offset-binary (default) or twos-complement.\n"
      "  --adc-bits 12|14|16    Payload significant bits, low aligned (default16).\n"
      "  --adc-remove-dc        Subtract each ADC frame's rounded signed mean before PL.\n"
      "  --ml-startup-samples N Exclude first N IQ samples from ML only (default0).\n"
      "  --ml-fft-window TYPE   none (default) or hann on the retained ML IQ segment.\n"
      "  --record-raw           Save paired raw stages, options and final result for PC replay.\n"
      "  --observe-waveform     Report all4096 PL beamformed IQ samples. Defaultoff.\n"
      "  --observe-spectrum     Report full4096bin FFT of PL beamformed IQ. Defaultoff.\n"
      "  --adc-endian ORDER     little (default) or big.\n"
      "  --rf-hz HZ             RF carrier (default 433000000); not IF/baseband.\n"
      "  --sample-rate HZ       ADC rate (default 100000000), decimation fixed at 4.\n"
      "  --dds-hz HZ            Mixer setting (default 12500000); --timeout SECONDS = 30.\n"
      "  --paths 1|2            Default 2: original ML3, two arrivals per triad (4 rows).\n"
      "                         1: one bearing per triad, then cross localization.\n"
      "  --processing-mode position|angle6|scan6  Default position; scan6 uses four-frame beam power.\n"
      "  --scan-max-checks N    1..3 cached-ADC PL candidate checks; default2.\n"
      "  --scan-candidate-gap-db DB  Check close runner-ups within default1.5dB.\n"
      "  --scan-min-snr-db DB   Local target-band peak/noise gate; default8dB.\n"
      "  --scan-ml-weight W    Same-batch sine-domain ML weight, 0..1; default0.\n"
      "  --no-scan-verify       Disable actual PL candidate verification.\n"
      "  --joint-paths 1|2|3    Angle6 model order; default 3 enables gated 3->2->1 fallback.\n"
      "  --joint-selection gated|quality|strongest  Original cascade (default), experimental quality/energy, or strongest.\n"
      "  --array FILE           Six-row position/calibration CSV; see config/array.csv.\n"
      "  --compensation FILE    Six-row channel,rf_hz,correction_real,correction_imag,phase_center_dx_m CSV.\n"
      "                         Replaces array gains and adds effective phase-centre X offsets.\n"
      "  --group0 LIST          Three 0-based channels (default 0,1,2).\n"
      "  --group1 LIST          Three 0-based channels (default 3,4,5); groups partition six.\n"
      "  --phase-sign -1|1      Default +1 matches the coherent low-side FM model.\n"
      "  --bins LIST            Common natural-order bins, e.g. 0,4,4092 (max 32).\n"
      "  --auto-bins N          Default 1; strongest common-power bins (1..32).\n"
      "  --band-min-hz HZ       Auto-selection baseband lower bound (default -250000).\n"
      "  --band-max-hz HZ       Auto-selection baseband upper bound (default 250000).\n"
      "  --min-bin-power P      Minimum mean calibrated power; default 0 only rejects zero.\n"
      "  --fft-scale SCALE      Common FFT scale (default 1/4096).\n"
      "  --angle-start DEG      Broadside angle start (default -80).\n"
      "  --angle-step DEG       Positive step (default 1); --angle-count N defaults to 161.\n"
      "  --ambiguity-tolerance  Normalized near-equal loss threshold (default 1e-6).\n"
      "  --min-path-ratio R     Two-path display flag relative to strongest (default 0.1).\n"
      "  --max-bearing-residual R  Localization gate on each J/E (default 0.1, range 0..1).\n"
      "  --min-crossing-angle DEG  Minimum acute ray angle (default 1, strictly 0..90).\n"
      "Angles: array on X, zero toward +Y, positive toward +X.\n"
      "Four-round PHA is signed32 LE I/Q; forward to an idle consumer, skip if busy/absent.\n"
      "Two-path indices are sorted by angle; direct/reflection identities are unassigned.\n";
}

Options parse_options(int argc, char** argv) {
    Options o;
    // Two consecutive three-antenna groups, each spaced by lambda/2 at 433 MHz.
    // The 0.5 m group gap is CH2 to CH3, not the distance between group centres.
    // These are fixed physical coordinates; --rf-hz changes only the steering wave number.
    const double d = light_speed / 433e6 / 2;
    const std::array<double, 6> position{{0, d, 2*d, 2*d + 0.5, 3*d + 0.5, 4*d + 0.5}};
    const double centre = position.back() / 2;
    for (std::size_t ch = 0; ch < channels; ++ch) o.antenna[ch].x = position[ch] - centre;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        auto value = [&]() -> std::string {
            if (++i >= argc) throw std::invalid_argument("Missing value for " + arg);
            return argv[i];
        };
        if (arg == "--save-results") o.save_results = true;
        else if (arg == "--record-raw") {o.record_raw=true;o.save_results=true;}
        else if (arg == "--compact-results") o.compact_results = true;
        else if (arg == "--npu-every") o.npu_every = integer(value(),1,1000000);
        else if (arg == "--once") o.once = true;
        else if (arg == "--max-batches") o.max_batches = integer(value(),0,1000000000);
        else if (arg == "--interval-ms") o.interval_ms = integer(value(),0,3600000);
        else if (arg == "--retry-delay-ms") o.retry_delay_ms = integer(value(),0,3600000);
        else if (arg == "--ipc-name") o.ipc_name = value();
        else if (arg == "--no-ipc") o.ipc_enabled = false;
        else if (arg == "--dry-run") o.dry_run = true;
        else if (arg == "--adc-remove-dc") o.adc_remove_dc = true;
        else if (arg == "--ml-startup-samples") o.ml_startup_samples = integer(value(),0,fft_length-2);
        else if (arg == "--ml-fft-window") {
            const auto s=value();
            if(s!="none" && s!="hann") throw std::invalid_argument("ML FFT window must be none or hann");
            o.ml_fft_hann=s=="hann";
        }
        else if (arg == "--observe-waveform") o.observe_waveform = true;
        else if (arg == "--observe-spectrum") o.observe_spectrum = true;
        else if (arg == "--output-prefix") o.prefix = value();
        else if (arg == "--fft-raw") { o.fft_raw = value(); o.fft_sources.push_back(o.fft_raw); }
        else if (arg == "--fft-preprocessing") {
            o.fft_preprocessing = value();
            if (o.fft_preprocessing != "raw" && o.fft_preprocessing != "pl-hann64")
                throw std::invalid_argument("fft-preprocessing must be raw or pl-hann64");
        }
        else if (arg == "--adc-raw") { o.adc_raw = value(); o.adc_sources.push_back(o.adc_raw); }
        else if (arg == "--capture-rounds") {
            o.capture_rounds = integer(value(),1,npu_rounds); o.capture_rounds_explicit = true;
            if(o.capture_rounds!=1 && o.capture_rounds!=npu_rounds)
                throw std::invalid_argument("capture-rounds must be 1 or 4");
        }
        else if (arg == "--tcp-host") o.tcp.host = value();
        else if (arg == "--tcp-port") o.tcp.port = static_cast<std::uint16_t>(integer(value(), 1, 65535));
        else if (arg == "--tcp-trigger") { auto s = value(); o.tcp.trigger.assign(s.begin(), s.end()); }
        else if (arg == "--tcp-timeout") o.tcp.timeout_seconds = number(value());
        else if (arg == "--adc-bits") {
            o.adc_bits = static_cast<unsigned>(integer(value(),12,16));
            if(o.adc_bits!=12 && o.adc_bits!=14 && o.adc_bits!=16)
                throw std::invalid_argument("adc-bits must be 12, 14 or 16");
        }
        else if (arg == "--adc-encoding") {
            auto s = value();
            if (s != "offset-binary" && s != "twos-complement") throw std::invalid_argument("Invalid ADC encoding");
            o.offset_binary = s == "offset-binary";
        } else if (arg == "--adc-endian") {
            auto s = value();
            if (s != "little" && s != "big") throw std::invalid_argument("Invalid ADC byte order");
            o.big_endian = s == "big";
        } else if (arg == "--rf-hz") o.rf_hz = number(value());
        else if (arg == "--sample-rate") o.sample_rate = number(value());
        else if (arg == "--dds-hz") o.dds_hz = number(value());
        else if (arg == "--timeout") o.timeout = number(value());
        else if (arg == "--paths") o.paths = static_cast<int>(integer(value(), 1, 2));
        else if (arg == "--no-scan-verify") o.scan_verify = false;
        else if (arg == "--scan-max-checks") o.scan_max_checks = integer(value(),1,3);
        else if (arg == "--scan-candidate-gap-db") o.scan_candidate_gap_db = number(value());
        else if (arg == "--scan-min-snr-db") o.scan_min_snr_db = number(value());
        else if (arg == "--scan-ml-weight") o.scan_ml_weight = number(value());
        else if (arg == "--processing-mode") o.processing_mode = value();
        else if (arg == "--joint-paths") o.joint_paths = static_cast<int>(integer(value(), 1, 3));
        else if (arg == "--joint-selection") {
            const auto s=value();
            if(s!="gated" && s!="legacy-gated" && s!="quality" && s!="strongest")
                throw std::invalid_argument("joint-selection must be gated, quality, legacy-gated or strongest");
            o.joint_gated_selection=s!="strongest";
            o.joint_quality_selection=s=="quality";
        }
        else if (arg == "--phase-sign") { const auto s = number(value()); if (s != -1 && s != 1) throw std::invalid_argument("phase sign must be +/-1"); o.phase_sign = static_cast<int>(s); }
        else if (arg == "--array") o.array_file = value();
        else if (arg == "--compensation") {
            o.compensation_file = value();
            if (o.compensation_file.empty()) throw std::invalid_argument("Empty compensation file path");
        }
        else if (arg == "--group0" || arg == "--group1") {
            auto list = indices(value(), channels - 1);
            if (list.size() != 3) throw std::invalid_argument("Each ML3 group needs three channels");
            std::copy(list.begin(), list.end(), o.groups[arg == "--group0" ? 0 : 1].begin());
        } else if (arg == "--bins") o.bins = indices(value(), fft_length - 1);
        else if (arg == "--auto-bins") o.auto_bins = integer(value(), 1, 32);
        else if (arg == "--band-min-hz") o.band_min = number(value());
        else if (arg == "--band-max-hz") o.band_max = number(value());
        else if (arg == "--min-bin-power") o.min_bin_power = number(value());
        else if (arg == "--fft-scale") o.fft_scale = number(value());
        else if (arg == "--angle-start") o.angle_start = number(value());
        else if (arg == "--angle-step") o.angle_step = number(value());
        else if (arg == "--angle-count") o.angle_count = integer(value(), 2, 2001);
        else if (arg == "--ambiguity-tolerance") o.ambiguity_tolerance = number(value());
        else if (arg == "--min-path-ratio") o.min_path_ratio = number(value());
        else if (arg == "--max-bearing-residual") o.max_bearing_residual = number(value());
        else if (arg == "--min-crossing-angle") o.min_crossing_angle_deg = number(value());
        else throw std::invalid_argument("Unknown option: " + arg);
    }
    const auto files=o.fft_sources.empty()?o.adc_sources.size():o.fft_sources.size();
    pha_ipc::validate_name(o.ipc_name);
    if(o.once && o.max_batches>1) throw std::invalid_argument("--once conflicts with --max-batches >1");
    if(o.once) o.max_batches=1;
    if(files) {
        if(files!=1 && files!=npu_rounds) throw std::invalid_argument("Supply exactly one or four input files");
        if(o.capture_rounds_explicit && o.capture_rounds!=files)
            throw std::invalid_argument("Input file count must equal capture-rounds; files are not repeated implicitly");
        o.capture_rounds=files;
    }
    if (!o.array_file.empty()) load_array(o);
    if (!o.compensation_file.empty()) load_compensation(o);
    const double end = o.angle_start + static_cast<double>(o.angle_count - 1) * o.angle_step;
    if (o.prefix.empty() || o.rf_hz <= 0 || o.sample_rate <= 0 || o.fft_scale <= 0 ||
        o.dds_hz < 0 || o.dds_hz >= o.sample_rate / 2 || o.timeout <= 0 ||
        o.tcp.host.empty() || o.tcp.trigger.empty() || o.tcp.timeout_seconds <= 0 ||
        o.angle_step <= 0 || !std::isfinite(end) || o.angle_start <= -90 || end >= 90 ||
        o.angle_start + o.angle_step <= o.angle_start || o.band_min > o.band_max ||
        o.band_min < -o.sample_rate/8 || o.band_max >= o.sample_rate/8 ||
        o.min_bin_power < 0 || o.ambiguity_tolerance < 0 || o.min_path_ratio < 0 || o.min_path_ratio > 1 ||
        o.max_bearing_residual < 0 || o.max_bearing_residual > 1 ||
        o.min_crossing_angle_deg <= 0 || o.min_crossing_angle_deg >= 90)
        throw std::invalid_argument("Invalid frequency, angle, selection, threshold, or timeout configuration");
    if ((!o.fft_raw.empty() && (!o.adc_raw.empty() || o.dry_run)) || o.bins.size() > 32)
        throw std::invalid_argument("fft-raw conflicts with adc-raw/dry-run, or too many bins");
    if (o.fft_preprocessing == "pl-hann64" &&
        (o.fft_raw.empty() || o.ml_startup_samples != 64 || !o.ml_fft_hann))
        throw std::invalid_argument("pl-hann64 needs offline FFT, startup64 and Hann; hardware reads the PL version");
    // [部署核验修正] 当前 v6 NPU 固定接收 25 MS/s PHA；原来会先 sent 再在 NPU 拒收。
    // 在采集前拒绝不兼容的在线配置。仅做 AOA/离线研究时仍可显式 --no-ipc。
    // 在线识别由模式调度固定为四帧，不能用 --capture-rounds 1 绕过模型采样率检查。
    if(o.ipc_enabled && !o.dry_run && o.sample_rate!=100e6 &&
       (o.capture_rounds==npu_rounds || (o.adc_sources.empty() && o.fft_sources.empty())))
        throw std::invalid_argument("Current v6 NPU IPC requires --sample-rate 100000000 (PHA 25000000); use --no-ipc for AOA-only analysis");
    std::set<std::size_t> grouped;
    for (const auto& group : o.groups) for (auto ch : group) grouped.insert(ch);
    if (grouped.size() != channels) throw std::invalid_argument("The two triads must partition all six channels");
    for (const auto& ant : o.antenna) {
        if (ant.correction == AoaComplex(0, 0) || !std::isfinite(std::norm(ant.correction)) ||
            !std::isfinite(2*pi*o.rf_hz/light_speed * ant.x) ||
            !std::isfinite(2*pi*o.rf_hz/light_speed * ant.y))
            throw std::invalid_argument("Invalid position or zero/nonfinite calibration gain");
    }
    const auto count_bins = o.bins.empty() ? o.auto_bins : o.bins.size();
    if (o.paths == 2 && o.angle_count * o.angle_count * count_bins > (256*1024*1024)/sizeof(AoaGridPoint))
        throw std::invalid_argument("ML3 workspace would exceed 256 MiB per triad; reduce grid/bins");
    validate_joint_options(o);
    if(o.scan_ml_weight<0 || o.scan_ml_weight>1) throw std::invalid_argument("Scan ML weight must be 0..1");
    if(o.scan_candidate_gap_db<0 || o.scan_candidate_gap_db>30 || o.scan_min_snr_db<0 || o.scan_min_snr_db>100)
        throw std::invalid_argument("Scan gap/SNR outside allowed range");
    return o;
}
std::uint32_t dds_word(const Options& o) {
    return static_cast<std::uint32_t>(std::llround(o.dds_hz / o.sample_rate * 4294967296.0));
}
double bin_hz(std::size_t bin, double sample_rate) { return pl_fft_bin_hz(bin, sample_rate / 4); }
} // namespace aoa_app
