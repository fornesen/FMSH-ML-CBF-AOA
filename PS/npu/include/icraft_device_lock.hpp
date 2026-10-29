#pragma once
// [板端联调修正] AOA capture and NPU share the ZG330 DMA/DDR device.
// IPC BUSY is only a payload state; it cannot protect SDK device operations.
// Both executables must use this identical advisory lock before Device::Open
// and retain it until all device buffers/sessions and Device::Close finish.
#include <cerrno>
#include <chrono>
#include <cstring>
#include <fcntl.h>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <sys/file.h>
#include <thread>
#include <unistd.h>

namespace icraft_device {
class HardwareLock {
    int fd_=-1;
public:
    explicit HardwareLock(const std::function<bool()>& cancelled={}) {
        constexpr const char* path="/dev/shm/icraft_zg330_hardware.lock";
        fd_=::open(path,O_CREAT|O_RDWR|O_CLOEXEC,0660);
        if(fd_<0) throw std::runtime_error(std::string("Cannot open hardware lock: ")+std::strerror(errno));
        const auto start=std::chrono::steady_clock::now();
        try {
            for(;;) {
                if(cancelled && cancelled()) throw std::runtime_error("Hardware lock wait cancelled");
                if(::flock(fd_,LOCK_EX|LOCK_NB)==0) break;
                const int error=errno;
                if(error!=EWOULDBLOCK && error!=EAGAIN && error!=EINTR)
                    throw std::runtime_error(std::string("Cannot acquire hardware lock: ")+std::strerror(error));
                if(std::chrono::steady_clock::now()-start>std::chrono::seconds(120))
                    throw std::runtime_error("Hardware lock wait timed out after 120s");
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            }
            const auto ms=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-start).count();
            std::clog<<"HARDWARE_LOCK acquired pid="<<::getpid()<<" wait_ms="<<ms<<std::endl;
        } catch(...) {::close(fd_);fd_=-1;throw;}
    }
    ~HardwareLock() {if(fd_>=0) {::flock(fd_,LOCK_UN);::close(fd_);}}
    HardwareLock(const HardwareLock&)=delete;
    HardwareLock& operator=(const HardwareLock&)=delete;
};
}
