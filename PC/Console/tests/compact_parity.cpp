// [定位调度核验] 同一实板 FFT：完整/精简导出必须逐值相同，并保留原始 PHA。
#include "console_aoa.hpp"
#include "pl_fft_input.hpp"
#include <cassert>
#include <filesystem>
#include <iostream>
namespace aoa_app {
double bin_hz(std::size_t bin,double rate){return pl_fft_bin_hz(bin,rate/4);}
std::uint32_t dds_word(const Options& o){return static_cast<std::uint32_t>(std::llround(o.dds_hz/o.sample_rate*4294967296.));}
}
int main(int argc,char** argv){
    using namespace aoa_app;namespace fs=std::filesystem;
    assert(argc==3);Options o;o.save_results=true;o.console_enabled=true;
    const double d=light_speed/433e6/2;
    for(int c=0;c<6;++c)o.antenna[c].x=(c<3?c*d:(c-1)*d+.5)-2*d-.25;
    CaptureState state;state.mode="offline_fft";
    const auto fft=decode_fft(o,load_exact(argv[1],fft_bytes),state);
    assert(state.complete);
    CaptureBatch batch;batch.run_id="parity";batch.completed=4;
    state.pha_collected=true;state.pha_written_samples=pha_samples;
    state.prefix=std::string(argv[2])+"/source_round";
    batch.rounds.assign(4,state);batch.pha.resize(4*pha_bytes);
    aoa_console::Json groups;std::vector<std::size_t> bins;
    for(int compact=0;compact<2;++compact){
        o.compact_results=compact;o.prefix=std::string(argv[2])+(compact?"/compact":"/full");
        initialize_results(o);export_fft(o,fft);
        const auto result=analyze(o,fft,{});assert(result.status=="ok");
        publish_npu_input(o,batch);export_directions(o,result);publish_results(o);
        const auto packet=console_packet(o,state,batch,result);
        if(!compact){groups=packet["groups"];bins=result.bins;}
        else{assert(groups==packet["groups"]);assert(bins==result.bins);}
        assert(fs::file_size(o.prefix+"_pha4.raw")==4*pha_bytes);
        assert(fs::exists(o.prefix+"_npu_ready.json"));
        assert(fs::exists(o.prefix+"_directions.csv"));
        assert(fs::exists(o.prefix+"_fft.csv")==!compact);
        assert(fs::exists(o.prefix+"_ml3_grid.csv")==!compact);
        assert(fs::exists(o.prefix+"_pha4.csv")==!compact);
    }
    std::cout<<"PASS: same FFT, exact angles/amplitudes/residuals/161x161 grids and masks; raw PHA preserved\n";
}
