#include "ml_preprocessing.hpp"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <iostream>

int main() {
    using namespace aoa_app;
    // A direct DFT oracle catches transform sign, normalization and bin order.
    std::vector<AoaComplex> input(16),oracle(16);
    for(std::size_t n=0;n<input.size();++n) input[n]={std::sin(n*.7),std::cos(n*.31)};
    for(std::size_t k=0;k<input.size();++k)
        for(std::size_t n=0;n<input.size();++n)
            oracle[k]+=input[n]*std::polar(1.0,-2*pi*k*n/input.size());
    auto transformed=input;ml_transform(transformed,false);
    for(std::size_t k=0;k<input.size();++k) assert(std::abs(transformed[k]-oracle[k])<1e-12);
    ml_transform(transformed,true);
    for(std::size_t n=0;n<input.size();++n) assert(std::abs(transformed[n]-input[n])<1e-12);

    // Genuine zero-Hz and negative-frequency carriers must survive startup removal.
    // Large arbitrary startup samples are excluded identically on all six channels.
    Options o;o.ml_startup_samples=64;o.ml_fft_hann=true;
    FftData fft;fft.raw.resize(fft_records);fft.corrected.resize(fft_records);fft.valid.assign(fft_records,1);
    for(std::size_t ch=0;ch<channels;++ch) {
        std::vector<AoaComplex> time(fft_length);
        const auto phase=std::polar(1.0,ch*.41);
        for(std::size_t n=0;n<fft_length;++n) {
            time[n]=(AoaComplex{800,0}+2200.0*std::polar(1.0,2*pi*4014*n/fft_length))*phase;
            if(n<64) time[n]+=AoaComplex{100000.0+n,-200000.0-n};
        }
        ml_transform(time,false);
        std::copy(time.begin(),time.end(),fft.raw.begin()+ch*fft_length);
    }
    const auto raw=fft.raw;preprocess_ml_fft(o,fft);assert(raw==fft.raw);
    const auto software_corrected=fft.corrected;
    for(std::size_t ch=0;ch<channels;++ch) {
        const auto phase=std::polar(1.0,ch*.41);
        assert(std::abs(fft.corrected[ch*fft_length]-800.0*phase)<.01);
        assert(std::abs(std::arg(fft.corrected[ch*fft_length+4014]/phase/
                                fft.corrected[4014]))<1e-9);
    }
    // Independent time-domain window oracle represents the new PL payload.
    // Non-unity complex calibration and scale must be applied exactly once.
    for (std::size_t ch=0; ch<channels; ++ch) {
        std::vector<AoaComplex> time(fft_length);
        const auto phase=std::polar(1.0,ch*.41);
        for (std::size_t n=64; n<fft_length; ++n) {
            const auto window=(1-std::cos(2*pi*(n-64)/4032.0))/2;
            time[n]=(AoaComplex{800,0}+2200.0*std::polar(1.0,2*pi*4014*n/fft_length))*phase*window;
        }
        ml_transform(time,false);
        std::copy(time.begin(),time.end(),fft.raw.begin()+ch*fft_length);
        o.antenna[ch].correction=std::polar(1.3+ch*.1,-ch*.12);
    }
    const auto windowed_raw=fft.raw;
    o.fft_scale*=.75;
    preprocess_ml_fft(o,fft,true);
    assert(fft.raw==windowed_raw);
    for (std::size_t r=0; r<fft_records; ++r)
        assert(std::abs(fft.corrected[r]-software_corrected[r]*.75*o.antenna[r/fft_length].correction)<1e-7);
    CaptureState hardware;hardware.mode="hardware";
    hardware.pl_version=legacy_pl_version;assert(!ml_window_in_pl(o,hardware));
    hardware.pl_version=windowed_fft_pl_version;assert(ml_window_in_pl(o,hardware));
    hardware.pl_version=0xffffffff;
    bool unknown=false;
    try {ml_window_in_pl(o,hardware);} catch (const std::runtime_error&) {unknown=true;}
    assert(unknown);
    CaptureState offline;offline.mode="offline_fft";
    assert(!ml_window_in_pl(o,offline));
    o.fft_preprocessing="pl-hann64";assert(ml_window_in_pl(o,offline));
    Options incompatible=o;incompatible.ml_startup_samples=0;
    bool rejected=false;
    try {preprocess_ml_fft(incompatible,fft,true);} catch (const std::invalid_argument&) {rejected=true;}
    assert(rejected);
    fft.valid[100]=0;
    bool refused=false;
    try {preprocess_ml_fft(o,fft);} catch(const std::runtime_error&) {refused=true;}
    assert(refused);
    refused=false;
    try {preprocess_ml_fft(o,fft,true);} catch(const std::runtime_error&) {refused=true;}
    assert(refused);
    std::cout<<"ML preprocessing: DFT, PL/PS equivalence, calibration, versions, raw preservation, true DC, phase, invalid frames OK\n";
}
