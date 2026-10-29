#include "pha_npu.hpp"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>

namespace pha_npu {
namespace {
void require(bool condition,const std::string& message) {
    if(!condition) throw std::runtime_error(message);
}
std::uint32_t rotr(std::uint32_t v,unsigned n) { return (v>>n)|(v<<(32-n)); }

void adjust_batch_amplitude(const Options& o,Batch& b) {
    // One gain for all retained samples in all four rounds. Never normalize
    // individual rounds/windows: their relative levels carry AM/OOK information.
    constexpr double peak_limit = 1.5;
    std::vector<double> window_rms;
    std::array<double,kRounds> round_rms{};
    std::complex<double> lag1{};
    double left_energy=0,right_energy=0,peak=0;
    for(int round=0;round<kRounds;++round) {
        const auto& x=b.iq[round];
        double energy=0;
        for(std::size_t i=0;i<x.size();++i) {
            const double p=std::norm(x[i]);energy+=p;peak=std::max(peak,std::abs(x[i]));
            if(i) {
                lag1+=x[i]*std::conj(x[i-1]);
                left_energy+=std::norm(x[i-1]);right_energy+=p;
            }
        }
        round_rms[round]=std::sqrt(energy/x.size());
        // 63 disjoint windows per round, covering all 4032 retained samples.
        // The percentile avoids using OOK off-time as the gain reference.
        for(std::size_t begin=0;begin+64<=x.size();begin+=64) {
            double sum=0;for(std::size_t i=begin;i<begin+64;++i)sum+=std::norm(x[i]);
            window_rms.push_back(std::sqrt(sum/64));
        }
    }
    std::sort(window_rms.begin(),window_rms.end());
    const auto rank=static_cast<std::size_t>(std::ceil(.95*window_rms.size()))-1;
    const double reference=window_rms.at(rank);
    const double denominator=std::sqrt(left_energy*right_energy);
    const double coherence=denominator>0?std::min(1.,std::abs(lag1)/denominator):0.;
    double gain=1.;std::string reason;
    if(o.amplitude_mode=="fixed")reason="fixed_mode";
    else if(reference==0)reason="zero_reference";
    else if(reference>=o.amplitude_target_rms)reason="already_in_range";
    else if(coherence<o.amplitude_min_coherence)reason="noise_like";
    else {
        const double desired=o.amplitude_target_rms/reference;
        const double peak_gain=peak>0?std::max(1.,peak_limit/peak):1.;
        gain=std::min({desired,o.amplitude_max_gain,peak_gain});
        reason=gain<desired?
            (peak_gain<=o.amplitude_max_gain?"peak_limited":"max_gain_limited"):"boosted";
    }
    const auto before=round_rms;
    if(gain>1)for(int round=0;round<kRounds;++round) {
        for(auto& v:b.iq[round])v*=gain;
        round_rms[round]*=gain;
    }
    b.amplitude_preprocessing={{"version",1},{"mode",o.amplitude_mode},
        {"scope","four_round_batch"},{"applied",gain>1},{"reason",reason},
        {"fixed_input_gain",o.input_gain},{"gain",gain},{"effective_input_gain",o.input_gain*gain},
        {"target_rms",o.amplitude_target_rms},{"max_gain",o.amplitude_max_gain},
        {"min_coherence",o.amplitude_min_coherence},{"lag1_coherence",coherence},
        {"peak_limit",peak_limit},{"peak_before",peak},{"peak_after",peak*gain},
        {"reference_percentile",95},{"reference_window_samples",64},
        {"reference_rms_before",reference},{"reference_rms_after",reference*gain},
        {"round_rms_before",before},{"round_rms_after",round_rms},
        {"units","IQ/2^20 after fixed_input_gain"},{"raw_source_preserved",true}};
}
}
void validate_amplitude_options(const Options& o) {
    require(std::isfinite(o.input_gain)&&o.input_gain>0,"Input gain must be positive and finite");
    require(o.amplitude_mode=="batch"||o.amplitude_mode=="fixed","Amplitude mode must be batch or fixed");
    require(std::isfinite(o.amplitude_target_rms)&&o.amplitude_target_rms>0&&o.amplitude_target_rms<=1,
            "Amplitude target RMS must be in (0,1]");
    require(std::isfinite(o.amplitude_max_gain)&&o.amplitude_max_gain>=1,"Amplitude maximum gain must be finite and >=1");
    require(std::isfinite(o.amplitude_min_coherence)&&o.amplitude_min_coherence>=0&&o.amplitude_min_coherence<=1,
            "Amplitude minimum coherence must be in [0,1]");
}
std::string sha256(const std::vector<std::uint8_t>& data) {
    static constexpr std::uint32_t k[64]={
      0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
      0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
      0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
      0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
      0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
      0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
      0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
      0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
    std::array<std::uint32_t,8> h{{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                                 0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19}};
    auto bytes=data; const auto bits=static_cast<std::uint64_t>(bytes.size())*8;
    bytes.push_back(0x80); while(bytes.size()%64!=56) bytes.push_back(0);
    for(int i=7;i>=0;--i) bytes.push_back(static_cast<std::uint8_t>(bits>>(i*8)));
    for(std::size_t offset=0;offset<bytes.size();offset+=64) {
        std::uint32_t w[64]{};
        for(int i=0;i<16;++i) for(int j=0;j<4;++j) w[i]=(w[i]<<8)|bytes[offset+i*4+j];
        for(int i=16;i<64;++i) {
            const auto a=rotr(w[i-15],7)^rotr(w[i-15],18)^(w[i-15]>>3);
            const auto b=rotr(w[i-2],17)^rotr(w[i-2],19)^(w[i-2]>>10);
            w[i]=w[i-16]+a+w[i-7]+b;
        }
        auto a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],v=h[7];
        for(int i=0;i<64;++i) {
            const auto s1=rotr(e,6)^rotr(e,11)^rotr(e,25);
            const auto ch=(e&f)^(~e&g), t1=v+s1+ch+k[i]+w[i];
            const auto s0=rotr(a,2)^rotr(a,13)^rotr(a,22);
            const auto maj=(a&b)^(a&c)^(b&c),t2=s0+maj;
            v=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;
        }
        h[0]+=a;h[1]+=b;h[2]+=c;h[3]+=d;h[4]+=e;h[5]+=f;h[6]+=g;h[7]+=v;
    }
    std::ostringstream out;out<<std::hex<<std::setfill('0');
    for(auto v:h) out<<std::setw(8)<<v;
    return out.str();
}
std::vector<std::uint8_t> read_bytes(const fs::path& path,std::size_t expected) {
    std::ifstream in(path,std::ios::binary|std::ios::ate);
    require(in.good(),"Cannot open file: "+path.string());
    const auto length=in.tellg();
    require(length>=0,"Cannot determine file length: "+path.string());
    if(expected) require(static_cast<std::uint64_t>(length)==expected,"Unexpected byte count: "+path.string());
    require(static_cast<std::uint64_t>(length)<1024ull*1024*1024,"File exceeds 1GiB application limit");
    std::vector<std::uint8_t> bytes(static_cast<std::size_t>(length));in.seekg(0);
    if(!bytes.empty()) in.read(reinterpret_cast<char*>(bytes.data()),static_cast<std::streamsize>(bytes.size()));
    require(in.good(),"Short read: "+path.string());return bytes;
}
std::string read_text(const fs::path& path) {
    const auto b=read_bytes(path); require(b.size()<4*1024*1024,"JSON manifest too large");
    return std::string(b.begin(),b.end());
}
void write_bytes_atomic(const fs::path& path,const void* data,std::size_t size) {
    const fs::path temp=path.string()+".tmp";
    std::ofstream out(temp,std::ios::binary|std::ios::trunc);
    require(out.good(),"Cannot create: "+temp.string());
    if(size) out.write(static_cast<const char*>(data),static_cast<std::streamsize>(size));
    out.flush();require(out.good(),"Cannot write: "+temp.string());out.close();
    fs::rename(temp,path);
}
void write_json_atomic(const fs::path& path,const Json& value) {
    const auto text=value.dump(2)+"\n";write_bytes_atomic(path,text.data(),text.size());
}
Json resolve_model_identity(const Options& o) {
    auto contract_path=o.model_contract;
    if(contract_path.empty()) {
        auto stem=o.model_json.stem().string();
        if(stem.size()>3 && stem.substr(stem.size()-3)=="_ZG") stem.resize(stem.size()-3);
        contract_path=o.model_json.parent_path()/(stem+"_contract.json");
    }
    const auto contract=Json::parse(read_text(contract_path));
    require(contract.at("classes")==Json(kClassNames) && contract.at("class_count")==kClassCount &&
            contract.at("output_shape")==Json::array({1,kClassCount}),"Model contract must match the six-class consumer");
    const auto family=contract.at("model_family").get<std::string>();
    require(!family.empty(),"Model contract needs a nonempty family");
    const auto json_hash=sha256(read_bytes(o.model_json)), raw_hash=sha256(read_bytes(o.model_raw));
    require(contract.at("model_json_sha256")==json_hash && contract.at("model_raw_sha256")==raw_hash,
            "Model JSON/RAW do not match the selected contract");
    const bool preprocessing_matches=contract.value("amplitude_mode",o.amplitude_mode)==o.amplitude_mode &&
        contract.value("amplitude_target_rms",o.amplitude_target_rms)==o.amplitude_target_rms &&
        contract.value("amplitude_max_gain",o.amplitude_max_gain)==o.amplitude_max_gain && o.input_gain==1.;
    return {{"model_family",family},{"contract_file",fs::absolute(contract_path).string()},
        {"contract_matched",true},{"model_json_sha256",json_hash},{"model_raw_sha256",raw_hash},
        {"preprocessing_matches_contract",preprocessing_matches}};
}
Batch load_memory_batch(const Options& o,const std::string& metadata,std::vector<std::uint8_t> raw) {
    validate_amplitude_options(o);
    Batch b;b.manifest_text=metadata;b.manifest=Json::parse(metadata);const auto& j=b.manifest;
    require(j.at("format_version")==1 && j.at("ready")==true,"Manifest is not a version1 ready batch");
    require(j.at("capture_round_count")==4 && j.at("samples_per_round")==4096 &&
            j.at("total_complex_samples")==16384 && j.at("byte_count")==131072,"Manifest sample/byte counts mismatch");
    require(j.at("shape")==Json::array({4,4096,2}) && j.at("dtype")=="int32" &&
            j.at("byte_order")=="little" && j.at("component_order")==Json::array({"I","Q"}),"Unsupported PHA shape/dtype/order");
    require(j.at("sample_rate_hz")==25000000,"Model requires 25MS/s complex IQ");
    require(j.at("direction_round_index")==3,"Expected final FFT direction index3");
    b.run_id=j.at("run_id").get<std::string>();require(!b.run_id.empty() && b.run_id.size()<=256,"Invalid run_id");
    const auto& captures=j.at("captures");require(captures.is_array() && captures.size()==4,"Four capture statuses are required");
    for(int i=0;i<4;++i) {
        const auto& c=captures.at(i);
        require(c.at("index")==i && c.at("complete")==true && (j.value("transport",std::string("file"))=="shm"?c.value("pha_collected",false):c.value("pha_saved",false)) &&
                c.at("pha_written_samples")==4096 && c.at("pha_format_errors")==0 &&
                c.at("written_slots")==24576 && c.at("error")=="","Incomplete/invalid capture round "+std::to_string(i));
        if(c.at("guard_checked")==true) require(c.at("guard_ok")==true,"Guard verification failed");
    }
    require(raw.size()==131072,"Unexpected in-memory PHA byte count");
    b.raw=std::move(raw);b.raw_sha256=sha256(b.raw);
    b.manifest_path="shm:"+o.ipc_name;b.raw_path=b.manifest_path;
    b.safe_id=b.run_id;
    const bool simple=b.safe_id.size()<=96 && b.safe_id!="." && b.safe_id!=".." &&
        std::all_of(b.safe_id.begin(),b.safe_id.end(),[](unsigned char c){return
            (c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_'||c=='-'||c=='.';});
    if(!simple) b.safe_id="run_"+sha256(std::vector<std::uint8_t>(b.run_id.begin(),b.run_id.end())).substr(0,24);
    for(int round=0;round<4;++round) {
        auto& x=b.iq[round];x.reserve(kSamples);
        for(int sample=kDiscard;sample<kRawSamples;++sample) {
            double parts[2]{};
            for(int component=0;component<2;++component) {
                const auto index=(round*kRawSamples+sample)*8+component*4;
                std::uint32_t u=0;for(int bit=0;bit<4;++bit) u|=static_cast<std::uint32_t>(b.raw[index+bit])<<(8*bit);
                const std::int64_t signed_value=u>=0x80000000u?static_cast<std::int64_t>(u)-0x100000000ll:u;
                const double scaled=std::nearbyint(signed_value*o.input_gain);
                require(scaled>=-2147483648. && scaled<=2147483647.,"Fixed input gain overflows signed32 IQ");
                parts[component]=scaled/kIqScale;
            }
            x.emplace_back(parts[0],parts[1]);
        }
    }
    adjust_batch_amplitude(o,b);
    return b;
}
Batch load_batch(const Options& o) {
    const auto path=fs::absolute(o.manifest);const auto metadata=read_text(path);
    const auto manifest=Json::parse(metadata);
    const fs::path declared=manifest.at("raw_file").get<std::string>();
    require(declared.is_absolute(),"Manifest raw_file must be an absolute path");
    const auto raw_path=o.raw_override.empty()?declared:fs::absolute(o.raw_override);
    auto raw=read_bytes(raw_path,131072);
    require(read_text(path)==metadata,"Ready manifest changed while reading PHA; retry new batch");
    auto b=load_memory_batch(o,metadata,std::move(raw));b.manifest_path=path;b.raw_path=raw_path;
    return b;
}
}
