#pragma once
#ifndef AOA_CONSOLE_TRANSPORT_HPP
#define AOA_CONSOLE_TRANSPORT_HPP
// [上位机新增] AOA1 遥测与有界控制收件箱。硬件操作只在采集线程执行。
// 此文件同步复制到两个板端工程的 console/ 目录，使原构建脚本仍可独立构建。
#include "json.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

namespace aoa_console {
using Json = nlohmann::json;
inline std::int64_t unix_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}
inline std::string base64(const std::vector<std::uint8_t>& v) {
    static const char* alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out; out.reserve((v.size()+2)/3*4);
    for(std::size_t i=0;i<v.size();i+=3) {
        const unsigned x=(unsigned(v[i])<<16)|(i+1<v.size()?unsigned(v[i+1])<<8:0)|(i+2<v.size()?v[i+2]:0);
        out+=alphabet[(x>>18)&63]; out+=alphabet[(x>>12)&63];
        out+=i+1<v.size()?alphabet[(x>>6)&63]:'='; out+=i+2<v.size()?alphabet[x&63]:'=';
    }
    return out;
}
class Publisher {
#ifdef _WIN32
    using Socket=SOCKET;
    static constexpr Socket invalid=INVALID_SOCKET;
#else
    using Socket=int;
    static constexpr Socket invalid=-1;
#endif
    std::string source_,session_;
    sockaddr_in address_{};
    bool enabled_=false;
    std::atomic<bool> stopping_{false};
    std::atomic<std::uint64_t> dropped_{0},sent_{0},failures_{0};
    std::uint64_t seq_=0;
    std::mutex mutex_;
    std::condition_variable cv_;
    std::deque<Json> queue_;
    Json command_, control_status_;
    std::chrono::steady_clock::time_point command_time_;
    std::string incoming_;
    std::atomic<bool> connected_{false};
    std::atomic<std::uint64_t> disconnect_epoch_{0};
    std::thread worker_;
    static void close_socket(Socket fd) {
#ifdef _WIN32
        closesocket(fd);
#else
        ::close(fd);
#endif
    }
    static bool writable(Socket fd,int milliseconds) {
#ifdef _WIN32
        fd_set set; FD_ZERO(&set); FD_SET(fd,&set);
        timeval tv{milliseconds/1000,(milliseconds%1000)*1000};
        return select(0,nullptr,&set,nullptr,&tv)>0;
#else
        pollfd p{fd,POLLOUT,0};
        return ::poll(&p,1,milliseconds)>0 && (p.revents&POLLOUT);
#endif
    }
    Socket connect_socket() {
        Socket fd=::socket(AF_INET,SOCK_STREAM,0);
        if(fd==invalid) return invalid;
#ifdef _WIN32
        u_long one=1; ioctlsocket(fd,FIONBIO,&one);
#else
        if(fcntl(fd,F_SETFL,O_NONBLOCK)<0) {close_socket(fd); return invalid;}
#endif
        const int result=::connect(fd,reinterpret_cast<sockaddr*>(&address_),sizeof(address_));
        if(result!=0 && !writable(fd,400)) {close_socket(fd); return invalid;}
        int error=0;
#ifdef _WIN32
        int length=sizeof(error);
        const auto status=getsockopt(fd,SOL_SOCKET,SO_ERROR,reinterpret_cast<char*>(&error),&length);
#else
        socklen_t length=sizeof(error);
        const auto status=getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&length);
#endif
        if(status!=0 || error!=0) {close_socket(fd); return invalid;}
        return fd;
    }
    bool transmit(Socket fd,const std::string& payload) {
        // [上位机新增] 固定魔数 + 网络序长度，避免 TCP 粘包；绝不发送超长数据。
        const auto n=static_cast<std::uint32_t>(payload.size());
        if(n==0 || n>1024*1024) return false;
        std::string frame="AOA1";
        for(int shift=24;shift>=0;shift-=8) frame.push_back(static_cast<char>((n>>shift)&255));
        frame+=payload;
        std::size_t offset=0;
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(500);
        while(offset<frame.size() && std::chrono::steady_clock::now()<deadline) {
            if(!writable(fd,40)) continue;
#ifdef _WIN32
            const auto count=::send(fd,frame.data()+offset,static_cast<int>(frame.size()-offset),0);
            if(count<0 && WSAGetLastError()==WSAEWOULDBLOCK) continue;
#else
            const auto count=::send(fd,frame.data()+offset,frame.size()-offset,MSG_NOSIGNAL);
            if(count<0 && (errno==EAGAIN || errno==EWOULDBLOCK || errno==EINTR)) continue;
#endif
            if(count<=0) return false;
            offset+=static_cast<std::size_t>(count);
        }
        return offset==frame.size();
    }
    // [波束控制新增] 增量解析反向 AOA1 帧；只接受当前进程会话的控制命令。
    // 每次最多读 8 KiB，断线清空，不重放跨连接旧命令；网络线程不碰 SDK。
    bool receive_commands(Socket fd) {
        char bytes[8192];
        const auto n=::recv(fd,bytes,sizeof(bytes),0);
        if(n==0) return false;
        if(n<0) {
#ifdef _WIN32
            return WSAGetLastError()==WSAEWOULDBLOCK;
#else
            return errno==EAGAIN || errno==EWOULDBLOCK || errno==EINTR;
#endif
        }
        incoming_.append(bytes,static_cast<std::size_t>(n));
        while(incoming_.size()>=8) {
            if(incoming_.compare(0,4,"AOA1")!=0) return false;
            std::uint32_t size=0;
            for(int i=4;i<8;++i) size=(size<<8)|static_cast<unsigned char>(incoming_[i]);
            if(!size || size>4096) return false;
            if(incoming_.size()<size+8) break;
            const auto p=Json::parse(incoming_.substr(8,size),nullptr,false);
            incoming_.erase(0,size+8);
            if(p.is_discarded() || !p.is_object() || p.value("type",std::string{})!="beam_command" ||
               p.value("session",std::string{})!=session_ || source_!="aoa") return false;
            std::lock_guard<std::mutex> lock(mutex_);
            if(!command_.is_null()) return false; // 禁止无界排队及覆盖未处理命令。
            command_=p; command_time_=std::chrono::steady_clock::now();
        }
        return true;
    }
    void disconnect(Socket& fd) {
        connected_=false;
        if(fd!=invalid) {++disconnect_epoch_;close_socket(fd);}
        fd=invalid; incoming_.clear();
        std::lock_guard<std::mutex> lock(mutex_); command_=nullptr;
    }
    void work() noexcept {
        Socket fd=invalid;
        auto heartbeat=std::chrono::steady_clock::now();
        // [上位机新增] 等待/锁/心跳分配也受异常隔离保护，不能从后台线程 terminate 主程序。
        try {
        while(true) {
            Json packet;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                cv_.wait_for(lock,std::chrono::milliseconds(40),[&]{return stopping_ || !queue_.empty();});
                if(stopping_ && queue_.empty()) break;
                if(queue_.empty() && std::chrono::steady_clock::now()>=heartbeat)
                    packet={{"type","heartbeat"},{"run_id",""}};
                else if(!queue_.empty()) {packet=std::move(queue_.front());queue_.pop_front();}
                // 空队列时仅轮询 socket，不制造额外遥测。
                if(!control_status_.is_null() && !packet.is_null() && !packet.contains("beam"))
                    packet["beam"]=control_status_;
            }
            try {
                if(fd!=invalid && !receive_commands(fd)) {++failures_;disconnect(fd);}
                if(packet.is_null()) continue;
                packet["schema"]=1; packet["source"]=source_; packet["session"]=session_;
                packet["seq"]=++seq_; packet["sent_unix_ms"]=unix_ms();
                packet["transport"]={{"sent",sent_.load()},{"dropped",dropped_.load()},{"failures",failures_.load()}};
                const auto payload=packet.dump();
                if(fd==invalid) {fd=connect_socket();connected_=fd!=invalid;}
                heartbeat=std::chrono::steady_clock::now()+std::chrono::seconds(2);
                if(fd!=invalid && transmit(fd,payload)) ++sent_;
                else {
                    ++failures_; ++dropped_;
                    disconnect(fd);
                    // [上位机新增] 断线时不重播过期批次；最多 2 条排队，后台限速重连。
                    std::unique_lock<std::mutex> lock(mutex_);
                    cv_.wait_for(lock,std::chrono::milliseconds(300),[&]{return stopping_.load();});
                }
            } catch(...) {++dropped_;++failures_;disconnect(fd);}
            if(stopping_) break;
        }
        } catch(...) {++dropped_;++failures_;}
        disconnect(fd);
    }
