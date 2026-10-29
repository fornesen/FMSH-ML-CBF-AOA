#include "console_transport.hpp"
#include <chrono>
#include <iostream>
int main(int argc,char**) {
    aoa_console::Publisher publisher("aoa");
    const bool stream=argc>1;
    const auto start=std::chrono::steady_clock::now();
    for(int i=0;i<(stream?100:20000);++i) {
        publisher.safe([&]{return aoa_console::Json{{"type","service_error"},{"run_id",""},{"error","probe"},{"index",i}};});
        if(stream)std::this_thread::sleep_for(std::chrono::milliseconds(30));
    }
    std::cout<<std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count()<<'\n';
}
