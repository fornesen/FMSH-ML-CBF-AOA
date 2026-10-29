#include "pha_npu.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>

using namespace pha_npu;
namespace {
constexpr double pi=3.14159265358979323846;
void check(bool ok,const char* what) {if(!ok)throw std::runtime_error(what);}
Json manifest() {
    Json captures=Json::array();
    for(int i=0;i<4;++i)captures.push_back({{"index",i},{"complete",true},{"pha_saved",true},
        {"pha_written_samples",4096},{"pha_format_errors",0},{"written_slots",24576},
        {"error",""},{"guard_checked",false}});
    return {{"format_version",1},{"ready",true},{"run_id","amplitude_fixture"},
        {"capture_round_count",4},{"samples_per_round",4096},{"total_complex_samples",16384},
        {"byte_count",131072},{"shape",{4,4096,2}},{"dtype","int32"},{"byte_order","little"},
        {"component_order",{"I","Q"}},{"sample_rate_hz",25000000},
        {"direction_round_index",3},{"captures",captures}};
}
void put(std::vector<std::uint8_t>& raw,int round,int sample,int component,std::int32_t value) {
    const auto u=static_cast<std::uint32_t>(value);
    const auto offset=(round*kRawSamples+sample)*8+component*4;
    for(int i=0;i<4;++i)raw[offset+i]=static_cast<std::uint8_t>(u>>(i*8));
}
std::vector<std::uint8_t> tone(const std::array<double,4>& amplitude) {
    std::vector<std::uint8_t> raw(131072);
    for(int r=0;r<4;++r)for(int i=0;i<4096;++i) {
        const auto value=std::polar(amplitude[r],-2*pi*500000*i/kFs+.3*r);
        put(raw,r,i,0,static_cast<std::int32_t>(std::nearbyint(value.real())));
        put(raw,r,i,1,static_cast<std::int32_t>(std::nearbyint(value.imag())));
    }
    return raw;
}
Batch load(const Options& o,const std::vector<std::uint8_t>& raw) {
    return load_memory_batch(o,manifest().dump(),raw);
}
void common_gain(const Batch& before,const Batch& after) {
    const double gain=after.amplitude_preprocessing.at("gain");
    for(int r=0;r<4;++r)for(int i=0;i<4032;++i)
        check(std::abs(after.iq[r][i]-before.iq[r][i]*gain)<1e-12,"One common complex gain preserves levels/phases");
    check(before.raw==after.raw&&before.raw_sha256==after.raw_sha256,"Original PHA must remain byte-identical");
}
}
int main() {
    try {
        Options automatic,fixed;fixed.amplitude_mode="fixed";
        const auto raw=tone({2400,2400,2400,2400});
        const auto legacy=load(fixed,raw),adjusted=load(automatic,raw);
        common_gain(legacy,adjusted);
        check(adjusted.amplitude_preprocessing["applied"]==true,"Weak carrier is boosted");
        check(std::abs(adjusted.amplitude_preprocessing["reference_rms_after"].get<double>()-.25)<1e-12,"RMS target reached");
        const auto low=preprocess(legacy),high=preprocess(adjusted);
        check(std::all_of(low[2].u8.begin(),low[2].u8.end(),[](auto v){return v==0;}),"Regression fixture has zero legacy envelope");
        check(std::all_of(high[2].u8.begin(),high[2].u8.end(),[](auto v){return v>=31&&v<=33;}),"Weak envelope survives quantization");
        const auto levels=tone({2000,0,800,0});
        const auto level_before=load(fixed,levels),level_after=load(automatic,levels);
        common_gain(level_before,level_after);
        check(level_after.iq[1]==level_before.iq[1]&&level_after.iq[3]==level_before.iq[3],"OOK off rounds stay zero");
        const auto level_features=preprocess(level_after);
        check(level_features[2].u8[0]>level_features[2].u8[96]&&level_features[2].u8[96]>0,
              "Different nonzero round levels remain different");
        auto burst=raw;
        for(int r=0;r<4;++r)for(int i=1024;i<2048;++i)for(int c=0;c<2;++c)put(burst,r,i,c,0);
        common_gain(load(fixed,burst),load(automatic,burst));
        const auto burst_batch=load(automatic,burst);
        check(std::all_of(burst_batch.iq[0].begin()+1024-64,burst_batch.iq[0].begin()+2048-64,
                         [](auto v){return v==std::complex<double>{};}),"OOK off interval is preserved");
        auto startup=raw;
        for(int r=0;r<4;++r)for(int i=0;i<64;++i)for(int c=0;c<2;++c)
            put(startup,r,i,c,std::numeric_limits<std::int32_t>::max());
        const auto no_startup=load(automatic,startup);
        check(no_startup.iq==adjusted.iq,"Discarded startup samples cannot drive common gain");
        std::vector<std::uint8_t> noise(131072);std::mt19937 rng(20261023);
        std::normal_distribution<double> normal(0,1200);
        for(int r=0;r<4;++r)for(int i=0;i<4096;++i)for(int c=0;c<2;++c)
            put(noise,r,i,c,static_cast<std::int32_t>(std::nearbyint(normal(rng))));
        const auto noise_batch=load(automatic,noise);
        check(noise_batch.amplitude_preprocessing["reason"]=="noise_like"&&
              noise_batch.iq==load(fixed,noise).iq,"Do not amplify incoherent weak noise");
        const auto zero=load(automatic,std::vector<std::uint8_t>(131072));
        check(zero.amplitude_preprocessing["reason"]=="zero_reference"&&
              zero.amplitude_preprocessing["gain"]==1.,"Zero input is stable");
        const auto zero_features=preprocess(zero);
        for(const auto& f:zero_features)check(std::all_of(f.u8.begin(),f.u8.end(),[](auto v){return v==0;}),"Zero input features stay zero");
        const auto tiny=load(automatic,tone({1,1,1,1}));
        check(tiny.amplitude_preprocessing["gain"]==256.&&
              tiny.amplitude_preprocessing["reason"]=="max_gain_limited","Maximum gain is enforced");
        auto impulse=raw;put(impulse,0,500,0,524288);
        auto allow_peak=automatic;allow_peak.amplitude_min_coherence=0;
        const auto limited=load(allow_peak,impulse);
        check(limited.amplitude_preprocessing["reason"]=="peak_limited"&&
              limited.amplitude_preprocessing["peak_after"].get<double>()<=1.5+1e-12,"Peak headroom is enforced");
        const auto strong=load(automatic,tone({600000,600000,600000,600000}));
        check(strong.amplitude_preprocessing["gain"]==1.&&
              strong.amplitude_preprocessing["reason"]=="already_in_range","Strong signals keep their original scale");
        auto manual=automatic;manual.input_gain=2;
        auto manual_fixed=fixed;manual_fixed.input_gain=2;
        const auto combined=load(manual,raw);
        common_gain(load(manual_fixed,raw),combined);
        check(combined.amplitude_preprocessing["effective_input_gain"].get<double>()==
              2*combined.amplitude_preprocessing["gain"].get<double>(),"Effective manual/automatic gain is recorded");
        auto boundaries=raw;put(boundaries,0,100,0,std::numeric_limits<std::int32_t>::min());
        put(boundaries,1,100,1,std::numeric_limits<std::int32_t>::max());
        check(load(automatic,boundaries).iq==load(fixed,boundaries).iq,"Signed32 boundary is not rescaled/overflowed");
        auto invalid=[&](Options options) {
            bool rejected=false;try{load(options,raw);}catch(const std::exception&){rejected=true;}
            check(rejected,"Invalid amplitude options must be rejected");
        };
        auto bad=automatic;bad.amplitude_mode="per_round";invalid(bad);
        bad=automatic;bad.amplitude_target_rms=0;invalid(bad);
        bad=automatic;bad.amplitude_target_rms=1.1;invalid(bad);
        bad=automatic;bad.amplitude_max_gain=.5;invalid(bad);
        bad=automatic;bad.amplitude_min_coherence=1.1;invalid(bad);
        bad=automatic;bad.amplitude_target_rms=std::numeric_limits<double>::quiet_NaN();invalid(bad);
        bad=automatic;bad.input_gain=std::numeric_limits<double>::infinity();invalid(bad);
        std::cout<<"PASS weak carrier, common gain, AM/OOK levels and phase, raw preservation, startup, noise, zero, limits, legacy, signed32 and options\n";
        return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
