// Linux/board-only scheduling test. No PL, TCP, DMA or hardware access.
#include "archive_control.hpp"
#include "capture_schedule.hpp"
#include <cassert>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>

int main(int argc,char** argv) {
    using namespace aoa_app;
    if(argc!=2) return 2;
    const std::filesystem::path folder=argv[1];
    std::filesystem::create_directories(folder);
    setenv("AOA_ARCHIVE_SWITCH","1",1);
    setenv("AOA_ARCHIVE_BACKLOG","1",1);
    setenv("AOA_ARCHIVE_CONTROL_PATH",(folder/"control.json").c_str(),1);
    Options options;options.save_results=true;options.record_raw=true;
    ArchiveControl control;control.prepare(options,{},false,true);
    assert(!options.save_results && !options.record_raw);
    // A previous recording remains present and must not be removed by disabling.
    const auto metadata=folder/"aoa_123_1_metadata.json";
    std::ofstream(metadata)<<"{}\n";
    auto command=nlohmann::json{{"schema",1},{"mode","archive"},{"id","on"},{"archive_enabled",true}};
    control.prepare(options,command,true,true);
    assert(options.record_raw);
    unsigned polls=0;
    const auto started=std::chrono::steady_clock::now();
    const bool capacity=wait_for_archive_capacity((folder/"aoa").string(),[&] {
        if(++polls==3) {
            command["id"]="off";command["archive_enabled"]=false;
            control.prepare(options,command,true,true);
        }
        return !options.save_results;
    });
    const double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count();
    assert(!capacity && !options.record_raw && !options.save_results && polls==3);
    assert(elapsed<.5 && std::filesystem::exists(metadata));
    std::cout<<"PASS recording-off interrupts capacity wait without deleting old originals; seconds="<<elapsed<<'\n';
    std::filesystem::remove(metadata);std::filesystem::remove(folder/"control.json");
    std::filesystem::remove(folder);
}
