#include "archive_control.hpp"
#include <cassert>
#include <chrono>
#include <filesystem>
#include <iostream>

namespace {
void setting(const char* key,const std::string& value) {
#ifdef _WIN32
    _putenv_s(key,value.c_str());
#else
    setenv(key,value.c_str(),1);
#endif
}
}
int main() {
    using namespace aoa_app;
    const auto folder=std::filesystem::current_path()/"build"/
        ("aoa_archive_control_"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    std::filesystem::create_directories(folder);
    setting("AOA_ARCHIVE_SWITCH","1");
    setting("AOA_ARCHIVE_CONTROL_PATH",(folder/"state.json").string());
    Options o;o.save_results=true;o.record_raw=true;o.observe_waveform=true;
    ArchiveControl control;
    control.prepare(o,{},false,true);
    assert(!o.save_results && !o.record_raw && o.observe_waveform);
    nlohmann::json state;std::ifstream(folder/"state.json")>>state;
    assert(state["enabled"]==false && control.state(true)["available"]==true);
    assert(control.state(true)["pending_batches"]==0);
    std::filesystem::create_directory(folder/"aoa");
    std::ofstream(folder/"aoa/aoa_1_1_metadata.json")<<"{}\n";
    assert(control.state(true)["pending_batches"]==1);
    auto command=nlohmann::json{{"schema",1},{"mode","archive"},{"id","enable"},{"archive_enabled",true}};
    control.prepare(o,command,true,true);
    assert(o.save_results && o.record_raw && o.observe_waveform);
    assert(control.state(true)["command_status"]=="applied");
    control.prepare(o,command,true,true);
    assert(o.record_raw && control.state(true)["command_status"]=="rejected");
    command["id"]="invalid";command["archive_enabled"]=1;
    control.prepare(o,command,true,true);
    assert(o.record_raw && control.state(true)["command_status"]=="rejected");
    command["id"]="off";command["archive_enabled"]=false;
    control.prepare(o,command,true,true);
    assert(!o.record_raw && !o.save_results && o.observe_waveform);
    assert(control.state(true)["pending_batches"]==1);
    std::filesystem::remove(folder/"aoa/aoa_1_1_metadata.json");
    assert(control.state(true)["pending_batches"]==0);
    assert(control.state(true)["command_status"]=="applied");
    ArchiveControl restarted;
    o.save_results=true;o.record_raw=true;
    restarted.prepare(o,{},false,true);
    assert(!o.record_raw && !o.save_results);
    command["id"]="unavailable";command["archive_enabled"]=true;
    restarted.prepare(o,command,true,false);
    assert(!o.record_raw && restarted.state(false)["command_status"]=="rejected");
    setting("AOA_ARCHIVE_SWITCH","");setting("AOA_ARCHIVE_CONTROL_PATH","");
    ArchiveControl legacy;o.save_results=true;o.record_raw=true;
    legacy.prepare(o,{},false,true);
    assert(o.record_raw && o.save_results && !legacy.state(true)["available"].get<bool>());
    std::filesystem::remove_all(folder);
    std::cout<<"PASS archive switch default-off, enable/disable ACK, restart, duplicate/type rejection and legacy CLI\n";
}
