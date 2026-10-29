#ifdef NDEBUG
#undef NDEBUG
#endif
#include "pha_npu.hpp"
#include <cassert>
#include <chrono>
#include <iostream>

using namespace pha_npu;
namespace {
void reject(const Options& options) {
    bool failed=false;
    try { resolve_model_identity(options); } catch(const std::exception&) { failed=true; }
    assert(failed);
}
}
int main(int argc,char** argv) {
    const auto directory=fs::current_path()/(
        "aoa-model-identity-"+std::to_string(std::chrono::high_resolution_clock::now().time_since_epoch().count()));
    fs::create_directories(directory);
    Options options;options.model_json=directory/"fixture_ZG.json";options.model_raw=directory/"fixture_ZG.raw";
    const std::string network="{\"fixture\":true}\n",weights="fixture parameter bytes";
    write_bytes_atomic(options.model_json,network.data(),network.size());
    write_bytes_atomic(options.model_raw,weights.data(),weights.size());
    const auto contract_path=directory/"fixture_contract.json";
    const Json contract={{"model_family","sixsum_fm_pm_v8"},{"classes",kClassNames},
        {"class_count",kClassCount},{"output_shape",{1,kClassCount}},
        {"model_json_sha256",sha256(read_bytes(options.model_json))},
        {"model_raw_sha256",sha256(read_bytes(options.model_raw))},
        {"amplitude_mode","batch"},{"amplitude_target_rms",.25},{"amplitude_max_gain",256}};
    write_json_atomic(contract_path,contract);
    const auto identity=resolve_model_identity(options);
    assert(identity.at("model_family")=="sixsum_fm_pm_v8");
    assert(identity.at("contract_matched")==true && identity.at("preprocessing_matches_contract")==true);
    auto alternate=contract;alternate["model_family"]="sixsum_custom_six_class";
    const auto explicit_contract=directory/"explicit.json";write_json_atomic(explicit_contract,alternate);
    options.model_contract=explicit_contract;
    assert(resolve_model_identity(options).at("model_family")=="sixsum_custom_six_class");
    options.amplitude_mode="fixed";
    assert(resolve_model_identity(options).at("preprocessing_matches_contract")==false);
    options.amplitude_mode="batch";options.model_contract.clear();
    const std::string changed="different parameters";
    write_bytes_atomic(options.model_raw,changed.data(),changed.size());reject(options);
    write_bytes_atomic(options.model_raw,weights.data(),weights.size());
    auto invalid=contract;invalid["classes"]={"PM","AM","CW","noise","OOK","FM"};
    write_json_atomic(contract_path,invalid);reject(options);
    invalid=contract;invalid["class_count"]=5;write_json_atomic(contract_path,invalid);reject(options);
    fs::remove(contract_path);reject(options);
    fs::remove(options.model_json);fs::remove(options.model_raw);fs::remove(explicit_contract);fs::remove(directory);
    if(argc==4) {
        Options delivered;delivered.model_json=argv[1];delivered.model_raw=argv[2];delivered.model_contract=argv[3];
        const auto actual=resolve_model_identity(delivered);
        assert(actual.at("model_family")=="sixsum_fm_pm_v8" && actual.at("contract_matched")==true);
        std::cout<<"Delivered v8 JSON/RAW SHA256 match the six-class contract; no inference executed\n";
    }
    std::cout<<"Model identity: actual file matching, explicit contract, class order, mismatch rejection and preprocessing labels OK\n";
}
