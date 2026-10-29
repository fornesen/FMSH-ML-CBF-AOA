// SDK-free batch replay for numerical validation of the actual C++ loader.
#include "pha_npu.hpp"
#include <algorithm>
#include <fstream>
#include <iostream>
#include <stdexcept>
using namespace pha_npu;
int main(int argc,char** argv) {
    try {
        if(argc!=3)throw std::runtime_error("Usage: dump_amplitude_features REQUESTS.jsonl NEW_OUTPUT_DIR");
        const fs::path output=fs::absolute(argv[2]);
        if(fs::exists(output))throw std::runtime_error("Preserve existing validation output; choose a new directory");
        fs::create_directories(output);std::ifstream requests(argv[1]);
        std::ofstream summaries(output/"features.jsonl");
        if(!requests||!summaries)throw std::runtime_error("Cannot open replay requests/output");
        std::string line;int count=0;
        while(std::getline(requests,line)) {
            const auto request=Json::parse(line);const auto name=request.at("name").get<std::string>();
            if(name.empty()||name.size()>128||!std::all_of(name.begin(),name.end(),[](unsigned char c){
                return (c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_'||c=='-';}))
                throw std::runtime_error("Invalid replay case name");
            Options options;options.amplitude_mode=request.value("amplitude_mode",std::string("batch"));
            const auto batch=load_memory_batch(options,request.at("manifest").dump(),
                                               read_bytes(request.at("raw_file").get<std::string>(),131072));
            const auto features=preprocess(batch);Json inputs=Json::array(),stats=Json::array();
            for(const auto& f:features) {
                const fs::path path=output/(name+"_"+f.name+".ftmp");
                if(fs::exists(path))throw std::runtime_error("Duplicate replay name");
                const auto rows=f.shape[2],columns=f.shape[3];std::vector<float> nhwc(f.f32.size());
                for(int group=0;group<4;++group)for(std::int64_t r=0;r<rows;++r)
                    for(std::int64_t c=0;c<columns;++c)
                        nhwc[(r*columns+c)*4+group]=f.f32[(group*rows+r)*columns+c];
                write_bytes_atomic(path,nhwc.data(),nhwc.size()*sizeof(float));inputs.push_back(path.string());
                stats.push_back({{"name",f.name},{"uint8_sha256",sha256(f.u8)},
                    {"zero_elements",std::count(f.u8.begin(),f.u8.end(),std::uint8_t(0))},
                    {"elements",f.u8.size()},{"maximum",*std::max_element(f.u8.begin(),f.u8.end())}});
            }
            summaries<<Json({{"name",name},{"inputs",inputs},{"features",stats},
                {"source_raw_sha256",batch.raw_sha256},{"amplitude_preprocessing",batch.amplitude_preprocessing}}).dump()<<'\n';
            if(!summaries)throw std::runtime_error("Cannot save replay metadata");
            ++count;
        }
        std::cout<<"COMPLETE feature batches="<<count<<" SDK/device not used\n";
        return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
