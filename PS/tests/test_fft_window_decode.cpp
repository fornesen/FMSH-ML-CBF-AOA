#include "app.hpp"
#include "ml_preprocessing.hpp"
#include "beam_control.hpp"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <iostream>
#include <map>

namespace {
void pack39(std::vector<std::uint8_t>& bytes, std::size_t offset, std::int64_t value) {
    const auto word=static_cast<std::uint64_t>(value)&((1ULL<<39)-1);
    for (std::size_t i=0; i<5; ++i) bytes[offset+i]=static_cast<std::uint8_t>(word>>(8*i));
}
}

int main() {
    using namespace aoa_app;
    Options options;options.ml_startup_samples=64;options.ml_fft_hann=true;
    options.fft_scale=2.0/fft_length;
    std::vector<std::uint8_t> slots(fft_bytes,0);
    for (std::size_t ch=0; ch<channels; ++ch) {
        options.antenna[ch].correction={1.0+ch*.1,-.25};
        const auto offset=(ch*fft_length+4014)*slot_bytes;
        pack39(slots,offset,2016*static_cast<std::int64_t>(ch+1));
        pack39(slots,offset+5,-2016*static_cast<std::int64_t>(ch+2));
    }
    CaptureState capture;capture.mode="hardware";capture.pl_version=windowed_fft_pl_version;
    const auto result=decode_fft(options,slots,capture);
    assert(capture.ml_preprocessing_backend=="pl" && capture.fft_preprocessing=="pl-hann64");
    assert(capture.written_slots==fft_records);
    for (std::size_t ch=0; ch<channels; ++ch) {
        const AoaComplex expected{2.0*(ch+1),-2.0*(ch+2)};
        assert(std::abs(result.corrected[ch*fft_length+4014]-expected*options.antenna[ch].correction)<1e-10);
        // A second Hann window would spread energy to adjacent bins.
        assert(result.corrected[ch*fft_length+4013]==AoaComplex(0,0));
        assert(result.corrected[ch*fft_length+4015]==AoaComplex(0,0));
    }
    CaptureState offline;offline.mode="offline_fft";
    options.fft_preprocessing="pl-hann64";
    const auto replay=decode_fft(options,slots,offline);
    assert(replay.corrected==result.corrected && offline.complete);
    slots[24]=1;
    bool refused=false;
    try {decode_fft(options,slots,offline);} catch (const std::runtime_error&) {refused=true;}
    assert(refused && offline.fft_preprocessing=="pl-hann64");

    // Both supported register versions retain the old console's exact-match contract.
    for (const auto version:{legacy_pl_version,windowed_fft_pl_version}) {
        BeamWeights weights;
        std::map<std::uint64_t,std::uint32_t> registers{{0xc0,version}};
        write_beam_registers(weights,[&](auto a,auto v){registers[a]=v;},
                             [&](auto a){return registers[a];},[]{});
    }
    bool unknown_version=false;
    try {
        write_beam_registers(BeamWeights{},[](auto,auto){assert(false);},
                             [](auto){return 0xffffffffu;},[]{});
    } catch (const std::runtime_error&) {unknown_version=true;}
    assert(unknown_version);
    std::cout<<"FFT decode: hardware/replay, signed39, six channels, scale, no double-window, invalid slots, beam versions OK\n";
}
