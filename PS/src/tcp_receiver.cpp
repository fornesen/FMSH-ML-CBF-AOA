#include "tcp_receiver.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <memory>
#include <iostream>
#include <stdexcept>
#include <thread>

#include <fcntl.h>
#include <netdb.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

namespace tcp_receiver {
namespace {
using Clock = std::chrono::steady_clock;
using Deadline = Clock::time_point;

struct Socket {
    int fd = -1;
    ~Socket() { if (fd >= 0) ::close(fd); }
    void reset(int next = -1) {
        if (fd >= 0) ::close(fd);
        fd = next;
    }
};

Deadline deadline_after(double seconds) {
    return Clock::now() + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(seconds));
}

void check_cancelled(const std::function<bool()>& cancelled) {
    if (cancelled && cancelled()) throw std::runtime_error("TCP capture interrupted");
}

void wait_socket(int fd, short events, Deadline deadline,
                 const std::function<bool()>& cancelled) {
    for (;;) {
        check_cancelled(cancelled);
        const auto remaining = std::chrono::duration<double, std::milli>(deadline - Clock::now()).count();
        if (remaining <= 0) throw std::runtime_error("TCP operation timed out");
        // Recheck cancellation at least every 100 ms, including when idle.
        const int timeout_ms = static_cast<int>(std::ceil(std::min(remaining, 100.0)));
        pollfd event{fd, events, 0};
        const int result = ::poll(&event, 1, timeout_ms);
        if (result < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error(std::string("TCP poll failed: ") + std::strerror(errno));
        }
        if (result == 0) continue;
        if (event.revents & POLLNVAL) throw std::runtime_error("TCP socket is invalid");
        // Read any remaining data on HUP; recv() reports EOF afterwards.
        if (event.revents & (events | POLLERR | POLLHUP)) return;
    }
}

void connect_socket(Socket& socket, const Options& options,
                    const std::function<bool()>& cancelled) {
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo* raw = nullptr;
    const auto service = std::to_string(options.port);
    const int result = ::getaddrinfo(options.host.c_str(), service.c_str(), &hints, &raw);
    if (result != 0)
        throw std::runtime_error(std::string("TCP address lookup failed: ") + ::gai_strerror(result));
    std::unique_ptr<addrinfo, decltype(&::freeaddrinfo)> addresses(raw, &::freeaddrinfo);
    const auto deadline = deadline_after(options.timeout_seconds);
    std::string last_error = "No usable TCP address";
    for (auto address = addresses.get(); address; address = address->ai_next) {
        check_cancelled(cancelled);
        socket.reset(::socket(address->ai_family, address->ai_socktype, address->ai_protocol));
        if (socket.fd < 0) { last_error = std::strerror(errno); continue; }
        const int flags = ::fcntl(socket.fd, F_GETFL, 0);
        if (flags < 0 || ::fcntl(socket.fd, F_SETFL, flags | O_NONBLOCK) < 0 ||
            ::fcntl(socket.fd, F_SETFD, FD_CLOEXEC) < 0)
            throw std::runtime_error(std::string("TCP socket setup failed: ") + std::strerror(errno));
        if (::connect(socket.fd, address->ai_addr, address->ai_addrlen) == 0) return;
        if (errno != EINPROGRESS && errno != EINTR) { last_error = std::strerror(errno); continue; }
        wait_socket(socket.fd, POLLOUT, deadline, cancelled);
        int error = 0;
        socklen_t size = sizeof(error);
        if (::getsockopt(socket.fd, SOL_SOCKET, SO_ERROR, &error, &size) < 0)
            throw std::runtime_error(std::string("TCP connect status failed: ") + std::strerror(errno));
        if (error == 0) return;
        last_error = std::strerror(error);
    }
    throw std::runtime_error("TCP connect to " + options.host + ":" + service + " failed: " + last_error);
}
}  // namespace

static void receive_once(const Options& options, std::size_t byte_count,
                   std::vector<std::uint8_t>& payload,
                   const std::function<bool()>& cancelled) {
    payload.clear();
    if (options.host.empty() || options.port == 0 || options.trigger.empty() || byte_count == 0 ||
        !std::isfinite(options.timeout_seconds) || options.timeout_seconds <= 0)
        throw std::invalid_argument("Invalid TCP host, port, trigger, byte count, or timeout");
    payload.reserve(byte_count);
    try {
        Socket socket;
        connect_socket(socket, options, cancelled);
        const auto send_deadline = deadline_after(options.timeout_seconds);
        std::size_t sent = 0;
        while (sent < options.trigger.size()) {
            wait_socket(socket.fd, POLLOUT, send_deadline, cancelled);
            const auto count = ::send(socket.fd, options.trigger.data() + sent,
                                      options.trigger.size() - sent, MSG_NOSIGNAL);
            if (count < 0) {
                if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) continue;
                throw std::runtime_error(std::string("TCP trigger send failed: ") + std::strerror(errno));
            }
            if (count == 0) throw std::runtime_error("TCP trigger send made no progress");
            sent += static_cast<std::size_t>(count);
        }
        const auto receive_deadline = deadline_after(options.timeout_seconds);
        std::array<std::uint8_t, 65536> buffer{};
        while (payload.size() < byte_count) {
            wait_socket(socket.fd, POLLIN, receive_deadline, cancelled);
            const auto count = ::recv(socket.fd, buffer.data(),
                                      std::min(buffer.size(), byte_count - payload.size()), 0);
            if (count < 0) {
                if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) continue;
                throw std::runtime_error(std::string("TCP receive failed: ") + std::strerror(errno));
            }
            if (count == 0) throw std::runtime_error("TCP peer closed before the frame was complete");
            payload.insert(payload.end(), buffer.begin(), buffer.begin() + count);
        }
    } catch (const std::exception& error) {
        throw std::runtime_error(std::string(error.what()) + "; received " +
                                 std::to_string(payload.size()) + "/" + std::to_string(byte_count) + " bytes");
    }
}

void receive_exact(const Options& options,std::size_t byte_count,
                   std::vector<std::uint8_t>& payload,const std::function<bool()>& cancelled) {
    // [7100实采通信修正] 尾包超时/断线只重试整个新帧，原连接已由RAII关闭。
    // 不拼接残帧，不重放旧帧；三次仍失败则保留最后残帧并走原错误/归档流程。
    for(unsigned attempt=0;attempt<3;++attempt) {
        check_cancelled(cancelled);
        try {receive_once(options,byte_count,payload,cancelled);return;}
        catch(const std::invalid_argument&) {throw;}
        catch(const std::exception& error) {
            check_cancelled(cancelled);
            if(attempt==2)throw;
            std::cerr<<"TCP_RETRY attempt="<<attempt+1<<" discarded_bytes="<<payload.size()
                     <<" reason="<<error.what()<<'\n';
            payload.clear();
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    }
}

}  // namespace tcp_receiver
