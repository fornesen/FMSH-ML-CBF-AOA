#include "app.hpp"
#include "pha_ipc.hpp"
#include "console_aoa.hpp" // [上位机新增] 独立 TCP 遥测，不复用 ZYNQ 采集连接。
#include "beam_control.hpp"
#include "capture_schedule.hpp"
#include "capture_mode.hpp"
#include "pl_fft_input.hpp"
#include "recording.hpp"
#include "beam_scan_verify.hpp"
#include <chrono>
#include <thread>
#include <csignal>
#include <iostream>
#include <filesystem>
#include <stdexcept>

namespace {
volatile std::sig_atomic_t stopped=0;
void request_stop(int) { stopped=1; }
}
namespace {
struct Counts {std::uint64_t attempted=0,sent=0,busy=0,absent=0,ipc_errors=0,errors=0;};
int run_batch(const aoa_app::Options& options,Counts& counts,aoa_console::Publisher& console,
              aoa_app::BeamController& beam) {
    using namespace aoa_app;
    const auto cancelled=[](){return stopped!=0;};
    CaptureState capture; CaptureBatch batch; Analysis analysis;
    // [合成IQ观测新增] 保留本批最后一轮PL槽供可选观测，仍只做一次硬件采集。
    std::vector<std::uint8_t> output;
    // [上位机新增] 成功/无信号/错误均携带批次状态；遥测异常不改变原任务返回码。
    const auto report=[&] {
        beam.finish(options,batch,capture,analysis,console);
        if(options.save_results) {
            const auto path=options.prefix+"_tracking.json";
            auto out=text_file(path+".tmp");out<<beam.state().dump(2)<<'\n';out.flush();out.close();
            std::filesystem::rename(path+".tmp",path);
        }
        const auto make_packet=[&] {
            auto packet=console_packet(options,capture,batch,analysis);
            packet["beam"]=beam.state();
            packet["observation"]=observation_packet(options,capture,batch,output);
            packet["counts"]={{"attempted",counts.attempted},{"ipc_sent",counts.sent},{"ipc_busy",counts.busy},
                {"ipc_absent",counts.absent},{"ipc_errors",counts.ipc_errors}};
            return packet;
        };
        if(options.record_raw) {
            const auto packet=make_packet();record_batch(options,batch,packet,analysis);
            console.safe([&] {return packet;});
        } else console.safe(make_packet);
    };
    capture.mode=options.fft_raw.empty()?(options.dry_run?"adc_only":"hardware"):"offline_fft";
    try {
        initialize_batch(options,batch);
        if(!options.ipc_enabled) batch.handoff_status="disabled";
        initialize_results(options); export_metadata(options,capture,analysis,batch);
        collect_batch(options,batch,capture,output,cancelled);
        if(options.dry_run) {
            analysis.status="adc_only"; publish_results(options); export_metadata(options,capture,analysis,batch);
            std::cout<<"ADC inputs processed for "<<batch.rounds.size()<<" round(s); no PL hardware accessed. NPU is not ready.\n";
            report(); std::cout<<std::flush;return 0;
        }
        // Retain the last raw FFT for export, but reuse its already decoded values.
        // No second startup removal/window is applied to any snapshot.
        if(batch.fft_snapshots.size()!=options.capture_rounds)
            throw std::runtime_error("Missing FFT snapshots in completed batch");
        FftData fft;
        fft.corrected=batch.fft_snapshots.back();
        fft.valid.assign(fft_records,1);
        fft.raw.resize(fft_records);
        for(std::size_t ch=0;ch<channels;++ch) for(std::size_t b=0;b<fft_length;++b)
            fft.raw[ch*fft_length+b]=pl_fft_decode80(output.data()+(ch*fft_length+b)*slot_bytes);
        fft.snapshots=std::move(batch.fft_snapshots);
        export_fft(options,fft);
        if(!capture.complete) throw std::runtime_error(capture.error.empty()?"Incomplete FFT slots; no direction estimate produced":capture.error);
        if(options.processing_mode=="scan6") {
            analysis=analyze(options,fft,cancelled);
            verify_beam_scan(options,fft,batch,analysis,cancelled);
        }
        if(options.capture_rounds==npu_rounds && options.ipc_enabled && capture.mode=="hardware") {
            publish_npu_input(options,batch); export_metadata(options,capture,Analysis{},batch);
            // [定位优先调度新增] 只降低识别交付频率；设备互斥仍完整保留。
            const bool npu_due=(counts.attempted-1)%options.npu_every==0;
            if(options.ipc_enabled && batch.npu_ready && npu_due) {
                const auto status=pha_ipc::try_publish(options.ipc_name,npu_metadata(options,batch,"shm"),batch.pha);
                batch.handoff_status=pha_ipc::name(status);
                if(status==pha_ipc::Publish::sent) ++counts.sent;
                else if(status==pha_ipc::Publish::busy) ++counts.busy;
                else if(status==pha_ipc::Publish::absent) ++counts.absent;
                else ++counts.ipc_errors;
            } else if(batch.npu_ready) batch.handoff_status=options.ipc_enabled?"rate_limited":"disabled";
            std::cout<<"PHA_IPC run_id="<<batch.run_id<<" status="<<batch.handoff_status
                     <<" bytes="<<batch.pha.size()<<" forwarded="<<counts.sent
                     <<" skipped_busy="<<counts.busy<<" skipped_absent="<<counts.absent
                     <<" ipc_errors="<<counts.ipc_errors<<'\n'<<std::flush;
            // [部署核验修正] AOA-only 非模型采样率不得打印 NPU_READY。
            if(options.save_results && batch.npu_ready) std::cout<<"NPU_READY: "<<options.prefix<<"_npu_ready.json\n";
            export_metadata(options,capture,Analysis{},batch);
        }
        std::cout<<"DOA uses "<<fft.snapshots.size()<<" independent FFT snapshots from this batch.\n";
        if(options.processing_mode!="scan6") analysis=analyze(options,fft,cancelled);
        export_directions(options,analysis);
        publish_results(options);
        std::cout<<"FFT slots="<<capture.written_slots<<", selected bins="<<analysis.bins.size()
                 <<", result="<<analysis.status<<'\n';
        for(const auto& group:analysis.groups) {
            std::cout<<"Group "<<group.id<<" ("<<group.channel.size()<<" antennas), J/E="
                     <<(group.energy>0?group.residual/group.energy:0)<<", ambiguous="<<group.ambiguous<<'\n';
            for(std::size_t path=0;path<group.angle.size();++path)
                std::cout<<"  Path "<<path<<": angle="<<group.angle[path]<<" deg, amplitude_rms="<<group.amplitude_rms[path]
                         <<", first-bin phase="<<group.phase_first_bin_deg[path]<<" deg, relative_power="
                         <<relative_path_power_db(group,path)<<" dB, active="<<group.active[path]<<'\n';
            if(group.has_best) std::cout<<"  Response independence="<<group.response_independence
                                       <<", steering condition="<<group.steering_condition<<'\n';
        }
        if(options.processing_mode!="scan6" && options.paths==2) std::cout<<"Two-arrival plane-wave fits; direct/reflection association is unassigned.\n";
        if(analysis.localization.valid)
            std::cout<<"Cross localization: x="<<analysis.localization.x<<" m, y="<<analysis.localization.y
                     <<" m, crossing angle="<<analysis.localization.crossing_angle_deg<<" deg\n";
        else std::cout<<"Cross localization unavailable: "<<analysis.localization.status<<'\n';
        if(options.min_bin_power==0) std::cout<<"No noise-floor threshold supplied; angles are fits, not detection confidence.\n";
        if(options.save_results) std::cout<<"Saved "<<options.prefix<<"_metadata.json and CSV/raw result files.\n";
        report(); export_metadata(options,capture,analysis,batch);std::cout<<std::flush;return 0;
    } catch(const std::exception& error) {
        capture.error=error.what(); analysis.complete=false; analysis.status="error";
        if(batch.completed!=options.capture_rounds) capture.complete=false;
        try { publish_results(options); export_metadata(options,capture,analysis,batch); } catch(const std::exception& save_error) {
            std::cerr<<"Result export failed: "<<save_error.what()<<'\n';
        }
        report(); std::cerr<<"Error: "<<error.what()<<'\n'; return 1;
    }
}

} // namespace
int main(int argc,char** argv) {
    using namespace aoa_app;
    for(int i=1;i<argc;++i) if(std::string(argv[i])=="--help" || std::string(argv[i])=="-h") {usage(argv[0]);return 0;}
    Options base;
    try {base=parse_options(argc,argv);} catch(const std::exception& e) {std::cerr<<"Configuration error: "<<e.what()<<'\n';return 2;}
#ifdef ICRAFT_AOA_HOST_ONLY
    if(base.fft_raw.empty()&&!base.dry_run) {std::cerr<<"Host-only build requires --fft-raw or --dry-run; no TCP/PL capture started.\n";return 2;}
#endif
    std::signal(SIGINT,request_stop);std::signal(SIGTERM,request_stop);
    Counts counts;
    // [上位机新增] AOA_CONSOLE_HOST=<PC IPv4> 开启；端口默认 9000，可用环境变量覆盖。
    aoa_console::Publisher console("aoa"); base.console_enabled=console.enabled();
    // [波束控制新增] 离线回放/仅 ADC 模式不提供硬件控制。
    BeamController beam(base.console_enabled && !base.dry_run && base.fft_raw.empty());
    std::cout<<"CAPTURE_SERVICE save_results="<<base.save_results<<" ipc="<<base.ipc_enabled
             <<" max_batches="<<base.max_batches<<std::endl;
    int last=0;
    while(!stopped&&(!base.max_batches||counts.attempted<base.max_batches)) {
        auto options=base;
        if(options.save_results&&!options.once)
            options.prefix=base.prefix+"_"+std::to_string(std::chrono::system_clock::now().time_since_epoch().count())+"_"+std::to_string(counts.attempted+1);
        beam.prepare(options,console);
        // [板端保存调度新增] 开始采样前让出执行，避免归档积压耗尽 RAM。
        try {
            // Continue polling control while old originals await PC ACK. A user
            // may turn recording off and resume ordinary acquisition immediately.
            if(options.save_results && !wait_for_archive_capacity(base.prefix,[&]() {
                    beam.prepare(options,console);return stopped!=0 || !options.save_results;
                }) && stopped) break;
        } catch(const std::exception& e) {
            std::cerr<<"Error: board archive scheduling: "<<e.what()<<std::endl;
            return 1;
        }
        // A runtime enable command also needs a unique output prefix.
        if(options.save_results&&!options.once && options.prefix==base.prefix)
            options.prefix=base.prefix+"_"+std::to_string(std::chrono::system_clock::now().time_since_epoch().count())+"_"+std::to_string(counts.attempted+1);
        ++counts.attempted;
        schedule_capture_rounds(options);
        last=run_batch(options,counts,console,beam);if(last&&!stopped) ++counts.errors;
        // [归档异常修正] 失败批次交给 wrapper 在写入者退出后保存，不能积满队列重试。
        if(last && std::getenv("AOA_ARCHIVE_BACKLOG")) break;
        // [双模式新增] 识别模式严格一批定位、一批识别；定位模式完全不提交/等待 NPU。
        if(!last && options.ipc_enabled && options.npu_every==1 && options.fft_raw.empty()) {
            const auto end=std::chrono::steady_clock::now()+std::chrono::seconds(30);
            while(!stopped && npu_job_pending(options.ipc_name)) {
                if(std::chrono::steady_clock::now()>=end) {std::cerr<<"NPU_WAIT_TIMEOUT\n";break;}
                std::this_thread::sleep_for(std::chrono::milliseconds(20));
            }
        }
        if(base.once) return stopped?0:last;
        if(stopped|| (base.max_batches&&counts.attempted>=base.max_batches)) break;
        const auto delay=last?base.retry_delay_ms:base.interval_ms;
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(delay);
        while(!stopped&&std::chrono::steady_clock::now()<deadline) std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    std::cout<<"CAPTURE_STOP attempted="<<counts.attempted<<" forwarded="<<counts.sent
             <<" skipped_busy="<<counts.busy<<" skipped_absent="<<counts.absent
             <<" ipc_errors="<<counts.ipc_errors<<" errors="<<counts.errors<<std::endl;
    return counts.errors?1:0;
}
