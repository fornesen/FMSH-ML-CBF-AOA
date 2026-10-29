#pragma once
#include "app.hpp"
#include "pl_contract.hpp"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace aoa_app {
// Natural-order, unscaled forward FFT and 1/N inverse. Only the ML copy is edited.
inline void ml_transform(std::vector<AoaComplex>& a,bool inverse) {
    const auto n=a.size();
    if(n<2 || (n&(n-1))) throw std::invalid_argument("ML transform needs power-of-two length");
    for(std::size_t i=1,j=0;i<n;++i) {
        auto bit=n>>1;
        for(;j&bit;bit>>=1) j^=bit;
        j^=bit;
        if(i<j) std::swap(a[i],a[j]);
    }
    for(std::size_t len=2;len<=n;len<<=1) {
        const auto root=std::polar(1.0,(inverse?2:-2)*pi/len);
        for(std::size_t i=0;i<n;i+=len) {
            AoaComplex w{1,0};
            for(std::size_t j=0;j<len/2;++j) {
                const auto u=a[i+j],v=a[i+j+len/2]*w;
                a[i+j]=u+v; a[i+j+len/2]=u-v; w*=root;
            }
        }
    }
    if(inverse) for(auto& v:a) v/=static_cast<double>(n);
}

inline void validate_pl_fft_contract(const Options& o, std::uint32_t version) {
    if (!supported_pl_version(version))
        throw std::runtime_error("Unknown PL FFT preprocessing contract");
    if (version == windowed_fft_pl_version && (o.ml_startup_samples != pl_fft_startup_samples || !o.ml_fft_hann))
        throw std::invalid_argument("This PL requires --ml-startup-samples 64 --ml-fft-window hann");
}

inline bool ml_window_in_pl(const Options& o, const CaptureState& capture) {
    if (capture.mode == "offline_fft") return o.fft_preprocessing == "pl-hann64";
    validate_pl_fft_contract(o, capture.pl_version);
    return capture.pl_version == windowed_fft_pl_version;
}

inline void preprocess_ml_fft(const Options& o,FftData& fft,bool windowed_in_pl=false) {
    if(o.ml_startup_samples>=fft_length-1) throw std::invalid_argument("ML retained segment is too short");
    if(!std::all_of(fft.valid.begin(),fft.valid.end(),[](auto v){return v!=0;}))
        throw std::runtime_error("ML startup processing requires all six complete FFT frames");
    if (windowed_in_pl) {
        validate_pl_fft_contract(o, windowed_fft_pl_version);
        // The Q17 ROM sums exactly to 2016 * 2^17. Compensate its coherent gain
        // here, without a second time-domain transform, crop or window.
        constexpr double window_gain = static_cast<double>(fft_length) / pl_fft_window_sum;
        for (std::size_t record = 0; record < fft_records; ++record) {
            const auto value = fft.raw[record] * window_gain * o.fft_scale *
                               o.antenna[record / fft_length].correction;
            if (!std::isfinite(std::norm(value)))
                throw std::runtime_error("Preprocessed ML FFT overflow");
            fft.corrected[record] = value;
        }
        return;
    }
    const auto kept=fft_length-o.ml_startup_samples;
    std::vector<double> window(fft_length,0);
    double sum=0;
    for(std::size_t j=0;j<kept;++j) {
        const double v=o.ml_fft_hann?0.5-0.5*std::cos(2*pi*j/kept):1.0;
        sum+=v; window[o.ml_startup_samples+j]=v;
    }
    // Keep coherent-tone amplitudes in the same units as the original 1/N FFT.
    for(auto& w:window) w*=fft_length/sum;
    std::vector<AoaComplex> time(fft_length);
    for(std::size_t ch=0;ch<channels;++ch) {
        std::copy_n(fft.raw.begin()+ch*fft_length,fft_length,time.begin());
        ml_transform(time,true);
        // Do not compact/reindex the segment: absolute sampling phase is retained.
        // In particular, a legitimate complex 0 Hz carrier must remain present.
        for(std::size_t s=0;s<fft_length;++s) time[s]*=window[s];
        ml_transform(time,false);
        for(std::size_t k=0;k<fft_length;++k) {
            const auto value=time[k]*o.fft_scale*o.antenna[ch].correction;
            if(!std::isfinite(std::norm(value))) throw std::runtime_error("Preprocessed ML FFT overflow");
            fft.corrected[ch*fft_length+k]=value;
        }
    }
}
} // namespace aoa_app
