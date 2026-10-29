#pragma once
#include "ipc_name.hpp"
// Shared ABI for the SPSC, drop-when-busy PHA handoff. Copy identically to both projects.
// Only RAM-backed POSIX shared memory is created. The producer never waits for a lock,
// semaphore, consumer startup or inference completion.
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <functional>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <fcntl.h>
#include <semaphore.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace pha_ipc {
// [部署核验修正] 已丢弃且已释放槽位的坏批次，与 IPC 初始化/等待失败分开处理。
// 只新增 C++ 异常类型；共享内存 Layout、版本及生产者协议均不变。
class RejectedPayload : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};
constexpr std::uint32_t magic=0x50484134,version=1;
constexpr std::size_t raw_capacity=4*4096*2*4,metadata_capacity=32768;
enum State : std::uint32_t { offline=0,idle=1,writing=2,ready=3,busy=4 };
struct alignas(64) Layout {
    std::uint32_t signature,abi_version,layout_bytes,state;
    std::uint32_t consumer_pid,metadata_bytes,raw_bytes,device_active;
    std::uint64_t accepted,completed,failed;
    sem_t notification;
    char metadata[metadata_capacity];
    std::uint8_t raw[raw_capacity];
};
static_assert(__atomic_always_lock_free(4,nullptr),"IPC requires lock-free aligned 32-bit atomics");
static_assert(__atomic_always_lock_free(8,nullptr),"IPC counters require lock-free 64-bit atomics");
inline std::uint32_t load(const std::uint32_t& x) {return __atomic_load_n(&x,__ATOMIC_ACQUIRE);}
inline void store(std::uint32_t& x,std::uint32_t v) {__atomic_store_n(&x,v,__ATOMIC_RELEASE);}
inline bool change(std::uint32_t& x,std::uint32_t from,std::uint32_t to) {
    return __atomic_compare_exchange_n(&x,&from,to,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);
}
inline bool publication_lock(int fd,bool take) {
    struct flock l{};l.l_type=take?F_WRLCK:F_UNLCK;l.l_whence=SEEK_SET;l.l_start=0;l.l_len=1;
    return ::fcntl(fd,F_SETLK,&l)==0;
}
struct Mapping {
    int fd=-1;Layout* p=nullptr;
    Mapping()=default;Mapping(const Mapping&)=delete;Mapping& operator=(const Mapping&)=delete;
    ~Mapping(){if(p)::munmap(p,sizeof(Layout));if(fd>=0)::close(fd);}
    bool map(){auto* v=::mmap(nullptr,sizeof(Layout),PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
        if(v==MAP_FAILED)return false;
        p=static_cast<Layout*>(v);return true;}
};
enum class Publish { sent,busy,absent,error };
inline const char* name(Publish p) {
    switch(p){case Publish::sent:return "sent";case Publish::busy:return "busy";
        case Publish::absent:return "absent";default:return "error";}
}
inline Publish try_publish(const std::string& object,const std::string& metadata,
                           const std::vector<std::uint8_t>& raw) noexcept {
    try {
        validate_name(object);
        if(metadata.empty()||metadata.size()>metadata_capacity||raw.size()!=raw_capacity)return Publish::error;
        Mapping m;m.fd=::shm_open(object.c_str(),O_RDWR|O_CLOEXEC,0600);
        if(m.fd<0)return errno==ENOENT?Publish::absent:Publish::error;
        // Only the consumer holds flock(EX). A successful shared lock means no consumer.
        if(::flock(m.fd,LOCK_SH|LOCK_NB)==0){::flock(m.fd,LOCK_UN);return Publish::absent;}
        if(errno!=EWOULDBLOCK&&errno!=EAGAIN)return Publish::error;
        struct stat s{};
        if(::fstat(m.fd,&s)||s.st_size!=static_cast<off_t>(sizeof(Layout))||!m.map())return Publish::error;
        if(!publication_lock(m.fd,true))return Publish::busy;
        auto& h=*m.p;
        if(load(h.signature)!=magic||h.abi_version!=version||h.layout_bytes!=sizeof(Layout))return Publish::error;
        if(!load(h.consumer_pid))return Publish::absent;
        if(!change(h.state,idle,writing))return Publish::busy;
        h.metadata_bytes=static_cast<std::uint32_t>(metadata.size());h.raw_bytes=static_cast<std::uint32_t>(raw.size());
        std::memcpy(h.metadata,metadata.data(),metadata.size());std::memcpy(h.raw,raw.data(),raw.size());
        // Payload and metadata are immutable from READY until finish().
        __atomic_add_fetch(&h.accepted,1,__ATOMIC_RELAXED);store(h.state,ready);
        // Notification failure does not block; the consumer also scans on each timeout.
        ::sem_post(&h.notification);
        return Publish::sent;
    } catch(...) {return Publish::error;}
}
class Consumer {
    Mapping m_;std::string name_;bool owns_=false;
public:
    explicit Consumer(const std::string& object):name_(object) {
        validate_name(object);m_.fd=::shm_open(object.c_str(),O_CREAT|O_RDWR|O_CLOEXEC,0600);
        if(m_.fd<0)throw std::runtime_error("Cannot open IPC shared memory");
        if(::flock(m_.fd,LOCK_EX|LOCK_NB))throw std::runtime_error("Another NPU consumer owns this IPC name");
        // Restart waits only in the consumer, never in the capture process. The short
        // publication lock prevents reset of a previous producer's in-flight memcpy.
        const auto end=std::chrono::steady_clock::now()+std::chrono::seconds(2);
        while(!publication_lock(m_.fd,true)) {
            if(std::chrono::steady_clock::now()>=end)throw std::runtime_error("Previous IPC writer has not finished");
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
        }
        struct stat st{};if(::fstat(m_.fd,&st))throw std::runtime_error("Cannot stat IPC object");
        const bool fresh=st.st_size==0;
        if(fresh&&::ftruncate(m_.fd,sizeof(Layout)))throw std::runtime_error("Cannot size IPC object");
        if(!fresh&&st.st_size!=static_cast<off_t>(sizeof(Layout)))throw std::runtime_error("IPC ABI size mismatch; use a new IPC name");
        if(!m_.map())throw std::runtime_error("Cannot map IPC object");
        auto& h=*m_.p;
        if(fresh) {
            std::memset(&h,0,sizeof(h));
            if(::sem_init(&h.notification,1,0))throw std::runtime_error("Cannot initialize process-shared semaphore");
            h.abi_version=version;h.layout_bytes=sizeof(Layout);store(h.signature,magic);
        } else if(load(h.signature)!=magic||h.abi_version!=version)
            throw std::runtime_error("IPC ABI mismatch; use a new IPC name");
        while(::sem_trywait(&h.notification)==0){}
        h.metadata_bytes=0;h.raw_bytes=0;store(h.device_active,0);store(h.consumer_pid,static_cast<std::uint32_t>(::getpid()));
        store(h.state,idle);owns_=true;publication_lock(m_.fd,false);
    }
    ~Consumer(){if(owns_){store(m_.p->device_active,0);store(m_.p->consumer_pid,0);store(m_.p->state,offline);::shm_unlink(name_.c_str());}}
    void set_device_active(bool value) noexcept {store(m_.p->device_active,value?1:0);}
    bool receive(std::string& metadata,std::vector<std::uint8_t>& raw) {
        auto& h=*m_.p;
        if(!change(h.state,ready,busy)) {
            struct timespec t{};::clock_gettime(CLOCK_REALTIME,&t);t.tv_nsec+=100000000;
            if(t.tv_nsec>=1000000000){++t.tv_sec;t.tv_nsec-=1000000000;}
            ::sem_timedwait(&h.notification,&t);return false;
        }
        ::sem_trywait(&h.notification); // Consume a pending hint; READY can precede sem_post.
        if(!h.metadata_bytes||h.metadata_bytes>metadata_capacity||h.raw_bytes!=raw_capacity) {
            finish(false);throw RejectedPayload("Invalid IPC payload lengths");
        }
        metadata.assign(h.metadata,h.metadata+h.metadata_bytes);raw.assign(h.raw,h.raw+h.raw_bytes);return true;
    }
    void finish(bool success) {
        auto& h=*m_.p;if(success)__atomic_add_fetch(&h.completed,1,__ATOMIC_RELAXED);
        else __atomic_add_fetch(&h.failed,1,__ATOMIC_RELAXED);
        store(h.state,idle);
    }
};
}
