#ifdef NDEBUG
#undef NDEBUG
#endif
#include "archive_control.hpp"
#include "console_aoa.hpp"
#include "pl_fft_input.hpp"
#include "recording.hpp"
#include <cassert>
#include <filesystem>
#include <iostream>

// All inputs are local fixtures. A network request would fail the test.
namespace tcp_receiver {
void receive_exact(const Options&,std::size_t,std::vector<std::uint8_t>&,
                   const std::function<bool()>&) {throw std::runtime_error("Network forbidden");}
}
namespace aoa_app {
double bin_hz(std::size_t bin,double rate) {return pl_fft_bin_hz(bin,rate/4);}
std::uint32_t dds_word(const Options& o) {
    return static_cast<std::uint32_t>(std::llround(o.dds_hz/o.sample_rate*4294967296.0));
}
}
namespace {
void setting(const char* key,const std::string& value) {
#ifdef _WIN32
    _putenv_s(key,value.c_str());
#else
    if(value.empty())unsetenv(key);else setenv(key,value.c_str(),1);
#endif
}
void pack39(std::uint8_t* p,std::int64_t value) {
    const auto word=static_cast<std::uint64_t>(value)&((1ULL<<39)-1);
    for(int byte=0;byte<5;++byte)p[byte]=static_cast<std::uint8_t>(word>>(8*byte));
}
}
int main(int argc,char** argv) {
    using namespace aoa_app;
    if(argc!=2)return 2;
    const auto root=std::filesystem::absolute(argv[1]);
    if(std::filesystem::exists(root))throw std::runtime_error("Use a fresh test directory");
    std::filesystem::create_directories(root/"inputs");
    Options base;base.rf_hz=445e6;base.paths=1;base.capture_rounds=4;
    base.ipc_enabled=false;base.console_enabled=true;base.compact_results=true;
    base.offset_binary=false;base.adc_bits=12;base.adc_remove_dc=true;
    base.bins={4014};base.ml_startup_samples=64;base.ml_fft_hann=true;
    base.fft_preprocessing="pl-hann64";base.observe_waveform=true;base.observe_spectrum=true;
    const std::array<double,6> x{{-1.2823613348729794,-.9361806674364898,-.59,.59,.9361806674364898,1.2823613348729794}};
    for(std::size_t ch=0;ch<channels;++ch)base.antenna[ch].x=x[ch];
    std::vector<std::string> adc_sources;
    for(std::size_t frame=0;frame<4;++frame) {
        std::vector<std::uint8_t> slots(fft_bytes),adc(adc_bytes);
        for(std::size_t ch=0;ch<channels;++ch) {
            const auto z=std::polar(100000.0,2*pi*base.rf_hz/light_speed*x[ch]*std::sin(20*pi/180)+frame*.4);
            auto* p=slots.data()+(ch*fft_length+4014)*slot_bytes;
            pack39(p,std::llround(z.real()));pack39(p+5,std::llround(z.imag()));
        }
        for(std::size_t s=0;s<adc_samples;++s)for(std::size_t ch=0;ch<channels;++ch) {
            const auto word=static_cast<std::uint16_t>((s*13+ch*37+frame*71)&4095);
            adc[s*12+ch*2]=static_cast<std::uint8_t>(word);
            adc[s*12+ch*2+1]=static_cast<std::uint8_t>(word>>8);
        }
        const auto fft_name=(root/"inputs"/("fft"+std::to_string(frame)+".raw")).string();
        const auto adc_name=(root/"inputs"/("adc"+std::to_string(frame)+".raw")).string();
        save_bytes(fft_name,slots,slots.size());save_bytes(adc_name,adc,adc.size());
        base.fft_sources.push_back(fft_name);adc_sources.push_back(adc_name);
    }
    base.fft_raw=base.fft_sources.front();
    setting("AOA_ARCHIVE_SWITCH","1");
    setting("AOA_ARCHIVE_CONTROL_PATH",(root/"control.json").string());
    ArchiveControl control;
    std::vector<AoaComplex> reference_fft;
    std::vector<double> reference_angles;
    for(int step=0;step<3;++step) {
        const bool enabled=step==1;
        const auto folder=root/(enabled?"on":step==0?"startup_off":"off_again");
        std::filesystem::create_directory(folder);
        Options o=base;o.prefix=(folder/"aoa").string();o.save_results=true;o.record_raw=true;
        nlohmann::json command;
        if(step)command={{"schema",1},{"mode","archive"},{"id",std::to_string(step)},{"archive_enabled",enabled}};
        control.prepare(o,command,step!=0,true);
        assert(o.save_results==enabled&&o.record_raw==enabled);
        assert(o.observe_waveform&&o.observe_spectrum&&o.capture_rounds==4);
        setting("AOA_RECORDING_CONTEXT",enabled?"":(root/"must_not_be_read.json").string());
        CaptureBatch batch;CaptureState capture;std::vector<std::uint8_t> output;
        initialize_results(o);initialize_batch(o,batch);collect_batch(o,batch,capture,output,{});
        FftData fft;fft.corrected=batch.fft_snapshots.back();fft.snapshots=batch.fft_snapshots;
        fft.raw=fft.corrected;fft.valid.assign(fft_records,1);
        const auto analysis=analyze(o,fft,{});
        const auto packet=console_packet(o,capture,batch,analysis);
        assert(packet["type"]=="aoa"&&packet["groups"].size()==2&&analysis.direction_frames==4);
        std::vector<double> angles;
        for(const auto& group:analysis.groups)angles.insert(angles.end(),group.angle.begin(),group.angle.end());
        if(step==0){reference_fft=fft.corrected;reference_angles=angles;}
        else {assert(reference_fft==fft.corrected);assert(reference_angles==angles);}
        export_fft(o,fft);export_directions(o,analysis);publish_results(o);
        export_metadata(o,capture,analysis,batch);record_batch(o,batch,packet,analysis);
        assert(std::filesystem::exists(o.prefix+"_capture.json")==enabled);
        assert(std::filesystem::exists(o.prefix+"_round1_fft_slots.raw")==enabled);

        // The same real receive/convert path gates original ADC and PL input files.
        Options adc=o;adc.prefix=(folder/"adc").string();adc.fft_raw.clear();adc.fft_sources.clear();
        adc.adc_sources=adc_sources;adc.adc_raw=adc_sources.front();adc.dry_run=true;
        CaptureBatch adc_batch;CaptureState adc_state;std::vector<std::uint8_t> unused;
        initialize_batch(adc,adc_batch);collect_batch(adc,adc_batch,adc_state,unused,{});
        assert(adc_batch.rounds.size()==4&&adc_state.adc_received_bytes==adc_bytes);
        assert(std::filesystem::exists(adc.prefix+"_round1_adc.raw")==enabled);
        assert(std::filesystem::exists(adc.prefix+"_round1_input_slots.raw")==enabled);

        // Synthetic complete hardware-PHA metadata exercises the production handoff exporter.
        CaptureBatch pha=batch;pha.pha.assign(npu_rounds*pha_bytes,123);
        for(auto& round:pha.rounds){round.mode="hardware";round.complete=true;round.pha_collected=true;round.pha_written_samples=pha_samples;}
        Options npu=o;npu.prefix=(folder/"npu").string();publish_npu_input(npu,pha);
        assert(pha.npu_ready&&pha.pha.size()==npu_rounds*pha_bytes);
        assert(std::filesystem::exists(npu.prefix+"_pha4.raw")==enabled);
        if(!enabled)assert(std::filesystem::is_empty(folder));
    }
    setting("AOA_RECORDING_CONTEXT","");
    std::cout<<"PASS archive off/on/off: zero off files; ADC/FFT/PHA exports gated; four-frame data and ML telemetry unchanged\n";
}
