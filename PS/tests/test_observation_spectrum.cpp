#include "observation.hpp"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <fstream>
#include <iostream>

namespace {
void put32(std::uint8_t* out,std::int32_t value) {
    const auto word=static_cast<std::uint32_t>(value);
    for(unsigned i=0;i<4;++i)out[i]=static_cast<std::uint8_t>(word>>(8*i));
}
void sample(std::vector<std::uint8_t>& slots,std::size_t i,std::int32_t re,std::int32_t im) {
    auto* out=slots.data()+i*aoa_app::slot_bytes+aoa_app::pha_offset;
    put32(out,re);put32(out+4,im);
}
} // namespace

int main(int argc,char** argv) {
    using namespace aoa_app;
    Options o;o.observe_waveform=o.observe_spectrum=true;o.capture_rounds=4;
    CaptureState capture;capture.complete=true;capture.mode="offline_fft";
    CaptureBatch batch;batch.run_id="observation_startup_test";
    std::vector<std::uint8_t> slots(fft_bytes,0);
    for(std::size_t i=0;i<pha_samples;++i) {
        const double a=2*pi*123*i/pha_samples,b=-2*pi*341*i/pha_samples;
        sample(slots,i,std::llround(1000000*std::cos(a)+200000*std::cos(b)+17000),
                       std::llround(1000000*std::sin(a)+200000*std::sin(b)-9000));
    }
    const auto original_slots=slots;
    const auto tone=observation_packet(o,capture,batch,slots);
    assert(slots==original_slots && tone.at("valid")==true);
    const auto& spectrum=tone.at("spectrum");
    assert(spectrum.at("bins")==4096 && spectrum.at("startup_samples")==64);
    assert(spectrum.at("retained_samples")==4032 && spectrum.at("preprocessing")=="zero64_hann4032");
    assert(spectrum.at("frequency_step_hz")==25e6/4096);
    assert(spectrum.at("peak_frequency_hz")==123*25e6/4096);
    // Arbitrarily strong changes confined to startup must not change a single spectral bin.
    for(std::size_t i=0;i<64;++i)sample(slots,i,2000000000,-1900000000);
    const auto transient=observation_packet(o,capture,batch,slots);
    assert(transient.at("spectrum")==spectrum);
    assert(transient.at("wave_iq_s32le")!=tone.at("wave_iq_s32le"));
    // The final valid sample must still participate; no truncation to 4032 positions.
    sample(slots,4095,2000000000,0);
    assert(observation_packet(o,capture,batch,slots).at("spectrum")!=spectrum);
    std::fill(slots.begin(),slots.end(),0);
    for(std::size_t i=0;i<64;++i)sample(slots,i,2000000000,-1900000000);
    const auto startup_only=observation_packet(o,capture,batch,slots);
    assert(startup_only.at("spectrum").at("peak_frequency_hz").is_null());
    assert(startup_only.at("spectrum").at("peak_amplitude_codes")==0);
    for(std::size_t i=64;i<pha_samples;++i)sample(slots,i,100000,0);
    const auto dc=observation_packet(o,capture,batch,slots);
    assert(dc.at("spectrum").at("peak_frequency_hz")==0);
    assert(std::abs(dc.at("spectrum").at("peak_amplitude_codes").get<double>()-100000)<1e-6);
    assert(dc.at("spectrum").at("dc_removed")==false);
    o.observe_spectrum=false;
    assert(!observation_packet(o,capture,batch,slots).contains("spectrum"));
    o.observe_waveform=false;o.observe_spectrum=true;
    assert(!observation_packet(o,capture,batch,slots).contains("wave_iq_s32le"));
    if(argc==2) {
        std::ofstream out(argv[1]);assert(out);
        out<<aoa_console::Json{{"tone",tone},{"startup_only",startup_only},{"dc",dc}}.dump();
    }
    std::cout<<"PASS PHA spectrum: startup rejection, full FFT, tail retained, raw IQ unchanged, DC/amplitude and switches\n";
}
