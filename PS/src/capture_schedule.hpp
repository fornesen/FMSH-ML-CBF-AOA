#pragma once
// [双模式新增] 仅识别模式在批次边界等待 IPC 消费完成；不改共享 ABI，也不持硬件锁。
#include "pha_ipc.hpp"
#include <cstdlib>
#include <chrono>
#include <filesystem>
#include <functional>
#include <iostream>
#include <thread>
namespace aoa_app {
// [板端保存调度新增] 独立于所有数值计算，仅在新批次开始前检查归档队列。
// wrapper 将输出置于板端 tmpfs；最多六批未归档数据。满队列等待，不丢帧，
// 不持 DMA 锁，SIGINT/SIGTERM 可退出。普通启动不设此环境变量，行为完全兼容。
inline bool wait_for_archive_capacity(const std::string& prefix,
                                      const std::function<bool()>& cancelled) {
    const auto* setting=std::getenv("AOA_ARCHIVE_BACKLOG");
    if(!setting || !*setting) return true;
    const std::string value(setting);std::size_t consumed=0;
    const auto maximum=std::stoul(value,&consumed);
    if(consumed!=value.size() || maximum<1 || maximum>6)
        throw std::runtime_error("Invalid board archive backlog limit");
    const auto* reserve_setting=std::getenv("AOA_ARCHIVE_MIN_FREE_MB");
    std::size_t reserve_mb=20;
    if(reserve_setting) {
        const std::string reserve(reserve_setting);std::size_t used=0;
        reserve_mb=std::stoul(reserve,&used);
        if(used!=reserve.size() || reserve_mb<20 || reserve_mb>256)
            throw std::runtime_error("Invalid archive free-space reserve");
    }
    const std::filesystem::path path(prefix);
    const auto parent=path.parent_path();
    const auto start=path.filename().string()+"_",end=std::string("_metadata.json");
    bool announced=false;
    while(!cancelled || !cancelled()) {
        std::size_t pending=0;
        for(const auto& entry:std::filesystem::directory_iterator(parent)) {
            const auto name=entry.path().filename().string();
            if(name.size()>=start.size()+end.size() && name.compare(0,start.size(),start)==0 &&
               name.compare(name.size()-end.size(),end.size(),end)==0) ++pending;
        }
        if(pending<maximum && std::filesystem::space(parent).available>=reserve_mb*1024*1024)
            return true;
        if(!announced) {
            std::clog<<"ARCHIVE_BACKPRESSURE pending_batches="<<pending<<std::endl;
            announced=true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    return false;
}
inline bool npu_job_pending(const std::string& name) {
    pha_ipc::Mapping m;m.fd=::shm_open(name.c_str(),O_RDWR|O_CLOEXEC,0600);
    if(m.fd<0)return false;
    if(::flock(m.fd,LOCK_SH|LOCK_NB)==0){::flock(m.fd,LOCK_UN);return false;}
    if(errno!=EWOULDBLOCK&&errno!=EAGAIN)throw std::runtime_error("Cannot inspect NPU ownership");
    struct stat st{};
    if(::fstat(m.fd,&st)||st.st_size!=static_cast<off_t>(sizeof(pha_ipc::Layout))||!m.map())
        throw std::runtime_error("Cannot inspect NPU IPC layout");
    const auto& h=*m.p;
    if(pha_ipc::load(h.signature)!=pha_ipc::magic||h.abi_version!=pha_ipc::version||h.layout_bytes!=sizeof(h))
        throw std::runtime_error("NPU IPC ABI mismatch");
    const auto state=pha_ipc::load(h.state);
    return pha_ipc::load(h.consumer_pid) && state!=pha_ipc::idle && state!=pha_ipc::offline;
}
}
