// Real Linux process integration. No Python, FPGA, NPU device or external server required.
#include "pha_ipc.hpp"
#include <nlohmann/json.hpp>
#include <algorithm>
#include <csignal>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <set>
#include <sstream>
#include <sys/wait.h>

namespace fs=std::filesystem;
using Json=nlohmann::json;
void check(bool ok,const std::string& what){if(!ok)throw std::runtime_error(what);}
std::string read_text(const fs::path& p){std::ifstream f(p);return {std::istreambuf_iterator<char>(f),{}};}
std::vector<std::uint8_t> read_raw(const fs::path& p){const auto s=read_text(p);return {s.begin(),s.end()};}
void save(const fs::path& p,const std::string& s){std::ofstream f(p,std::ios::binary);f<<s;check(bool(f),"Cannot save test evidence");}
void pause_ms(int n){std::this_thread::sleep_for(std::chrono::milliseconds(n));}
struct Process {
    pid_t pid=-1;int output=-1;bool exited=false;int status=0;std::string text;
    Process(const std::vector<std::string>& args,const fs::path& cwd) {
        int pipefd[2];check(::pipe(pipefd)==0,"pipe");pid=::fork();check(pid>=0,"fork");
        if(!pid) {
            ::close(pipefd[0]);::dup2(pipefd[1],STDOUT_FILENO);::dup2(pipefd[1],STDERR_FILENO);::close(pipefd[1]);
            ::chdir(cwd.c_str());std::vector<char*> a;for(const auto& s:args)a.push_back(const_cast<char*>(s.c_str()));a.push_back(nullptr);
            ::execvp(a[0],a.data());::_exit(127);
        }
        ::close(pipefd[1]);output=pipefd[0];::fcntl(output,F_SETFL,::fcntl(output,F_GETFL)|O_NONBLOCK);
    }
    ~Process(){if(!exited&&pid>0){::kill(pid,SIGCONT);::kill(pid,SIGKILL);::waitpid(pid,&status,0);}if(output>=0)::close(output);}
    void pump(){char b[8192];ssize_t n;while((n=::read(output,b,sizeof(b)))>0)text.append(b,b+n);}
    void until(const std::string& needle,int ms=5000){
        auto end=std::chrono::steady_clock::now()+std::chrono::milliseconds(ms);
        while(std::chrono::steady_clock::now()<end){pump();if(text.find(needle)!=std::string::npos)return;pause_ms(1);}
        throw std::runtime_error("Missing process output: "+needle+"\n"+text);
    }
    int wait(int ms=5000){
        auto end=std::chrono::steady_clock::now()+std::chrono::milliseconds(ms);
        while(std::chrono::steady_clock::now()<end){pump();if(::waitpid(pid,&status,WNOHANG)==pid){exited=true;pump();return WIFEXITED(status)?WEXITSTATUS(status):128+WTERMSIG(status);}pause_ms(1);}
        throw std::runtime_error("Process timeout (producer must never wait for consumer)\n"+text);
    }
};
std::vector<Json> results(const std::string& text) {
    std::vector<Json> v;std::istringstream in(text);std::string line;
    while(std::getline(in,line))if(line.rfind("NPU_RESULT ",0)==0)v.push_back(Json::parse(line.substr(11)));
    return v;
}
int main(int argc,char** argv){
    try {
        check(argc==5||argc==8,"Usage: ipc_service_tests PS_BINARY NPU_BINARY FRAME_DIR NEW_EVIDENCE_DIR [SDK_BINARY MODEL.json MODEL.raw]");
        const std::string ps=argv[1],npu=argv[2];const fs::path frames=argv[3],root=argv[4];
        check(!fs::exists(root),"Preserve existing evidence; select a new directory");fs::create_directories(root/"no_files");
        const auto work=root/"no_files";const std::string base="/pha_test_"+std::to_string(::getpid());
        std::vector<std::uint8_t> expected;
        for(int i=0;i<4;++i){const auto data=read_raw(frames/("frame"+std::to_string(i)+".raw"));check(data.size()==24576*64,"fixture slots");
            for(int k=0;k<4096;++k)expected.insert(expected.end(),data.begin()+k*64+16,data.begin()+k*64+24);}
        save(root/"expected_pha4.raw",{expected.begin(),expected.end()});
        Process digest({"sha256sum",(root/"expected_pha4.raw").string()},root);check(digest.wait()==0,"sha256sum");const auto sha=digest.text.substr(0,64);
        auto ps_args=[&](const std::string& name,int batches,bool persist=false){
            std::vector<std::string> a{ps,"--ipc-name",name,"--paths","1","--bins","4","--angle-start","-40","--angle-step","20","--angle-count","5"};
            for(int i=0;i<4;++i){a.push_back("--fft-raw");a.push_back((frames/("frame"+std::to_string(i)+".raw")).string());}
            if(batches==1)a.push_back("--once");else{a.push_back("--max-batches");a.push_back(std::to_string(batches));}
            if(persist){a.push_back("--save-results");a.push_back("--output-prefix");a.push_back((root/"saved_ps/run").string());}
            return a;
        };
        auto npu_args=[&](const std::string& name){return std::vector<std::string>{npu,"--backend","preprocess","--ipc-name",name};};
        Json checks=Json::array();
        {
            // [部署核验新增] 不兼容模型采样率须在采集/发布前被拒绝，不能打印 sent。
            auto args=ps_args(base+"_rate",1);args.insert(args.end(),{"--sample-rate","99999999"});
            Process rejected(args,work);
            check(rejected.wait()==2&&rejected.text.find("Current v6 NPU IPC requires")!=std::string::npos,
                  "incompatible NPU rate must fail configuration");
            check(rejected.text.find("PHA_IPC")==std::string::npos,"incompatible rate must not publish");
            save(root/"incompatible_rate.log",rejected.text);
            checks.push_back({{"case","incompatible_rate_rejected_before_capture"},{"passed",true}});
            auto offline=ps_args(base+"_rate_only",1,true);
            const auto prefix=root/"rate_only/run";
            offline.insert(offline.end(),{"--sample-rate","99999999","--no-ipc","--output-prefix",prefix.string()});
            Process aoa_only(offline,work);
            check(aoa_only.wait()==0,"AOA-only non-model sample rate remains usable");
            check(aoa_only.text.find("NPU_READY:")==std::string::npos&&!fs::exists(prefix.string()+"_npu_ready.json"),
                  "AOA-only non-model rate must not advertise NPU readiness");
            const auto rate_meta=Json::parse(read_text(prefix.string()+"_metadata.json"));
            check(rate_meta["npu_input_ready"]==false&&rate_meta["npu_handoff_status"]=="unsupported_sample_rate",
                  "AOA-only rate status explains unavailable NPU input");
            save(root/"aoa_only_rate.log",aoa_only.text);
            checks.push_back({{"case","AOA_only_non_model_rate_has_no_ready_manifest"},{"passed",true}});
        }
        {
            Process p(ps_args(base+"_absent",3),work);check(p.wait()==0,"absent consumer must not fail capture");
            check(p.text.find("skipped_absent=3")!=std::string::npos,"absent skip accounting");save(root/"absent.log",p.text);
            checks.push_back({{"case","consumer_absent_three_batches"},{"passed",true}});
        }
        {
            pha_ipc::Consumer stalled(base+"_busy");
            Process first(ps_args(base+"_busy",1),work);check(first.wait()==0,"first publication");
            std::string meta;std::vector<std::uint8_t> raw;
            for(int i=0;i<100&&!stalled.receive(meta,raw);++i){}
            check(raw==expected,"all four IQ frames preserve byte order, signed boundaries and zeros");
            auto j=Json::parse(meta);check(j["captures"][0]["pha_collected"]==true&&j["captures"][0]["pha_saved"]==false,"collected/saved flags distinct");
            fs::create_directories(root/"different_frames");
            for(int i=0;i<4;++i){auto d=read_raw(frames/("frame"+std::to_string(i)+".raw"));d[64*64+16]^=0x5a;
                save(root/"different_frames"/("frame"+std::to_string(i)+".raw"),{d.begin(),d.end()});}
            auto different=ps_args(base+"_busy",20);int frame=0;
            for(std::size_t i=0;i+1<different.size();++i)if(different[i]=="--fft-raw")different[i+1]=(root/"different_frames"/("frame"+std::to_string(frame++)+".raw")).string();
            const auto started=std::chrono::steady_clock::now();Process p(different,work);check(p.wait()==0,"busy consumer must never block capture");
            check(p.text.find("skipped_busy=20")!=std::string::npos,"busy skip accounting");check(raw==expected,"private snapshot remains immutable");
            pha_ipc::Mapping inspection;inspection.fd=::shm_open((base+"_busy").c_str(),O_RDWR,0600);check(inspection.fd>=0&&inspection.map(),"inspect shared buffer");
            check(std::equal(expected.begin(),expected.end(),inspection.p->raw),"busy shared payload must never be overwritten by different inputs");
            save(root/"busy_twenty_batches.log",p.text);stalled.finish(true);
            checks.push_back({{"case","busy_consumer_twenty_batches_no_wait"},{"passed",true},
                {"milliseconds",std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count()}});
        }
        {
            auto a=npu_args(base+"_resident");a.insert(a.end(),{"--max-batches","3"});Process worker(a,work);worker.until("NPU_SERVICE");
            Process duplicate(npu_args(base+"_resident"),work);check(duplicate.wait()==1,"duplicate consumer must reject");
            for(int i=1;i<=3;++i){Process p(ps_args(base+"_resident",1),work);check(p.wait()==0,"resident capture");worker.until("NPU_IDLE processed="+std::to_string(i));}
            check(worker.wait()==0,"three batch worker exit");const auto r=results(worker.text);check(r.size()==3,"resident worker processed three jobs");
            std::set<std::string> ids;for(const auto& j:r){check(j["source_raw_sha256"]==sha,"IPC snapshot SHA256");check(j["save_results"]==false&&j["npu_executed"]==false,"no files / no fake hardware execution");ids.insert(j["run_id"]);}
            check(ids.size()==3,"unique batch IDs");save(root/"resident.log",worker.text);
            checks.push_back({{"case","resident_three_batches_and_duplicate_rejection"},{"passed",true}});
        }
        {
            const auto name=base+"_restart";Process worker(npu_args(name),work);worker.until("NPU_SERVICE");
            Process p(ps_args(name,1),work);worker.until("NPU_BUSY");::kill(worker.pid,SIGSTOP);check(p.wait()==0,"capture publishes before stopping worker");
            // SIGSTOP is used only by this test to freeze a real consumer. If the
            // first job finished before the stop, READY on the next job still fills
            // the single slot; all subsequent publications must return immediately.
            Process busy_producer(ps_args(name,20),work);check(busy_producer.wait()==0,"SIGSTOP worker must not block producer");
            check(busy_producer.text.find("status=busy")!=std::string::npos,"paused worker causes skip");
            ::kill(worker.pid,SIGKILL);check(worker.wait()==128+SIGKILL,"worker crash");
            Process absent(ps_args(name,1),work);check(absent.wait()==0&&absent.text.find("status=absent")!=std::string::npos,"dead consumer detected despite stale shared memory");
            auto a=npu_args(name);a.push_back("--once");Process restarted(a,work);restarted.until("NPU_SERVICE");
            Process again(ps_args(name,1),work);check(again.wait()==0&&restarted.wait()==0,"crashed consumer restart");
            check(results(restarted.text).size()==1,"restart consumes new job");save(root/"stopped_worker_producer.log",busy_producer.text);save(root/"restart.log",restarted.text);
            checks.push_back({{"case","real_worker_stopped_killed_restarted"},{"passed",true}});
        }
        check(fs::is_empty(work),"default mode must not create any result, lock, raw, feature or SDK log files");
        checks.push_back({{"case","default_working_directory_stays_empty"},{"passed",true}});
        if(argc==8) {
            Process worker({argv[5],"--backend","host","--ipc-name",base+"_sdk","--max-batches","12",
                "--model-json",argv[6],"--model-raw",argv[7]},work);worker.until("NPU_SERVICE");
            for(int i=1;i<=12;++i) {Process p(ps_args(base+"_sdk",1),work);check(p.wait()==0,"SDK input capture");worker.until("NPU_IDLE processed="+std::to_string(i),30000);}
            check(worker.wait()==0,"SDK resident worker");save(root/"sdk_resident_no_files.log",worker.text);const auto r=results(worker.text);
            check(r.size()==12,"twelve resident SDK jobs completed");
            for(const auto& item:r)check(r[0]["classification"]["logits"]==item["classification"]["logits"],"repeated SDK jobs have identical logits");
            check(r[0]["classification"]["classes"]==Json::array({"FM","AM","CW","noise","OOK"})&&r[0]["classification"]["logits"].size()==5,"resident SDK five-class/OOK contract");
            check(r[0]["npu_executed"]==false&&r[0]["save_results"]==false,"SDK CPU reference is not NPU execution");
            check(fs::is_empty(work),"SDK inference default must not leave result, feature or log files");save(root/"sdk_resident_no_files.log",worker.text);
            checks.push_back({{"case","SDK_host_resident_twelve_inferences_without_file_output"},{"passed",true}});
        }
        {
            auto a=npu_args(base+"_save");a.insert(a.end(),{"--once","--save-results","--output-dir",(root/"saved_npu").string()});
            Process worker(a,work);worker.until("NPU_SERVICE");Process p(ps_args(base+"_save",1,true),work);
            check(p.wait()==0&&worker.wait()==0,"optional saved output");const auto r=results(worker.text);check(r.size()==1,"saved result");const auto id=r[0]["run_id"].get<std::string>();
            check(read_raw(root/"saved_npu"/(id+"_source_pha4.raw"))==expected,"saved source snapshot matches all input bytes");
            check(read_raw(root/"saved_ps/run_pha4.raw")==expected,"PS saved PHA matches IPC bytes");
            const auto meta=Json::parse(read_text(root/"saved_ps/run_metadata.json"));check(meta["save_results"]==true,"PS save flag");
            Process replay({npu,"--backend","preprocess","--manifest",(root/"saved_ps/run_npu_ready.json").string(),"--save-results","--output-dir",(root/"file_replay").string()},work);
            check(replay.wait()==0,"legacy file replay");
            for(const auto* role:{"tf","spectrum","sequence"})for(const auto* ext:{"u8","f32"}){
                const std::string file=id+"_"+role+"."+ext;check(read_raw(root/"saved_npu"/file)==read_raw(root/"file_replay"/file),"IPC/file feature equality: "+file);
            }
            save(root/"saved_consumer.log",worker.text);
            checks.push_back({{"case","optional_storage_and_byte_exact_file_feature_equivalence"},{"passed",true}});
            // Metadata failures must release BUSY and not poison the resident service.
            auto valid=Json::parse(read_text(root/"saved_ps/run_npu_ready.json"));valid["transport"]="shm";
            auto b=npu_args(base+"_invalid");b.insert(b.end(),{"--max-batches","2"});Process receiver(b,work);receiver.until("NPU_SERVICE");
            auto invalid=valid;invalid["sample_rate_hz"]=1;invalid["run_id"]="invalid_rate";
            check(pha_ipc::try_publish(base+"_invalid",invalid.dump(),expected)==pha_ipc::Publish::sent,"invalid fixture publish");receiver.until("NPU_ERROR");
            check(pha_ipc::try_publish(base+"_invalid",valid.dump(),expected)==pha_ipc::Publish::sent,"invalid job released slot");
            check(receiver.wait()==0&&results(receiver.text).size()==1,"service survives malformed metadata");save(root/"invalid_then_valid.log",receiver.text);
            checks.push_back({{"case","invalid_metadata_releases_slot_and_service_continues"},{"passed",true}});
            // [部署核验新增] 直接构造生产者不会主动发布的坏长度，检验 receive 抛错后的服务存活。
            // 之后由独立 PS 进程正常发布，覆盖“坏批次 -> 释放 -> 正常下一批”的完整路径。
            const auto broken_name=base+"_bad_lengths";
            auto length_args=npu_args(broken_name);length_args.insert(length_args.end(),{"--max-batches","2"});
            Process length_receiver(length_args,work);length_receiver.until("NPU_SERVICE");
            {
                pha_ipc::Mapping injection;injection.fd=::shm_open(broken_name.c_str(),O_RDWR,0600);
                check(injection.fd>=0&&injection.map(),"map malformed-length fixture");
                auto& h=*injection.p;
                check(pha_ipc::change(h.state,pha_ipc::idle,pha_ipc::writing),"claim malformed fixture slot");
                h.metadata_bytes=1;h.raw_bytes=1;h.metadata[0]='{';
                pha_ipc::store(h.state,pha_ipc::ready);::sem_post(&h.notification);
                length_receiver.until("NPU_ERROR rejected_payload");
                check(pha_ipc::load(h.state)==pha_ipc::idle,"bad length releases slot exactly once");
                check(__atomic_load_n(&h.failed,__ATOMIC_ACQUIRE)==1,"bad length counted exactly once");
            }
            Process valid_after_length(ps_args(broken_name,1),work);
            check(valid_after_length.wait()==0&&length_receiver.wait()==0,"bad length must not terminate resident service");
            check(results(length_receiver.text).size()==1,"normal inference follows rejected length");
            save(root/"invalid_length_then_valid.log",length_receiver.text);
            checks.push_back({{"case","invalid_length_releases_once_and_service_continues"},{"passed",true}});
        }
        {
            pha_ipc::Consumer consumer(base+"_recover");fs::create_directories(root/"recovery_fixture");
            const auto last=root/"recovery_fixture/last.raw";save(last,"short");
            auto a=ps_args(base+"_recover",2);a.insert(a.end(),{"--retry-delay-ms","100"});
            int frame=0;for(std::size_t i=0;i+1<a.size();++i)if(a[i]=="--fft-raw"&&frame++==3)a[i+1]=last.string();
            Process p(a,work);p.until("Error: Raw file");const auto data=read_raw(frames/"frame3.raw");save(last,{data.begin(),data.end()});
            check(p.wait()==0&&p.text.find("errors=1")!=std::string::npos&&p.text.find("forwarded=1")!=std::string::npos,"capture service recovers after an incomplete batch");
            std::string meta;std::vector<std::uint8_t> raw;for(int i=0;i<50&&!consumer.receive(meta,raw);++i){}
            check(raw==expected,"partial failed batch was never forwarded");consumer.finish(true);save(root/"capture_failure_recovery.log",p.text);
            checks.push_back({{"case","capture_failure_retries_and_only_publishes_complete_batch"},{"passed",true}});
        }
        {
            Process receiver(npu_args(base+"_term"),work);receiver.until("NPU_SERVICE");::kill(receiver.pid,SIGTERM);check(receiver.wait(1500)==0,"idle NPU graceful shutdown");
            auto a=ps_args(base+"_term",0);a.insert(a.end(),{"--interval-ms","10000"});Process producer(a,work);producer.until("FFT slots=");::kill(producer.pid,SIGTERM);check(producer.wait(1500)==0,"capture service graceful shutdown");
            checks.push_back({{"case","SIGTERM_stops_both_resident_services"},{"passed",true}});
        }
        Json report={{"passed",true},{"execution","Linux processes; offline FFT and CPU preprocessing; no board hardware"},{"checks",checks}};
        save(root/"report.json",report.dump(2)+"\n");std::cout<<report.dump(2)<<std::endl;return 0;
    }catch(const std::exception& e){std::cerr<<"INTEGRATION FAILURE: "<<e.what()<<std::endl;return 1;}
}
