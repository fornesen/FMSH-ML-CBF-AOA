// Loopback-only server for synthetic integration tests. No FPGA is accessed.
#include <algorithm>
#include <chrono>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <thread>
#include <vector>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>

int main(int argc, char** argv) {
    if (argc != 4 && argc != 5) return 2;
    const int rounds=argc==5?std::stoi(argv[4]):1;
    if(rounds!=1 && rounds!=4) return 2;
    std::ifstream input(argv[1], std::ios::binary);
    if (!input) return 2;
    std::vector<char> payload((std::istreambuf_iterator<char>(input)), {});
    const std::string hex = argv[2], mode = argv[3];
    std::vector<char> trigger;
    for (std::size_t i = 0; i < hex.size(); i += 2)
        trigger.push_back(static_cast<char>(std::stoul(hex.substr(i, 2), nullptr, 16)));
    const int listener = ::socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (listener < 0 || ::bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) ||
        ::listen(listener, 1)) return 2;
    socklen_t size = sizeof(address);
    if (::getsockname(listener, reinterpret_cast<sockaddr*>(&address), &size)) return 2;
    std::cout << ntohs(address.sin_port) << std::endl;
    const auto full_payload=payload;
    const bool failing=mode=="short" || mode=="stall" || mode=="short-last";
    // A failed final frame is retried on two fresh connections by receive_exact.
    for(int connection=0;connection<rounds+(failing?2:0);++connection) {
    const int round=std::min(connection,rounds-1);
    payload=full_payload;
    if(rounds==4) {
        constexpr std::size_t frame=196608;
        if(full_payload.size()!=4*frame) return 2;
        payload.assign(full_payload.begin()+round*frame,full_payload.begin()+(round+1)*frame);
    }
    const int client = ::accept(listener, nullptr, nullptr);
    if (client < 0) return 2;
    std::vector<char> received(trigger.size());
    std::size_t offset = 0;
    while (offset < received.size()) {
        const auto count = ::recv(client, received.data()+offset, received.size()-offset, 0);
        if (count <= 0) return 3;
        offset += static_cast<std::size_t>(count);
    }
    if (received != trigger) return 4;
    if (mode == "short" || mode == "stall" || (mode=="short-last" && round==rounds-1)) payload.resize(37);
    else payload.insert(payload.end(), 12, '\x5a');  // Bytes beyond one fixed frame.
    const std::size_t fragments[] = {1, 2, 11, 13, 517, 4093, 32768};
    offset = 0;
    std::size_t fragment = 0;
    while (offset < payload.size()) {
        const auto length = std::min(fragments[fragment++ % 7], payload.size()-offset);
        const auto count = ::send(client, payload.data()+offset, length, MSG_NOSIGNAL);
        if (count <= 0) break;  // Client may close after the fixed frame.
        offset += static_cast<std::size_t>(count);
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (mode == "stall") std::this_thread::sleep_for(std::chrono::milliseconds(400));
    ::close(client);
    }
    ::close(listener);
    return 0;
}
