// [上位机新增] 用原 ML 核心验证显示导出没有改变结果，并通过真实 socket 提交契约包。
#include "console_aoa.hpp"
#include "console_npu.hpp"
#include "pl_fft_input.hpp"
#include <cassert>
#include <cmath>
#include <fstream>
#include <iostream>

namespace aoa_app {
// 原 app_config.cpp 还包含 POSIX IPC 头。此测试仅提供同等纯数值封装，核心算法仍链接原文件。
double bin_hz(std::size_t bin,double rate) {return pl_fft_bin_hz(bin,rate/4);}
std::uint32_t dds_word(const Options& o) {return static_cast<std::uint32_t>(std::llround(o.dds_hz/o.sample_rate*4294967296.0));}
}
int main(int argc,char** argv) {
    using namespace aoa_app;
    Options o;const double d=light_speed/433e6/2;
    const double x[6]={-2*d-.25,-d-.25,-.25,.25,d+.25,2*d+.25};
    for(int c=0;c<6;c++)o.antenna[c].x=x[c];
    o.bins={4};
    assert(bin_hz(4,o.sample_rate)==4*25e6/4096);
    assert(bin_hz(4095,o.sample_rate)==-25e6/4096);
    FftData fft;fft.raw.resize(fft_records);fft.corrected.resize(fft_records);fft.valid.assign(fft_records,1);
    const double angles[2][2]={{12,35},{-28,-8}};
    for(int c=0;c<6;c++) {
        AoaComplex y{};
        for(int p=0;p<2;p++) y+=std::polar(p==0?1.0:.6,2*pi*o.rf_hz/light_speed*x[c]*std::sin(angles[c/3][p]*pi/180));
        fft.raw[c*fft_length+4]=fft.corrected[c*fft_length+4]=y;
    }
    const auto original=analyze(o,fft,{});o.console_enabled=true;const auto with_grid=analyze(o,fft,{});
    assert(original.status=="ok");
    for(int g=0;g<2;g++) {
        assert(original.groups[g].angle==with_grid.groups[g].angle);
        assert(original.groups[g].amplitude_rms==with_grid.groups[g].amplitude_rms);
        assert(original.groups[g].residual==with_grid.groups[g].residual);
        assert(with_grid.groups[g].display_score.size()==161*161);
        for(int i=0;i<161;i++)assert(with_grid.groups[g].display_invalid[i*161+i]==1);
        for(int p=0;p<2;p++)assert(with_grid.groups[g].angle[p]==angles[g][p]);
    }
    CaptureState capture;capture.mode="offline_fft";capture.complete=true;
    CaptureBatch batch;batch.run_id="contract-001";batch.completed=4;batch.handoff_status="sent";
    // [部署核验新增] 独立 AOA 采样率不应得到可给旧 v6 模型消费的 ready 标志。
    auto incompatible=o;incompatible.sample_rate=99999999;incompatible.ipc_enabled=false;
    CaptureBatch rejected_batch;rejected_batch.npu_ready=true;
    publish_npu_input(incompatible,rejected_batch);
    assert(!rejected_batch.npu_ready&&rejected_batch.handoff_status=="unsupported_sample_rate");
    bool rate_rejected=false;
    try {npu_metadata(incompatible,rejected_batch,"file");}catch(const std::runtime_error&) {rate_rejected=true;}
    assert(rate_rejected);
    auto packet=console_packet(o,capture,batch,with_grid);
    pha_npu::Options no;no.backend="preprocess";
    pha_npu::Batch nb;nb.run_id=batch.run_id;nb.raw_sha256="fixture";
    for(auto& round:nb.iq) for(int i=0;i<pha_npu::kSamples;i++)round.push_back(std::polar(.4,2*pi*200e3*i/25e6));
    const auto features=pha_npu::preprocess(nb);
    const pha_npu::Json result={{"status","preprocessed"},{"npu_executed",false},{"total_ms",0}};
    auto n=pha_npu::console_packet(no,nb,features,result);
    assert(n["signal"]["wave_iq"].size()==512);
    assert(n["signal"]["tf_u8"].get<std::string>().size()==54616);
    assert(n["signal"]["spectrum_u8"].get<std::string>()==aoa_console::base64(
        std::vector<std::uint8_t>(features[1].u8.begin()+3*640,features[1].u8.end())));
    if(argc>1) {std::ofstream file(argv[1]);file<<aoa_console::Json::array({packet,n}).dump();}
    {
        aoa_console::Publisher ps("aoa"),npu("npu");
        ps.publish(packet);npu.publish(n);
        if(ps.enabled())std::this_thread::sleep_for(std::chrono::milliseconds(1200));
    }
    std::cout<<"PASS: unchanged ML estimates; grid/mask; original NPU preprocessing; packet export\n";
}
