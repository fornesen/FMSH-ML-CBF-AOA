#pragma once
// [合成IQ观测新增] 只读取PL已输出的PHA；不使用ML六路FFT，不改波束或识别输入。
#include "app.hpp"
#include "pl_contract.hpp"
#include "console/console_transport.hpp"
#include <algorithm>
#include <cmath>
#include <complex>
#include <cstring>
#include <deque>
#include <stdexcept>

namespace aoa_app {
class ObservationControl {
    using Json=aoa_console::Json;
    bool initialized_=false,wave_=false,spectrum_=false;
    std::string id_,status_="idle",error_;
    std::deque<std::string> seen_;
public:
    void prepare(Options& o,const Json& command,bool received,bool available) {
        if(!initialized_) {wave_=o.observe_waveform;spectrum_=o.observe_spectrum;initialized_=true;}
        if(received) {
            try {
                id_=command.at("id").get<std::string>();status_="pending";error_.clear();
                if(id_.empty()||id_.size()>64||!available||command.value("expired",false)||command.at("schema")!=1)
                    throw std::invalid_argument("Observation control unavailable/invalid/expired");
                if(std::find(seen_.begin(),seen_.end(),id_)!=seen_.end())
                    throw std::invalid_argument("Duplicate observation command");
                if(!command.at("waveform_enabled").is_boolean()||!command.at("spectrum_enabled").is_boolean())
                    throw std::invalid_argument("Expected boolean observation switches");
                const bool wave=command.at("waveform_enabled").get<bool>();
                const bool spectrum=command.at("spectrum_enabled").get<bool>();
                seen_.push_back(id_);if(seen_.size()>32)seen_.pop_front();
                wave_=wave;spectrum_=spectrum;
            } catch(const std::exception& e) {status_="rejected";error_=e.what();}
        }
        o.observe_waveform=wave_;o.observe_spectrum=spectrum_;
    }
    // 回执仅确认开关设置已在批次边界采用；本批PHA有效性另由packet.valid/error说明。
    void finish() {if(status_=="pending")status_="applied";}
    Json state(bool available) const {
        return {{"available",available},{"waveform_enabled",wave_},{"spectrum_enabled",spectrum_},
            {"command_id",id_},{"command_status",status_},{"error",error_}};
    }
};

inline void observation_u32(std::vector<std::uint8_t>& out,std::uint32_t word) {
    for(unsigned i=0;i<4;++i)out.push_back(static_cast<std::uint8_t>(word>>(8*i)));
}
inline double observation_i32(const std::uint8_t* p) {
    const std::uint32_t word=std::uint32_t(p[0])|(std::uint32_t(p[1])<<8)|
        (std::uint32_t(p[2])<<16)|(std::uint32_t(p[3])<<24);
    return word<=0x7fffffffu?double(word):double(word)-4294967296.0;
}

inline aoa_console::Json observation_packet(const Options& o,const CaptureState& capture,
    const CaptureBatch& batch,const std::vector<std::uint8_t>& slots) {
    using aoa_console::Json;
    Json p={{"source","PL_PHA_beamformed_IQ"},{"run_id",batch.run_id},
        {"waveform_enabled",o.observe_waveform},{"spectrum_enabled",o.observe_spectrum},
        {"sample_count",pha_samples},{"sample_rate_hz",o.sample_rate/4},
        {"round_index",o.capture_rounds-1},{"iq_scale",1048576},
        {"beam_mode",o.beam.mode},{"phase_rad",o.beam.phase_rad},
        {"integrity","paired_slot_structure_no_independent_PHA_valid_count"},
        {"valid",false},{"error",""}};
    if(!o.observe_waveform&&!o.observe_spectrum)return p;
    try {
        if(!capture.complete||slots.size()<fft_bytes||
           (capture.mode=="hardware"&&(!capture.guard_checked||!capture.guard_ok)))
            throw std::runtime_error("No complete PL output for this batch");
        std::vector<std::uint8_t> iq;iq.reserve(pha_bytes);
        for(std::size_t i=0;i<fft_records;++i) {
            const auto* slot=slots.data()+i*slot_bytes;
            if(!slot_valid(slot))throw std::runtime_error("Invalid paired FFT/PHA slot");
            if(i<pha_samples)iq.insert(iq.end(),slot+pha_offset,slot+pha_offset+pha_sample_bytes);
            else if(!std::all_of(slot+pha_offset,slot+pha_offset+pha_sample_bytes,[](auto v){return v==0;}))
                throw std::runtime_error("Unexpected PHA tail outside4096 samples");
        }
        // Waveform preserves all4096 signed32 I/Q samples, including the startup transient.
        if(o.observe_waveform)p["wave_iq_s32le"]=aoa_console::base64(iq);
        if(o.observe_spectrum) {
            constexpr std::size_t n=pha_samples;
            constexpr std::size_t startup=pl_fft_startup_samples,retained=n-startup;
            static_assert(startup<n,"PHA spectrum needs valid samples after startup");
            std::vector<std::complex<double>> fft(n);
            double window_sum=0;
            // Exclude the startup transient only in the spectrum working copy.
            // Keep the original 4096-sample time grid; Hann spans the valid 4032 samples.
            for(std::size_t i=startup;i<n;++i) {
                const double window=.5-.5*std::cos(2*pi*(i-startup)/retained);window_sum+=window;
                fft[i]={observation_i32(iq.data()+i*8)*window,observation_i32(iq.data()+i*8+4)*window};
            }
            // Display-only complex FFT of PL beam output. Keep DC: it may be a real
            // baseband carrier. Do not fold negative frequencies or add ML channel FFTs.
            for(std::size_t i=1,j=0;i<n;++i) {
                auto bit=n>>1;for(;j&bit;bit>>=1)j^=bit;j^=bit;
                if(i<j)std::swap(fft[i],fft[j]);
            }
            for(std::size_t size=2;size<=n;size*=2) {
                const auto root=std::polar(1.0,-2*pi/size);
                for(std::size_t start=0;start<n;start+=size) {
                    std::complex<double> phase=1;
                    for(std::size_t i=start;i<start+size/2;++i,phase*=root) {
                        const auto even=fft[i],odd=phase*fft[i+size/2];
                        fft[i]=even+odd;fft[i+size/2]=even-odd;
                    }
                }
            }
            std::size_t peak_index=0;
            double peak=0;
            for(std::size_t i=0;i<n;++i)if(std::abs(fft[i])>peak) {peak=std::abs(fft[i]);peak_index=i;}
            std::vector<std::uint8_t> db;db.reserve(n*4);
            for(std::size_t i=0;i<n;++i) {
                const double amplitude=std::abs(fft[(i+n/2)%n]);
                const float value=peak>0?static_cast<float>(20*std::log10(std::max(1e-6,amplitude/peak))):-120.f;
                std::uint32_t bits;static_assert(sizeof(value)==sizeof(bits),"Float32 required");
                std::memcpy(&bits,&value,sizeof(bits));observation_u32(db,bits);
            }
            const double fs=o.sample_rate/4;
            const auto signed_bin=peak_index>=n/2?int(peak_index)-int(n):int(peak_index);
            p["spectrum"]={{"bins",n},{"db_f32le",aoa_console::base64(db)},
                {"frequency_start_hz",-fs/2},{"frequency_step_hz",fs/n},
                {"db_min",-120},{"db_max",0},{"reference","per_frame_peak"},
                {"window","periodic_Hann"},{"dc_removed",false},
                {"preprocessing","zero64_hann4032"},{"startup_samples",startup},{"retained_samples",retained},
                {"peak_frequency_hz",peak>0?Json(signed_bin*fs/n):Json(nullptr)},
                {"peak_amplitude_codes",peak/window_sum}};
        }
        p["valid"]=true;
    } catch(const std::exception& e) {
        p.erase("wave_iq_s32le");p.erase("spectrum");p["error"]=e.what();
    }
    return p;
}
} // namespace aoa_app
