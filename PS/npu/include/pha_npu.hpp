#pragma once
#include <array>
#include <complex>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <string>
#include <vector>
#include <nlohmann/json.hpp>

namespace pha_npu {
using Json = nlohmann::json;
namespace fs = std::filesystem;
constexpr int kRounds = 4, kRawSamples = 4096, kDiscard = 64, kSamples = 4032;
constexpr int kWindow = 1024, kHop = 64, kFrames = 48, kColumns = 640;
constexpr double kFs = 25000000., kIqScale = 1048576.;
constexpr std::size_t kClassCount = 6;
inline const std::array<std::string,kClassCount> kClassNames{{"FM","AM","CW","noise","OOK","PM"}};

struct Options {
    fs::path manifest, raw_override, output_dir = "results/npu", model_json, model_raw, model_contract;
    std::string backend = "npu", device = "axi://zg330aiu?npu=0x40000000&dma=0x80000000";
    double input_gain = 1., wait_seconds = 0.;
    std::string amplitude_mode = "batch";
    double amplitude_target_rms = .25, amplitude_max_gain = 256., amplitude_min_coherence = .5;
    bool force = false;
    bool save_results = false, once = false;
    std::string ipc_name = "/icraft_pha4";
    std::uint64_t max_batches = 0;
    std::function<void(bool)> device_activity;
    std::function<bool()> cancelled; // [板端联调修正] 允许硬件互斥等待响应 SIGTERM。
};
struct Batch {
    Json manifest;
    std::string manifest_text, run_id, safe_id, raw_sha256;
    fs::path manifest_path, raw_path;
    std::vector<std::uint8_t> raw;
    std::array<std::vector<std::complex<double>>, kRounds> iq;
    Json amplitude_preprocessing;
};
struct Feature {
    std::string name;
    std::array<std::int64_t, 4> shape;
    std::vector<std::uint8_t> u8;
    std::vector<float> f32;
};
using Features = std::array<Feature, 3>;
struct Inference {
    std::vector<float> logits;
    Json details;
};
std::string sha256(const std::vector<std::uint8_t>& data);
std::vector<std::uint8_t> read_bytes(const fs::path& path, std::size_t expected = 0);
std::string read_text(const fs::path& path);
Json resolve_model_identity(const Options& options);
void write_bytes_atomic(const fs::path& path, const void* data, std::size_t size);
void write_json_atomic(const fs::path& path, const Json& value);
Batch load_batch(const Options& options);
void validate_amplitude_options(const Options& options);
Batch load_memory_batch(const Options&,const std::string&,std::vector<std::uint8_t>);
Features preprocess(const Batch& batch);
Json dump_features(const Features& features, const fs::path& prefix);
Inference infer_icraft(const Options& options, const Features& features, const fs::path& prefix);
Json postprocess(const std::vector<float>& logits);
}
