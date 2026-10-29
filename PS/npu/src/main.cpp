#include "pha_npu.hpp"
#include "pha_ipc.hpp"
#include "console_npu.hpp" // [上位机新增] 遥测适配；模型及 IPC ABI 保持原协议。
#include <csignal>
#include <memory>
#include <cfenv>
#include <chrono>
#include <cmath>
#include <fcntl.h>
#include <iostream>
#include <stdexcept>
#include <sys/file.h>
#include <thread>
#include <unistd.h>

namespace {
using namespace pha_npu;
volatile std::sig_atomic_t stopped=0;
void stop_signal(int) {stopped=1;}
void help() {
    std::cout<<"Usage: icraft_npu_yolo [options]\n"
      "  Default: continuous shared-memory consumer; no result files.\n"
      "  Model classes: FM / AM / CW / noise / OOK / PM (6 logits).\n"
      "  --save-results                    Enable original raw/features/CSV/JSON dumps\n"
      "  --ipc-name /NAME                  Default /icraft_pha4\n"
      "  --once / --max-batches N          Exit after one/N received batches\n"
      "  --manifest FILE                   Legacy file replay, one batch then exit\n"
      "  --model-json FILE --model-raw FILE  Compiled Icraft network/parameters\n"
      "  --model-contract FILE             Matching model identity contract; inferred beside JSON\n"
      "  --backend npu|host|preprocess      Default npu; host is CPU reference\n"
      "  --device URI                      Default axi://zg330aiu?npu=0x40000000&dma=0x80000000\n"
      "  --input-gain VALUE                One fixed gain for all four rounds, default1\n"
      "  --amplitude-mode batch|fixed      Default batch: bounded common weak-signal gain\n"
      "  --amplitude-target-rms VALUE      Batch 95th-percentile RMS target, default0.25\n"
      "  --amplitude-max-gain VALUE        Maximum extra batch gain, default256\n"
      "  --amplitude-min-coherence VALUE   Lag-one coherence gate, default0.5\n"
      "  --raw-file FILE                   Explicit replay override for relocated PHA\n"
      "  --wait-seconds VALUE              Wait for manifest publication, default0\n"
      "  --force                           Explicitly rerun a completed run/backend\n"
      "--save-results saves source IQ, tensors, raw output, logits/probabilities and CSV.\n";
}
Options arguments(int argc,char** argv) {
    Options o;
    const auto exe=fs::canonical("/proc/self/exe").parent_path();
    o.model_json=exe/"models/fm_sixsum_fm_pm_v8_ZG.json";o.model_raw=exe/"models/fm_sixsum_fm_pm_v8_ZG.raw";
    const auto number=[](const std::string& value) {std::size_t used=0;const auto n=std::stod(value,&used);
        if(used!=value.size()||!std::isfinite(n)) throw std::runtime_error("Expected finite number");
        return n;};
    for(int i=1;i<argc;++i) {
        const std::string arg=argv[i];
        const auto value=[&]() {if(++i>=argc) throw std::runtime_error("Missing value for "+arg);return std::string(argv[i]);};
        if(arg=="--save-results") o.save_results=true;
        else if(arg=="--once") o.once=true;
        else if(arg=="--ipc-name") o.ipc_name=value();
        else if(arg=="--max-batches") {const auto n=number(value());if(n<0||n>1000000000||std::trunc(n)!=n) throw std::runtime_error("Invalid max-batches");o.max_batches=static_cast<std::uint64_t>(n);}
        else if(arg=="--manifest") o.manifest=value();
        else if(arg=="--output-dir") o.output_dir=value();
        else if(arg=="--model-json") o.model_json=value();
        else if(arg=="--model-raw") o.model_raw=value();
        else if(arg=="--model-contract") o.model_contract=value();
        else if(arg=="--raw-file") o.raw_override=value();
        else if(arg=="--backend") o.backend=value();
        else if(arg=="--device") o.device=value();
        else if(arg=="--input-gain") o.input_gain=number(value());
        else if(arg=="--amplitude-mode") o.amplitude_mode=value();
        else if(arg=="--amplitude-target-rms") o.amplitude_target_rms=number(value());
        else if(arg=="--amplitude-max-gain") o.amplitude_max_gain=number(value());
        else if(arg=="--amplitude-min-coherence") o.amplitude_min_coherence=number(value());
        else if(arg=="--wait-seconds") o.wait_seconds=number(value());
        else if(arg=="--force") o.force=true;
        else if(arg=="--help" || arg=="-h") {help();std::exit(0);}
        else throw std::runtime_error("Unknown option: "+arg);
    }
    pha_ipc::validate_name(o.ipc_name);
    if(!o.manifest.empty()) o.once=true;
    if(o.once&&o.max_batches>1) throw std::runtime_error("--once/file replay conflicts with max-batches>1");
    if(o.once) o.max_batches=1;
    if(o.manifest.empty()&&!o.raw_override.empty()) throw std::runtime_error("raw-file requires manifest replay");
    if(o.backend!="npu" && o.backend!="host" && o.backend!="preprocess") throw std::runtime_error("Unknown backend");
    validate_amplitude_options(o);
    if(!std::isfinite(o.wait_seconds)||o.wait_seconds<0) throw std::runtime_error("Invalid wait timeout");
    if(o.backend!="preprocess" && (o.model_json.empty()||o.model_raw.empty()))
        throw std::runtime_error("Inference needs compiled --model-json and --model-raw; TorchScript .pt is not a board network");
    return o;
}
class DirectoryLock {
    int fd_=-1;
public:
    explicit DirectoryLock(const fs::path& directory) {
        fd_=::open((directory/".consumer.lock").c_str(),O_CREAT|O_RDWR,0600);
        if(fd_<0) throw std::runtime_error("Cannot open consumer lock");
        if(::flock(fd_,LOCK_EX|LOCK_NB)) {::close(fd_);fd_=-1;throw std::runtime_error("Another consumer uses this output directory");}
    }
    ~DirectoryLock() {if(fd_>=0) {::flock(fd_,LOCK_UN);::close(fd_);}}
};
}
namespace {
int run_job(const Options& o,const Batch& batch,aoa_console::Publisher& console) {
    fs::path error_path;
    try {
        const auto started=std::chrono::steady_clock::now();
        std::unique_ptr<DirectoryLock> lock;
        if(o.save_results) {fs::create_directories(o.output_dir);lock=std::make_unique<DirectoryLock>(o.output_dir);}
        const auto prefix=o.output_dir/batch.safe_id;
        const fs::path result_path=prefix.string()+"_"+o.backend+"_result.json";
        if(o.save_results) error_path=prefix.string()+"_"+o.backend+"_error.json";
        if(o.save_results && fs::exists(result_path) && !o.force) {
            const auto done=Json::parse(read_text(result_path));
            if(done.at("run_id")!=batch.run_id || done.at("source_raw_sha256")!=batch.raw_sha256)
                throw std::runtime_error("A completed run_id exists with different data; use a new run_id");
            const auto prior=done.value("amplitude_preprocessing",Json::object());
            if(done.value("fixed_input_gain",1.)!=o.input_gain ||
               prior.value("mode",std::string("fixed"))!=o.amplitude_mode ||
               (o.amplitude_mode=="batch" &&
                (prior.value("target_rms",0.)!=o.amplitude_target_rms ||
                 prior.value("max_gain",0.)!=o.amplitude_max_gain ||
                 prior.value("min_coherence",-1.)!=o.amplitude_min_coherence)))
                throw std::runtime_error("Completed run uses different amplitude preprocessing; use --force to replay");
            std::cout<<"Already consumed run_id="<<batch.run_id<<" backend="<<o.backend<<"; use --force to replay.\n";
            // [上位机新增] 重复文件批次也显式标记，避免界面一直等待。
            console.safe([&] {return Json{{"type","npu"},{"run_id",batch.run_id},{"status","already_consumed"},
                {"backend",o.backend},{"npu_executed",false}};});
            return 3;
        }
        // On explicit replay revoke old completion before any artifact changes.
        if(o.save_results && o.force && fs::exists(result_path)) fs::remove(result_path);
        const auto f=preprocess(batch);
        Json result={{"format_version",2},{"run_id",batch.run_id},{"backend",o.backend},
            {"npu_executed",false},{"status","preprocessed"},
            {"source_manifest",batch.manifest_path.string()},{"source_raw_file",batch.raw_path.string()},
            {"source_raw_sha256",batch.raw_sha256},{"source_byte_count",batch.raw.size()},
            {"source_manifest_contents",batch.manifest},{"fixed_input_gain",o.input_gain},
            {"amplitude_preprocessing",batch.amplitude_preprocessing},
            {"effective_input_gain",batch.amplitude_preprocessing.at("effective_input_gain")},
            {"discard_per_round",64},{"valid_iq_shape",{4,4032,2}},
            {"iq_divisor",kIqScale},{"stft_window",1024},{"stft_hop",64},{"stft_frames_per_round",48},
            {"no_cross_round_windows",true},{"per_round_normalization",false},
            {"class_order",kClassNames},{"class_count",kClassCount},{"model_family","preprocess_only"}};
        if(o.save_results) {
            const fs::path copy_path=prefix.string()+"_source_pha4.raw";
            write_bytes_atomic(copy_path,batch.raw.data(),batch.raw.size());
            result["source_snapshot"]=fs::absolute(copy_path).string();
            result["features"]=dump_features(f,prefix);
        } else {
            result["features"]=Json::array();
            for(const auto& t:f) result["features"].push_back({{"name",t.name},{"shape",t.shape},{"uint8_sha256",sha256(t.u8)}});
        }
        result["preprocess_ms"]=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();
        if(o.backend!="preprocess") {
            const auto inference_started=std::chrono::steady_clock::now();
            const auto inference=infer_icraft(o,f,prefix);
            result["inference_ms"]=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-inference_started).count();
            result["classification"]=postprocess(inference.logits);
            result["runtime"]=inference.details;
            result["model_identity"]=inference.details.at("model_identity");
            result["model_family"]=result["model_identity"].at("model_family");
            result["npu_executed"]=(o.backend=="npu");result["status"]="inferred";
            if(o.save_results) {
            const fs::path logits_path=prefix.string()+"_"+o.backend+"_logits.f32";
            write_bytes_atomic(logits_path,inference.logits.data(),inference.logits.size()*sizeof(float));
            result["logits_file"]=fs::absolute(logits_path).string();
            std::string csv="class_index,class_name,logit,probability\n";
            const auto& c=result["classification"];
            for(std::size_t i=0;i<kClassCount;++i) csv+=std::to_string(i)+","+c["classes"][i].get<std::string>()+","+
                c["logits"][i].dump()+","+c["probabilities"][i].dump()+"\n";
            const fs::path csv_path=prefix.string()+"_"+o.backend+"_scores.csv";
            write_bytes_atomic(csv_path,csv.data(),csv.size());result["scores_file"]=fs::absolute(csv_path).string();
            }
        }
        result["total_ms"]=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();
        if(o.save_results) {
            write_json_atomic(result_path,result);
            if(fs::exists(error_path)) fs::remove(error_path);
        }
        Json summary={{"run_id",batch.run_id},{"backend",o.backend},{"npu_executed",result["npu_executed"]},
            {"status",result["status"]},{"source_raw_sha256",batch.raw_sha256},{"total_ms",result["total_ms"]},
            {"save_results",o.save_results},{"class_order",kClassNames},{"class_count",kClassCount}};
        summary["model_family"]=result["model_family"];
        if(result.contains("classification")) summary["classification"]=result["classification"];
        // SDK progress output may leave a carriage return/ANSI erase on stdout.
        std::cout<<"\nNPU_RESULT "<<summary.dump()<<std::endl;
        // [上位机新增] 在推理完成后提交复制数据；后台发送不会持有 Batch/Features 引用。
        console.safe([&] {return console_packet(o,batch,f,result);});
        return 0;
    } catch(const std::exception& error) {
        std::cerr<<"NPU_ERROR run_id="<<batch.run_id<<" error="<<error.what()<<std::endl;
        // [上位机新增] 带 run_id 的错误，不能用上一批识别结果替换本批失败。
        console.safe([&] {return Json{{"type","npu"},{"run_id",batch.run_id},{"status","error"},
            {"error",error.what()},{"backend",o.backend},{"npu_executed",false}};});
        if(o.save_results&&!error_path.empty()) try {write_json_atomic(error_path,{{"run_id",batch.run_id},{"status","error"},{"error",error.what()},{"success",false}});} catch(...) {}
        return 1;
    }
}
} // namespace
int main(int argc,char** argv) {
    using namespace pha_npu;
    try {
        auto o=arguments(argc,argv);
        // [上位机新增] 与 PS 共用主机/端口，但有独立 source、session、seq 和连接。
        aoa_console::Publisher console("npu");
        if(std::fesetround(FE_TONEAREST)) throw std::runtime_error("Cannot set rounding mode");
        std::signal(SIGINT,stop_signal);std::signal(SIGTERM,stop_signal);
        o.cancelled=[](){return stopped!=0;}; // [板端联调修正] 可取消设备锁等待。
        if(o.backend!="preprocess"&&(!fs::is_regular_file(o.model_json)||!fs::is_regular_file(o.model_raw)))
            throw std::runtime_error("Compiled model JSON/RAW does not exist; specify --model-json and --model-raw");
        if(!o.manifest.empty()) {
            const auto deadline=std::chrono::steady_clock::now()+std::chrono::duration<double>(o.wait_seconds);
            while(!stopped&&!fs::is_regular_file(o.manifest)) {
                if(std::chrono::steady_clock::now()>=deadline) throw std::runtime_error("Ready manifest not found");
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
            if(stopped) return 0;
            return run_job(o,load_batch(o),console);
        }
        pha_ipc::Consumer consumer(o.ipc_name);
        o.device_activity=[&](bool active){consumer.set_device_active(active);};
        std::cout<<"NPU_SERVICE ipc="<<o.ipc_name<<" save_results="<<o.save_results<<" backend="<<o.backend
                 <<" pid="<<::getpid()<<std::endl;
        std::uint64_t processed=0,errors=0;
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::duration<double>(o.wait_seconds);
        while(!stopped&&(!o.max_batches||processed<o.max_batches)) {
            bool acquired=false;
            try {
                std::string metadata;std::vector<std::uint8_t> raw;
                if(!consumer.receive(metadata,raw)) {
                    if(!processed&&o.wait_seconds>0&&std::chrono::steady_clock::now()>=deadline)
                        throw std::runtime_error("Timed out waiting for first IPC batch");
                    continue;
                }
                acquired=true;++processed;
                const auto batch=load_memory_batch(o,metadata,std::move(raw));
                std::cout<<"NPU_BUSY run_id="<<batch.run_id<<std::endl;
                const auto code=run_job(o,batch,console);if(code!=0&&code!=3) ++errors;
                consumer.finish(code==0||code==3);acquired=false;
                std::cout<<"NPU_IDLE processed="<<processed<<" errors="<<errors<<std::endl;
                if(o.once) return code;
            } catch(const pha_ipc::RejectedPayload& e) {
                // [部署核验修正] receive 已 finish(false)，此时 acquired 尚未置 true。
                // 原通用 catch 会将坏长度批次误判为服务故障并退出；这里计一次失败后继续。
                // 不重复 finish，避免把下一批 READY 清回 IDLE。未知 run_id 不进行猜测。
                ++processed;++errors;
                std::cerr<<"NPU_ERROR rejected_payload processed="<<processed<<" error="<<e.what()<<std::endl;
                console.safe([&] {return Json{{"type","service_error"},{"run_id",""},{"error",e.what()}};});
                if(o.once) return 1;
            } catch(const std::exception& e) {
                if(acquired) {consumer.set_device_active(false);consumer.finish(false);++errors;}
                else if(!stopped) throw;
                std::cerr<<"NPU_ERROR "<<e.what()<<std::endl;
                // [上位机新增] 输入校验阶段未知批次只报服务错误，不猜测 run_id。
                console.safe([&] {return Json{{"type","service_error"},{"run_id",""},{"error",e.what()}};});
                if(o.once) return 1;
            }
        }
        std::cout<<"NPU_STOP processed="<<processed<<" errors="<<errors<<std::endl;
        return 0;
    } catch(const std::exception& e) {std::cerr<<"ERROR: "<<e.what()<<std::endl;return 1;}
}
