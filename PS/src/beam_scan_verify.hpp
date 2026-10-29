#pragma once
#include "beam_scan.hpp"
#include "capture_pha.hpp"
#include "ml_preprocessing.hpp"
#include <filesystem>

namespace aoa_app {
inline void inspect_scan_pha(const std::vector<std::uint8_t>& slots,CaptureState& state) {
    if(slots.size()<fft_bytes) throw std::runtime_error("Incomplete scan check buffer");
    state.pha_written_samples=0;state.pha_format_errors=0;
    for(std::size_t r=0;r<fft_records;++r) {
        if(!slot_valid(slots.data()+r*slot_bytes)) continue;
        if(r<pha_samples) ++state.pha_written_samples;
        else if(!std::all_of(slots.begin()+r*slot_bytes+pha_offset,
                            slots.begin()+r*slot_bytes+pha_offset+pha_sample_bytes,[](auto v){return v==0;}))
            ++state.pha_format_errors;
    }
}
inline std::int32_t scan_i32(const std::uint8_t* p) {
    std::uint32_t v=0;for(unsigned i=0;i<4;++i) v|=std::uint32_t(p[i])<<(8*i);
    return static_cast<std::int32_t>(v);
}
inline double scan_pha_power(const Options& o,const std::vector<std::uint8_t>& pha,
                             const std::vector<std::size_t>& bins) {
    if(pha.size()!=pha_bytes) throw std::runtime_error("Invalid scan PHA length");
    std::vector<AoaComplex> samples(fft_length);
    const auto kept=fft_length-o.ml_startup_samples;double window_sum=0;
    for(std::size_t i=o.ml_startup_samples;i<fft_length;++i) {
        const double w=o.ml_fft_hann?.5-.5*std::cos(2*pi*(i-o.ml_startup_samples)/kept):1;
        window_sum+=w;samples[i]=AoaComplex(scan_i32(pha.data()+i*8),scan_i32(pha.data()+i*8+4))*w;
    }
    ml_transform(samples,false);
    double power=0;
    for(auto b:bins) power+=std::norm(samples.at(b)*(fft_length/window_sum)*o.fft_scale);
    return power;
}
inline void verify_beam_scan(const Options& o,const FftData& fft,const CaptureBatch& batch,
                             Analysis& a,const std::function<bool()>& cancelled) {
    auto& s=a.beam_scan;
    if(o.processing_mode!="scan6" || !o.scan_verify || !s.valid) return;
    if(!o.fft_raw.empty()) {s.verification_status="offline_not_run";return;}
    if(batch.scan_input.size()!=input_bytes || batch.rounds.empty())
        throw std::runtime_error("Missing frozen ADC input for scan verification");
    const auto started=std::chrono::steady_clock::now();
    s.verification_status="checking";
    const auto& original=fft.snapshots.empty()?fft.corrected:fft.snapshots.back();
    bool changed=false;
    auto restore=[&] {
        if(changed) {
            restore_beam(o.beam.enabled?o.beam:beam_weights(o,"equal"),{});
            s.restore_verified=true;changed=false;
        }
    };
    try {
        for(const auto& c:s.candidates) {
            if(s.checks.size()>=o.scan_max_checks || (c.id>0 && -c.relative_db>o.scan_candidate_gap_db)) break;
            s.checks.emplace_back();auto& check=s.checks.back();
            check.candidate_id=c.id;check.angle_deg=c.angle_deg;
            check.capture_id=batch.run_id+"_scancheck"+std::to_string(c.id);
            check.expected_power=scan_power(o,original,a.bins,c.angle_deg);
            auto probe=o;probe.beam=beam_weights(o,"manual",c.angle_deg);
            CaptureState state;state.mode="hardware";
            const auto prefix=o.prefix+"_scancheck"+std::to_string(c.id);
            std::vector<std::uint8_t> slots(fft_bytes+slot_bytes,0xa5);
            changed=true;
            try {capture_fft(probe,batch.scan_input,slots,state,cancelled);}
            catch(...) {
                if(o.save_results) save_bytes(prefix+"_fft_slots.raw",slots,fft_bytes);
                throw;
            }
            const auto decoded=decode_fft(probe,slots,state);
            if(!state.complete || !state.guard_ok || state.pl_version!=batch.rounds.back().pl_version)
                throw std::runtime_error("Incomplete or incompatible scan check capture");
            inspect_scan_pha(slots,state);
            const auto pha=pha_frame(slots,state);
            if(o.save_results) {
                save_bytes(prefix+"_fft_slots.raw",slots,fft_bytes);
                save_bytes(prefix+"_pha.raw",pha,pha.size());
                check.fft_file=std::filesystem::path(prefix+"_fft_slots.raw").filename().string();
                check.pha_file=std::filesystem::path(prefix+"_pha.raw").filename().string();
                check.source_input_file=std::filesystem::path(batch.rounds.back().prefix+"_input_slots.raw").filename().string();
            }
            double error=0,energy=0;
            for(std::size_t i=0;i<fft_records;++i) {
                error+=std::norm(decoded.corrected[i]-original[i]);energy+=std::norm(original[i]);
            }
            check.input_shape_error=std::sqrt(error/std::max(energy,1e-300));
            check.observed_power=scan_pha_power(o,pha,a.bins);
            check.relative_error=std::abs(check.observed_power-check.expected_power)/std::max(check.expected_power,1e-300);
            check.registers_verified=state.beam_registers_verified;
            check.valid=check.registers_verified && check.input_shape_error<1e-6 && check.relative_error<.01;
            if(!check.valid) check.error="PL/input equivalence check failed";
        }
        restore();
        s.verification_status=!s.checks.empty() && std::all_of(s.checks.begin(),s.checks.end(),
            [](const auto& c){return c.valid;})?"matched":"mismatch";
        if(s.verification_status!="matched") {s.valid=false;s.status="verification_failed";a.status=s.status;}
    } catch(const std::exception& e) {
        if(!s.checks.empty()) s.checks.back().error=e.what();
        s.valid=false;s.status="verification_failed";s.verification_status="error";a.status=s.status;
        try {restore();} catch(const std::exception& restore_error) {
            s.verification_status="restore_failed";
            throw std::runtime_error(std::string("Scan beam restore failed: ")+restore_error.what());
        }
    }
    s.verification_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();
}
} // namespace aoa_app
