#ifdef NDEBUG
#undef NDEBUG
#endif
#include "app.hpp"
#include "capture_pha.hpp"
#include "console_aoa.hpp"
#include "pl_fft_input.hpp"
#include <cassert>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <random>

// Test transport cannot access the board. All processing below is production code.
namespace tcp_receiver {
void receive_exact(const Options&,std::size_t,std::vector<std::uint8_t>&,
                   const std::function<bool()>&) {throw std::runtime_error("Network forbidden in offline test");}
}
namespace aoa_app {
// app_config.cpp also contains POSIX IPC option validation; use its scalar helpers here.
double bin_hz(std::size_t bin,double rate) {return pl_fft_bin_hz(bin,rate/4);}
std::uint32_t dds_word(const Options& o) {
    return static_cast<std::uint32_t>(std::llround(o.dds_hz/o.sample_rate*4294967296.0));
}
}
namespace {
void pack39(std::uint8_t* out,std::int64_t value) {
    auto word=static_cast<std::uint64_t>(value)&((1ULL<<39)-1);
    for(int b=0;b<5;++b) out[b]=static_cast<std::uint8_t>(word>>(8*b));
}
template<class F> void rejects(F action) {
    bool rejected=false;try {action();} catch(const std::runtime_error&) {rejected=true;}assert(rejected);
}
}
int main(int argc,char** argv) {
    using namespace aoa_app;
    const auto tag=std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
    const auto dir=std::filesystem::temp_directory_path()/("aoa_four_frame_test_"+tag);
    std::filesystem::create_directory(dir);
    Options o;o.processing_mode="angle6";o.capture_rounds=4;o.rf_hz=445e6;
    o.bins={4014};o.ml_startup_samples=64;o.ml_fft_hann=true;o.fft_preprocessing="pl-hann64";
    o.ipc_enabled=false;o.prefix=(dir/"batch").string();
    const std::array<double,6> x{{-1.2823613348729794,-.9361806674364898,-.59,.59,.9361806674364898,1.2823613348729794}};
    for(std::size_t ch=0;ch<6;++ch) o.antenna[ch].x=x[ch];
    std::vector<std::vector<std::uint8_t>> files;
    for(std::size_t t=0;t<4;++t) {
        std::vector<std::uint8_t> slots(fft_bytes,0);
        for(std::size_t ch=0;ch<6;++ch) {
            AoaComplex y{};
            for(const auto path:std::array<std::pair<double,double>,3>{{{20,100000},{-35,65000},{65,35000}}})
                y+=std::polar(path.second,2*pi*o.rf_hz/light_speed*x[ch]*std::sin(path.first*pi/180)+t*pi/2);
            auto* p=slots.data()+(ch*fft_length+4014)*slot_bytes;
            pack39(p,std::llround(y.real()));pack39(p+5,std::llround(y.imag()));
        }
        // Independent PHA markers include sign-bit patterns and every sample index.
        for(std::size_t s=0;s<pha_samples;++s) for(std::size_t b=0;b<8;++b)
            slots[s*slot_bytes+pha_offset+b]=static_cast<std::uint8_t>((s*13+t*71+b*37)&255);
        const auto file=(dir/("round"+std::to_string(t)+".raw")).string();
        save_bytes(file,slots,slots.size());o.fft_sources.push_back(file);files.push_back(std::move(slots));
    }
    o.fft_raw=o.fft_sources[0];
    CaptureBatch batch;CaptureState capture;std::vector<std::uint8_t> slots;
    initialize_batch(o,batch);collect_batch(o,batch,capture,slots,{});
    assert(batch.completed==4 && batch.fft_snapshots.size()==4 && batch.pha.empty());
    for(std::size_t t=0;t<4;++t) {
        auto state=batch.rounds[t];const auto independently_decoded=decode_fft(o,files[t],state);
        assert(batch.fft_snapshots[t]==independently_decoded.corrected);
        assert(batch.fft_snapshots[t][4013]==AoaComplex(0,0));
        assert(batch.fft_snapshots[t][4015]==AoaComplex(0,0));
    }
    rejects([&]{npu_metadata(o,batch,"file");});
    FftData fft;fft.snapshots=batch.fft_snapshots;fft.corrected=fft.snapshots.back();
    fft.raw=fft.corrected;fft.valid.assign(fft_records,1);
    auto a=analyze(o,fft,{});
    assert(a.complete && a.direction_frames==4 && a.bins==o.bins && a.joint_raw_validated);
    if(!joint_eligible(o,a)) std::cerr<<joint_packet(o,a).dump(2)<<std::endl;
    assert(joint_eligible(o,a));
    assert(std::abs(a.joint.angle[a.selection.path_index]-20)<=1);
    const auto original=fft.snapshots;analyze(o,fft,{});assert(fft.snapshots==original);
    if(argc>1) {
        std::ofstream out(std::string(argv[1])+"_gated.json");out<<console_packet(o,capture,batch,a).dump(2);
    }
    o.joint_quality_selection=true;auto quality=analyze(o,fft,{});
    assert(quality.v12.enabled && quality.selection.accepted && joint_eligible(o,quality));
    if(argc>1) {
        std::ofstream out(std::string(argv[1])+"_quality.json");out<<console_packet(o,capture,batch,quality).dump(2);
    }
    std::mt19937 rng(241024);std::normal_distribution<double> normal;
    for(auto& frame:fft.snapshots) for(std::size_t ch=0;ch<6;++ch) frame[ch*fft_length+4014]={normal(rng),normal(rng)};
    fft.corrected=fft.snapshots.back();auto noise=analyze(o,fft,{});
    assert(noise.v12.enabled && noise.v12.valid && joint_eligible(o,noise));
    // V1.2 reports raw quality diagnostically; no residual cutoff changes the score.
    if(argc>1) {
        std::ofstream out(std::string(argv[1])+"_fallback.json");out<<console_packet(o,capture,batch,noise).dump(2);
    }
    // PHA extraction never changes the ML data or removes its first 64 samples.
    CaptureBatch pha;pha.run_id=batch.run_id;pha.completed=4;pha.rounds=batch.rounds;
    std::vector<std::uint8_t> expected;
    for(std::size_t t=0;t<4;++t) {
        auto& state=pha.rounds[t];state.mode="hardware";state.pha_written_samples=pha_samples;
        const auto frame=pha_frame(files[t],state);state.pha_collected=true;
        pha.pha.insert(pha.pha.end(),frame.begin(),frame.end());
        for(std::size_t s=0;s<pha_samples;++s) for(std::size_t b=0;b<8;++b)
            expected.push_back(static_cast<std::uint8_t>((s*13+t*71+b*37)&255));
    }
    assert(pha.pha==expected && pha.pha.size()==4*4096*8);
    const auto metadata=aoa_console::Json::parse(npu_metadata(o,pha,"file"));
    assert(metadata["shape"]==aoa_console::Json({4,4096,2}));
    assert(metadata["continuous_between_rounds"]==false);
    pha.rounds[1].mode="offline_fft";rejects([&]{npu_metadata(o,pha,"file");});
    // Missing round and a cancellation may not complete a batch or reuse previous history.
    o.fft_sources[2]=(dir/"missing.raw").string();initialize_batch(o,batch);
    rejects([&]{collect_batch(o,batch,capture,slots,{});});
    assert(batch.completed==2 && batch.fft_snapshots.size()==2 && !batch.npu_ready);
    initialize_batch(o,batch);assert(batch.fft_snapshots.empty() && batch.pha.empty());
    rejects([&]{collect_batch(o,batch,capture,slots,[]{return true;});});
    assert(batch.completed==0);
    // Position mode must also reject a poor latest snapshot, even with a good fused fit.
    Options position=o;position.paths=1;
    std::vector<GroupEstimate> groups(2);
    for(int id=0;id<2;++id) {
        auto& group=groups[id];group.id=id;group.has_best=true;
        group.angle={id==0?20.0:-20.0};group.active={true};group.energy=1;group.residual=.01;
        group.raw_validated=true;group.raw_residual_ratio=.01;group.latest_residual_ratio=.01;
    }
    assert(localize(position,groups).valid);
    groups[1].latest_residual_ratio=.5;
    assert(localize(position,groups).status=="poor_bearing_fit");
    groups[1].latest_residual_ratio=std::numeric_limits<double>::quiet_NaN();
    assert(localize(position,groups).status=="poor_bearing_fit");
    std::filesystem::remove_all(dir);
    std::cout<<"Four-frame pipeline: decode once, raw gate, separate byte-exact PHA, offline guard, failure/reset OK\n";
}
