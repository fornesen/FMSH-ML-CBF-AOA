#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

// A fixed-length TCP client, independent of Icraft and the ADC/DDR layout.
namespace tcp_receiver {

struct Options {
    std::string host = "192.168.10.50";
    std::uint16_t port = 7;
    std::vector<std::uint8_t> trigger = {'S', 'T', 'A', 'R', 'T'};
    double timeout_seconds = 10.0;
};

// Connect, send the trigger, then receive exactly byte_count bytes.
// Each phase has a deadline. On error, payload retains the received prefix;
// callers must not process that prefix as a complete frame.
void receive_exact(const Options& options, std::size_t byte_count,
                   std::vector<std::uint8_t>& payload,
                   const std::function<bool()>& cancelled = {});

}  // namespace tcp_receiver