public:
    explicit Publisher(std::string source):source_(std::move(source)) {
        // [上位机新增] 未配置时完全关闭遥测；仅接收数字 IPv4，避免 DNS 阻塞采集。
        const char* host=std::getenv("AOA_CONSOLE_HOST");
        if(!host || !*host) return;
#ifdef _WIN32
        WSADATA data; if(WSAStartup(MAKEWORD(2,2),&data)!=0) return;
#endif
        address_.sin_family=AF_INET;
        const char* port=std::getenv("AOA_CONSOLE_PORT");
        char* end=nullptr; const long number=port?std::strtol(port,&end,10):9000;
        if(number<1 || number>65535 || (port && (!*port || *end)) || inet_pton(AF_INET,host,&address_.sin_addr)!=1) {
            std::cerr<<"CONSOLE disabled: invalid AOA_CONSOLE_HOST/PORT\n"; return;
        }
        address_.sin_port=htons(static_cast<unsigned short>(number));
        session_=source_+"_"+std::to_string(unix_ms())+"_"+std::to_string(
            std::chrono::steady_clock::now().time_since_epoch().count());
        try {worker_=std::thread([this]{work();});enabled_=true;}
        catch(...) {std::cerr<<"CONSOLE disabled: cannot start telemetry thread\n";}
    }
    ~Publisher() {
        // [上位机新增] 退出只尝试剩余最新一条；网络等待受 400/500ms 截止时间约束。
        {std::lock_guard<std::mutex> lock(mutex_);while(queue_.size()>1) {queue_.pop_front();++dropped_;}}
        stopping_=true;cv_.notify_all();if(worker_.joinable()) worker_.join();
    }
    bool enabled() const {return enabled_;}
    bool connected() const {return connected_;}
    std::uint64_t disconnect_epoch() const {return disconnect_epoch_;}
    void control_status(Json value) {
        std::lock_guard<std::mutex> lock(mutex_); control_status_=std::move(value);
    }
    bool take_command(Json& value) {
        std::lock_guard<std::mutex> lock(mutex_);
        if(command_.is_null()) return false;
        value=std::move(command_);command_=nullptr;
        if(!connected_ || std::chrono::steady_clock::now()-command_time_>std::chrono::seconds(15))
            value["expired"]=true;
        return true;
    }
    void publish(Json packet) noexcept {
        if(!enabled_) return;
        try {
            packet["produced_unix_ms"]=unix_ms();
            std::lock_guard<std::mutex> lock(mutex_);
            if(queue_.size()==2) {queue_.pop_front();++dropped_;}
            queue_.push_back(std::move(packet));cv_.notify_one();
        } catch(...) {++dropped_;}
    }
    // 包构造也可能失败，因此调用点统一通过 safe()，不让显示功能改变采集成败。
    template<class F> void safe(F make_packet) noexcept {
        if(!enabled_) return;
        try {publish(make_packet());} catch(...) {++dropped_;}
    }
};
} // namespace aoa_console
#endif
