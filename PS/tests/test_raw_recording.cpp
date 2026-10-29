#ifdef NDEBUG
#undef NDEBUG
#endif
#include "app.hpp"
#include "recording.hpp"
#include "console_aoa.hpp"
#include "pl_fft_input.hpp"
#include <cassert>
#include <filesystem>
#include <iostream>
namespace aoa_app {
double bin_hz(std::size_t bin,double rate) {return pl_fft_bin_hz(bin,rate/4);}
std::uint32_t dds_word(const Options& o) {
    return static_cast<std::uint32_t>(std::llround(o.dds_hz/o.sample_rate*4294967296.0));
}
}
namespace {
void pack39(std::uint8_t* p,std::int64_t value) {
    const auto word=static_cast<std::uint64_t>(value)&((1ULL<<39)-1);
    for(int b=0;b<5;++b)p[b]=static_cast<std::uint8_t>(word>>(8*b));
}
}
int main(int argc,char** argv) {
    using namespace aoa_app;
    if(argc!=2)return 2;
    const auto root=std::filesystem::absolute(argv[1]);std::filesystem::create_directories(root);
    for(const auto mode:{"angle6","position","position2","legacy","no_signal","headless"}) {
        Options o;o.record_raw=true;o.save_results=true;o.compact_results=true;o.console_enabled=true;
        if(std::string(mode)=="headless")o.console_enabled=false;
        o.processing_mode=std::string(mode).find("position")==0?"position":"angle6";
        o.rf_hz=445e6;o.capture_rounds=4;o.ipc_enabled=false;
        o.ml_startup_samples=64;o.ml_fft_hann=true;o.adc_bits=12;o.offset_binary=false;o.adc_remove_dc=true;
        o.band_min=-550000;o.band_max=-450000;o.paths=1;o.prefix=(root/mode).string();
        if(std::string(mode)=="position2")o.paths=2;
        const std::array<double,6> x{{-1.2823613348729794,-.9361806674364898,-.59,.59,.9361806674364898,1.2823613348729794}};
        for(std::size_t ch=0;ch<6;++ch)o.antenna[ch].x=x[ch];
        assert(recording_options(recording_replay_options(recording_options(o)))==recording_options(o));
        CaptureBatch batch;batch.run_id=std::string("fixture_")+mode;batch.handoff_status="disabled";
        initialize_results(o);
        FftData fft;CaptureState final;
        for(std::size_t t=0;t<4;++t) {
            CaptureState state;state.mode="hardware";state.prefix=o.prefix+"_round"+std::to_string(t+1);
            state.started_unix_ns=1000000000+static_cast<std::int64_t>(t)*1000000000;
            state.finished_unix_ns=state.started_unix_ns+500000000;
            std::vector<std::uint8_t> adc(adc_bytes),slots(fft_bytes,0);
            for(std::size_t s=0;s<adc_samples;++s)for(std::size_t ch=0;ch<6;++ch) {
                const auto word=static_cast<std::uint16_t>((s*13+ch*37+t*71)&4095);
                adc[s*12+ch*2]=static_cast<std::uint8_t>(word);adc[s*12+ch*2+1]=static_cast<std::uint8_t>(word>>8);
            }
            const auto input=adc_to_slots(o,adc,&state.adc_dc_removed);
            state.adc_dc_applied=true;state.adc_received_bytes=adc.size();
            for(std::size_t ch=0;ch<6;++ch) {
                AoaComplex y{};
                const double angle=ch<3?20:-20;
                y=std::polar(100000.0,2*pi*o.rf_hz/light_speed*x[ch]*std::sin(angle*pi/180)+t*pi/2);
                auto* p=slots.data()+(ch*fft_length+4014)*slot_bytes;
                if(std::string(mode)!="no_signal") {
                    pack39(p,std::llround(y.real()));pack39(p+5,std::llround(y.imag()));
                }
            }
            std::vector<std::uint8_t> pha(pha_bytes);
            for(std::size_t s=0;s<pha_samples;++s)for(std::size_t b=0;b<8;++b) {
                const auto byte=static_cast<std::uint8_t>((s*13+t*71+b*37)&255);
                slots[s*64+16+b]=byte;pha[s*8+b]=byte;
            }
            state.pl_version=std::string(mode)=="legacy"?0x20261020:0x00010001;
            state.complete=true;state.guard_checked=state.guard_ok=true;
            state.pha_collected=state.pha_saved=true;state.pha_written_samples=pha_samples;
            auto decoded=decode_fft(o,slots,state);fft.snapshots.push_back(decoded.corrected);
            fft.raw=std::move(decoded.raw);fft.corrected=std::move(decoded.corrected);fft.valid=std::move(decoded.valid);
            save_bytes(state.prefix+"_adc.raw",adc,adc.size());save_bytes(state.prefix+"_input_slots.raw",input,input.size());
            save_bytes(state.prefix+"_fft_slots.raw",slots,slots.size());save_bytes(state.prefix+"_pha.raw",pha,pha.size());
            batch.rounds.push_back(state);++batch.completed;final=state;
        }
        const auto analysis=analyze(o,fft,{});assert(analysis.complete && analysis.direction_frames==4);
        export_directions(o,analysis);publish_results(o);
        const auto packet=console_packet(o,final,batch,analysis);
        record_batch(o,batch,packet,analysis);export_metadata(o,final,analysis,batch);
        auto restored=recording_replay_options(recording_options(o));
        const auto again=analyze(restored,fft,{});
        assert(analysis.joint.angle==again.joint.angle);
        assert(analysis.localization.valid==again.localization.valid);
        o.record_raw=false;o.prefix=(root/(std::string(mode)+"_disabled")).string();
        record_batch(o,batch,packet,analysis);assert(!std::filesystem::exists(o.prefix+"_capture.json"));
    }
    std::cout<<"Paired raw recording fixture: ADC/PL/PHA/options/result with four ordered rounds.\n";
}
