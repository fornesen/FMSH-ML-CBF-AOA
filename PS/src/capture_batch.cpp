#include "app.hpp"
#include "capture_pha.hpp"
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <unistd.h>

namespace aoa_app {
namespace {
void check_cancelled(const std::function<bool()>& cancelled) {
    if(cancelled && cancelled()) throw std::runtime_error("Capture batch interrupted");
}
void save_round_bytes(const Options& o,const CaptureState& round,const std::string& suffix,
                      const std::vector<std::uint8_t>& data,std::size_t count) {
    if(!o.save_results) return;
    save_bytes(round.prefix+suffix,data,count);
    // [板端存储调度优化] 每轮原始数据只写一次；兼容文件名用同目录硬链接指向该轮。
    // 原始字节、四轮记录和失败轮证据全部保留，不改采集/计算流程。先建临时链接再
    // 原子替换 canonical，不能直接 truncate 已链接文件，否则会破坏前一轮数据。
    if(round.prefix!=o.prefix) {
#ifdef _WIN32
        save_bytes(o.prefix+suffix,data,count);
#else
        const auto alias=o.prefix+suffix, temporary=alias+".linktmp";
        std::filesystem::remove(temporary);
        std::error_code link_error;
        std::filesystem::create_hard_link(round.prefix+suffix,temporary,link_error);
        // 不支持硬链接的结果目录仍可使用独立副本；先写临时文件，不能覆盖旧 inode。
        if(link_error) std::filesystem::copy_file(round.prefix+suffix,temporary);
        std::filesystem::rename(temporary,alias);
        // POSIX rename 在源/目标已同 inode 时不删除源名，复用失败轮前缀时需清理。
        std::filesystem::remove(temporary);
#endif
    }
}
void inspect_slots(const std::vector<std::uint8_t>& output,CaptureState& round,bool pha) {
    round.written_slots=0; round.pha_written_samples=0; round.pha_format_errors=0;
    if(output.size()<fft_bytes) throw std::runtime_error("Incomplete PL output buffer");
    for(std::size_t r=0;r<fft_records;++r) {
        const auto* slot=output.data()+r*slot_bytes;
        if(!slot_valid(slot)) continue;
        ++round.written_slots;
        if(pha && r<pha_samples) ++round.pha_written_samples;
        if(pha && r>=pha_samples &&
           !std::all_of(slot+pha_offset,slot+pha_offset+pha_sample_bytes,[](auto v){return v==0;}))
            ++round.pha_format_errors;
    }
    const bool valid=round.written_slots==fft_records && round.pha_format_errors==0;
    if(round.mode=="offline_fft") round.complete=valid;
    else round.complete=round.complete && valid && round.guard_checked && round.guard_ok && round.error.empty();
}
} // namespace

void initialize_batch(const Options& o,CaptureBatch& batch) {
    batch=CaptureBatch{};
    batch.run_id=std::to_string(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count())+"_"+std::to_string(::getpid());
    if(o.capture_rounds==npu_rounds) batch.pha.reserve(npu_rounds*pha_bytes);
    batch.rounds.reserve(o.capture_rounds);
    batch.fft_snapshots.reserve(o.capture_rounds);
    if(!o.save_results) return;
    const auto parent=std::filesystem::path(o.prefix).parent_path();
    if(!parent.empty()) std::filesystem::create_directories(parent);
    // Invalidate a previous handoff before touching any files for the new run.
    for(const auto* suffix:{"_npu_ready.json","_npu_ready.json.tmp","_pha4.raw","_pha4.raw.tmp",
                            "_pha4.csv","_pha4.csv.tmp"})
        std::filesystem::remove(o.prefix+suffix);
    for(std::size_t i=0;i<npu_rounds;++i)
        std::filesystem::remove(o.prefix+"_round"+std::to_string(i+1)+"_pha.raw");
}

void collect_batch(const Options& o,CaptureBatch& batch,CaptureState& final,
                   std::vector<std::uint8_t>& output,const std::function<bool()>& cancelled) {
    // An offline FFT file does not certify the separate PHA payload.
    const bool collect_pha=o.capture_rounds==npu_rounds && !o.dry_run && o.fft_sources.empty();
    for(std::size_t index=0;index<o.capture_rounds;++index) {
        check_cancelled(cancelled);
        batch.rounds.emplace_back(); auto& round=batch.rounds.back();
        round.mode=o.fft_raw.empty()?(o.dry_run?"adc_only":"hardware"):"offline_fft";
        round.prefix=o.capture_rounds==1?o.prefix:o.prefix+"_round"+std::to_string(index+1);
        if(!o.adc_sources.empty()) round.adc_source=o.adc_sources[index];
        if(!o.fft_sources.empty()) round.fft_source=o.fft_sources[index];
        const auto started=std::chrono::steady_clock::now(); output.clear();
        round.started_unix_ns=std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();
        auto finish=[&] {
            round.finished_unix_ns=std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            round.elapsed_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();
            final=round;
        };
        try {
            std::cout<<"Capture round "<<index+1<<'/'<<o.capture_rounds<<": "<<round.mode<<std::endl;
            if(!round.fft_source.empty()) output=load_exact(round.fft_source,fft_bytes);
            else {
                std::vector<std::uint8_t> adc;
                try {
                    if(!round.adc_source.empty()) adc=load_exact(round.adc_source,adc_bytes);
                    else {
                        auto tcp=o.tcp;
                        // [电脑激励联调新增] 仅显式开启时附带批次/帧号，模拟器据此保持
                        // 四帧识别期间位置不变。真实 7100 默认仍收到原来的五字节 START。
                        const auto* tagged=std::getenv("AOA_SIM_TAGGED_START");
                        if(tagged && std::string(tagged)=="1") {
                            const auto trigger="START "+std::to_string(o.capture_rounds)+" "+
                                std::to_string(index)+" "+batch.run_id+"\n";
                            tcp.trigger.assign(trigger.begin(),trigger.end());
                        }
                        tcp_receiver::receive_exact(tcp,adc_bytes,adc,cancelled);
                    }
                } catch(...) {
                    round.adc_received_bytes=adc.size();
                    save_round_bytes(o,round,"_adc.raw",adc,adc.size()); throw;
                }
                round.adc_received_bytes=adc.size(); save_round_bytes(o,round,"_adc.raw",adc,adc.size());
                const auto input=adc_to_slots(o,adc,&round.adc_dc_removed);
                if(o.processing_mode=="scan6" && o.scan_verify && index+1==o.capture_rounds) batch.scan_input=input;
                round.adc_dc_applied=o.adc_remove_dc;
                save_round_bytes(o,round,"_input_slots.raw",input,input.size());
                if(o.dry_run) {
                    finish(); export_metadata(o,final,Analysis{},batch); continue;
                }
                output.assign(fft_bytes+slot_bytes,0xa5);
                try { capture_fft(o,input,output,round,cancelled); }
                catch(const std::exception& error) { round.error=error.what(); }
            }
            save_round_bytes(o,round,"_fft_slots.raw",output,fft_bytes);
            inspect_slots(output,round,collect_pha);
            // Decode each independent FFT once, including startup/Hann handling.
            // PHA is extracted separately below and is never windowed by this step.
            auto decoded=decode_fft(o,output,round);
            if(!round.complete) throw std::runtime_error("Incomplete FFT round in batch");
            batch.fft_snapshots.push_back(std::move(decoded.corrected));
            if(collect_pha) {
                const auto frame=pha_frame(output,round);
                batch.pha.insert(batch.pha.end(),frame.begin(),frame.end()); round.pha_collected=true;
                if(o.save_results) {save_bytes(round.prefix+"_pha.raw",frame,frame.size());round.pha_saved=true;}
            }
            if(round.complete) ++batch.completed;
            finish(); export_metadata(o,final,Analysis{},batch);
        } catch(const std::exception& error) {
            round.error=error.what(); finish(); throw;
        }
    }
}
} // namespace aoa_app
